"""Resume loading, LaTeX cleanup, and cached embedding helpers."""

from __future__ import annotations

import re
from pathlib import Path

from openai import OpenAI

from jobsearch.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESUME_PATH = PROJECT_ROOT / "data" / "resume.tex"

_BODY_BEGIN_RE = re.compile(r"\\begin\{document\}")
_BODY_END_RE = re.compile(r"\\end\{document\}")
_DOCUMENTCLASS_RE = re.compile(r"(?m)^\s*\\documentclass(?:\[[^\]]*\])?\{[^{}]*\}\s*$")
_USEPACKAGE_RE = re.compile(r"(?m)^\s*\\usepackage(?:\[[^\]]*\])?\{[^{}]*\}\s*$")
_SECTION_RE = re.compile(r"\\(?:subsection|section)\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
_COMMAND_WITH_ARGS_RE = re.compile(
    r"\\([a-zA-Z@]+)\*?(?:\[[^\]]*\])?((?:\s*\{[^{}]*\})+)(?!\s*\{)"
)
_COMMENT_LINE_RE = re.compile(r"(?m)^\s*%.*$")
_BEGIN_END_RE = re.compile(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?")
_ESCAPED_CHAR_RE = re.compile(r"\\([#$%&_{}])")
_LINEBREAK_RE = re.compile(r"\\\\")
_BARE_COMMAND_RE = re.compile(r"\\[a-zA-Z@]+\*?")
_SPACE_RE = re.compile(r"[^\S\n]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

_DROP_ARGUMENT_COMMANDS = {
    "color",
    "hfill",
    "hspace",
    "phantom",
    "quad",
    "qquad",
    "vfill",
    "vspace",
}
_KEEP_LAST_ARGUMENT_COMMANDS = {
    "emph",
    "href",
    "item",
    "resumeItem",
    "resumeSubItem",
    "textbf",
    "textit",
    "underline",
}
_KEEP_ALL_ARGUMENT_COMMANDS = {
    "resumeProjectHeading",
    "resumeSubSubheading",
    "resumeSubheading",
}

_RESUME_TEXT: str | None = None
_RESUME_EMBEDDING: list[float] | None = None
_OPENAI_CLIENT: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    """Return a cached OpenAI client for embedding requests."""

    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Resume embeddings require it.")

    _OPENAI_CLIENT = OpenAI(api_key=settings.openai_api_key)
    return _OPENAI_CLIENT


def _replace_command_sequence(match: re.Match[str]) -> str:
    """Strip a LaTeX command while preserving useful text arguments."""

    command_name = match.group(1)
    arguments = re.findall(r"\{([^{}]*)\}", match.group(2))
    if not arguments:
        return " "

    if command_name in _DROP_ARGUMENT_COMMANDS:
        return " "
    if command_name == "href":
        return arguments[-1]
    if command_name in _KEEP_ALL_ARGUMENT_COMMANDS:
        return "\n" + "\n".join(argument.strip() for argument in arguments if argument.strip()) + "\n"
    if command_name in _KEEP_LAST_ARGUMENT_COMMANDS:
        return arguments[-1]
    return " ".join(argument.strip() for argument in arguments if argument.strip())


def _clean_resume_text(raw_text: str) -> str:
    """Convert a LaTeX resume into whitespace-normalized plain text."""

    text = _DOCUMENTCLASS_RE.sub("", raw_text)
    text = _USEPACKAGE_RE.sub("", text)

    begin_match = _BODY_BEGIN_RE.search(text)
    if begin_match is not None:
        text = text[begin_match.end() :]

    end_match = _BODY_END_RE.search(text)
    if end_match is not None:
        text = text[: end_match.start()]

    text = _COMMENT_LINE_RE.sub("", text)
    text = _BEGIN_END_RE.sub("\n", text)
    text = _SECTION_RE.sub(lambda match: f"\n\n{match.group(1).strip()}\n", text)

    previous = None
    while text != previous:
        previous = text
        text = _COMMAND_WITH_ARGS_RE.sub(_replace_command_sequence, text)

    text = _BARE_COMMAND_RE.sub(" ", text)
    text = _ESCAPED_CHAR_RE.sub(r"\1", text)
    text = text.replace("$", " ")
    text = text.replace("~", " ")
    text = text.replace("&", " ")
    text = _LINEBREAK_RE.sub("\n", text)
    text = text.replace("{", " ").replace("}", " ")
    text = text.replace("[", " ").replace("]", " ")
    text = text.replace("|", " | ")
    text = _SPACE_RE.sub(" ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def load_resume_text() -> str:
    """Load and cache the cleaned resume text from `data/resume.tex`."""

    global _RESUME_TEXT
    if _RESUME_TEXT is not None:
        return _RESUME_TEXT

    if not RESUME_PATH.exists():
        raise FileNotFoundError(
            f"Resume source not found at {RESUME_PATH}. Expected a LaTeX resume at data/resume.tex."
        )

    _RESUME_TEXT = _clean_resume_text(RESUME_PATH.read_text(encoding="utf-8"))
    return _RESUME_TEXT


def get_resume_embedding() -> list[float]:
    """Return the cached embedding vector for the cleaned resume text."""

    global _RESUME_EMBEDDING
    if _RESUME_EMBEDDING is not None:
        return _RESUME_EMBEDDING

    settings = get_settings()
    response = _get_openai_client().embeddings.create(
        model=settings.embedding_model,
        input=load_resume_text(),
    )
    _RESUME_EMBEDDING = list(response.data[0].embedding)
    return _RESUME_EMBEDDING
