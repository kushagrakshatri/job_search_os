"""Direct Codex backend client for score-generation requests."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from jobsearch.config import get_settings

AUTH_PATH = Path.home() / ".codex" / "auth.json"
TOKEN_REFRESH_WINDOW_MS = 5 * 60 * 1000
CODEX_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_INSTRUCTIONS = (
    "You are Codex, based on GPT-5. You are running as a coding agent in the "
    "Codex CLI on a user's computer."
)


def get_access_token() -> str:
    """Load a valid Codex bearer token from the local auth file."""

    try:
        auth = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Codex auth file not found at {AUTH_PATH}. Run `codex` once to sign in."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Codex auth file at {AUTH_PATH} is invalid JSON. Run `codex` once to refresh the token."
        ) from exc

    token, expires_ms = _extract_token_and_expiry(auth)
    if not token or not expires_ms:
        raise RuntimeError(
            f"Codex access token not found in {AUTH_PATH}. Run `codex` once to refresh the token."
        )

    now_ms = int(time.time() * 1000)
    if expires_ms < now_ms + TOKEN_REFRESH_WINDOW_MS:
        raise RuntimeError(
            "Codex access token is missing or expires within 5 minutes. "
            "Run `codex` once to refresh the token."
        )

    return token


def complete(system_prompt: str, user_message: str, model: str | None = None) -> str:
    """Send a completion request to the Codex backend and return message text."""

    token = get_access_token()
    resolved_model = model or get_settings().SCORER_MODEL
    payload = {
        "model": resolved_model,
        "instructions": CODEX_INSTRUCTIONS,
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_message}],
            },
        ],
        "tools": [],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
        # The live Codex backend currently rejects stream=false.
        "stream": True,
    }
    request = urllib.request.Request(
        CODEX_BACKEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return _read_streamed_response(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Codex backend request failed with status {exc.code}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Codex backend request failed: {exc}") from exc


def _extract_token_and_expiry(auth: dict[str, Any]) -> tuple[str | None, int | None]:
    """Extract bearer token and expiry from either documented or live auth shapes."""

    codex_auth = auth.get("openai-codex")
    if isinstance(codex_auth, dict):
        token = codex_auth.get("access")
        expires_ms = _coerce_expires_to_ms(codex_auth.get("expires"))
        if token and expires_ms:
            return str(token), expires_ms

    tokens = auth.get("tokens")
    if isinstance(tokens, dict):
        token = tokens.get("access_token")
        if token:
            return str(token), _decode_jwt_exp_to_ms(str(token))

    return None, None


def _coerce_expires_to_ms(value: Any) -> int | None:
    """Normalize common expiry formats into epoch milliseconds."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = int(value)
        return numeric if numeric > 10**12 else numeric * 1000
    if isinstance(value, str) and value.isdigit():
        numeric = int(value)
        return numeric if numeric > 10**12 else numeric * 1000
    return None


def _decode_jwt_exp_to_ms(token: str) -> int | None:
    """Decode the JWT exp claim without verifying the signature."""

    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded.decode("utf-8"))
        exp = claims.get("exp")
        return _coerce_expires_to_ms(exp)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _read_streamed_response(response: Any) -> str:
    """Parse the streaming Codex response and return assistant text."""

    current_event: str | None = None
    data_lines: list[str] = []
    last_message_text: str | None = None

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
            continue

        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
            continue

        if line:
            continue

        if not data_lines:
            current_event = None
            continue

        data = "\n".join(data_lines)
        if data == "[DONE]":
            break

        parsed = json.loads(data)
        if current_event == "response.output_item.done":
            item = parsed.get("item", {})
            text = _extract_message_text([item])
            if text:
                last_message_text = text
        elif current_event == "response.completed":
            output = parsed.get("response", {}).get("output", [])
            text = _extract_message_text(output)
            if text:
                return text

        current_event = None
        data_lines = []

    if last_message_text:
        return last_message_text

    raise RuntimeError("Codex backend response did not contain assistant message text.")


def _extract_message_text(output_items: list[dict[str, Any]]) -> str:
    """Extract assistant text from a Responses API output array."""

    parts: list[str] = []
    for item in output_items:
        if item.get("type") != "message":
            continue
        for content_item in item.get("content", []):
            content_type = content_item.get("type")
            if content_type in {"output_text", "text"}:
                text = content_item.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)
