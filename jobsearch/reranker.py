"""Embedding-first reranking pipeline for new job rows."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select

from jobsearch.codex_client import complete
from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.embeddings import embed_jobs_batch, get_adaptive_shortlist
from jobsearch.models import Job
from jobsearch.prompts import RERANKER_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

RERANK_BATCH_SIZE = 10

_SCORE_LIMITS: dict[str, int] = {
    "tech_stack": 35,
    "interviewability": 35,
    "work_auth": 20,
    "role_fit": 10,
}
_JSON_STRING_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _extract_json_object_text(text: str, job_id: str) -> str | None:
    """Normalize model output and isolate the outermost JSON object."""

    cleaned = "\n".join(
        line for line in text.strip().splitlines() if not line.lstrip().startswith("```")
    ).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end < start:
        logger.warning(
            "rerank_job_missing_json_object",
            job_id=job_id,
            raw_response_excerpt=cleaned[:200],
        )
        return None

    return cleaned[start : end + 1]


def _extract_json_array_text(text: str, batch_job_ids: list[str]) -> str | None:
    """Normalize model output and isolate the outermost JSON array."""

    cleaned = "\n".join(
        line for line in text.strip().splitlines() if not line.lstrip().startswith("```")
    ).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")

    if start == -1 or end == -1 or end < start:
        logger.warning(
            "rerank_batch_missing_json_array",
            job_ids=batch_job_ids,
            raw_response_excerpt=cleaned[:200],
        )
        return None

    return cleaned[start : end + 1]


def _build_user_message(job: Job, similarity_score: float) -> str:
    """Build the reranker user message for a single job."""

    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Remote: {job.is_remote}\n"
        f"Embedding similarity to candidate resume: {similarity_score:.3f}\n\n"
        f"Job Description:\n{job.jd_text[:3000]}"
    )


def _sanitize_job_text_for_prompt(text: str, limit: int = 1500) -> str:
    """Normalize JD text for prompt insertion before truncation."""

    sanitized = text.replace("\n", " ").replace("\t", " ")
    sanitized = "".join(
        character for character in sanitized if character == " " or ord(character) >= 32
    )
    return sanitized[:limit]


def _build_batch_user_message(batch: list[tuple[Job, float]]) -> str:
    """Build the reranker user message for one batch of jobs."""

    job_blocks = []
    for job_index, (job, _) in enumerate(batch):
        job_blocks.append(
            (
                f"[{job_index}] Title: {job.title} | Company: {job.company}\n"
                f"JD: {_sanitize_job_text_for_prompt(job.jd_text)}"
            )
        )

    batch_size = len(batch)
    return (
        f"Score the following {batch_size} jobs. Return a JSON array of exactly "
        f"{batch_size} objects in the same order, each with fields:\n"
        "job_index (0-based int), knocked_out (bool), knockout_reason "
        "(str or null), tech_stack (int), interviewability (int),\n"
        "work_auth (int), role_fit (int), total_score (int), tier (str),\n"
        "reasoning (str).\n\n"
        "Jobs:\n"
        f"{'\n\n'.join(job_blocks)}"
    )


def sanitize_llm_json(raw: str) -> str:
    """Strip code fences and illegal control characters from model JSON output."""

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.lstrip().startswith("```")
        ).strip()

    def _sanitize_string(match: re.Match[str]) -> str:
        token = match.group(0)
        inner = token[1:-1]
        inner = inner.replace("\n", " ").replace("\t", " ")
        inner = "".join(character for character in inner if ord(character) >= 32)
        return f'"{inner}"'

    return _JSON_STRING_PATTERN.sub(_sanitize_string, cleaned)


def _loads_llm_json(raw: str) -> Any:
    """Parse one LLM JSON payload after sanitization."""

    sanitized = sanitize_llm_json(raw)
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        logger.debug("rerank_json_parse_failed", raw_response=raw)
        raise


def _coerce_score(scores: dict[str, Any], key: str) -> int:
    """Validate and clamp one score entry."""

    raw_value = scores.get(key)
    if not isinstance(raw_value, int):
        raise ValueError(f"Invalid score for {key}: {raw_value!r}")

    return max(0, min(_SCORE_LIMITS[key], raw_value))


def _normalize_result(parsed: dict[str, Any], similarity_score: float) -> dict[str, Any]:
    """Validate the reranker payload and normalize tier assignment."""

    scores = parsed.get("scores")
    rationale = parsed.get("rationale")
    normalized_rationale: dict[str, str]
    reasoning_text: str

    if isinstance(scores, dict) and isinstance(rationale, dict):
        normalized_scores = {key: _coerce_score(scores, key) for key in _SCORE_LIMITS}
        normalized_rationale = {}
        for key in _SCORE_LIMITS:
            value = rationale.get(key)
            if not isinstance(value, str):
                raise ValueError(f"Invalid rationale for {key}: {value!r}")
            normalized_rationale[key] = value
        reasoning_text = " ".join(normalized_rationale.values())
    else:
        normalized_scores = {key: _coerce_score(parsed, key) for key in _SCORE_LIMITS}
        reasoning = parsed.get("reasoning")
        if not isinstance(reasoning, str):
            raise ValueError("Missing reasoning string in reranker response.")
        normalized_rationale = {key: reasoning for key in _SCORE_LIMITS}
        reasoning_text = reasoning

    knocked_out = bool(parsed.get("knocked_out"))
    knockout_reason = parsed.get("knockout_reason")
    if knockout_reason is not None and not isinstance(knockout_reason, str):
        raise ValueError("knockout_reason must be a string or null.")

    total_score = sum(normalized_scores.values())
    if knocked_out or total_score < 60:
        tier = "skip"
    elif total_score >= 75:
        tier = "A"
    else:
        tier = "B"

    return {
        "knocked_out": knocked_out,
        "knockout_reason": knockout_reason,
        "scores": normalized_scores,
        "rationale": normalized_rationale,
        "reasoning": reasoning_text,
        "total_score": total_score,
        "tier": tier,
        "embedding_similarity": similarity_score,
    }


def _update_job_from_result(db_job: Job, parsed: dict[str, Any]) -> None:
    """Apply the normalized reranker result to a persisted job row."""

    scores = parsed["scores"]

    db_job.knocked_out = bool(parsed["knocked_out"])
    db_job.knockout_reason = parsed["knockout_reason"]
    db_job.score_tech_stack = scores["tech_stack"]
    db_job.score_interviewability = scores["interviewability"]
    db_job.score_work_auth = scores["work_auth"]
    db_job.score_role_fit = scores["role_fit"]
    db_job.score_ai_signal = None
    db_job.score_growth = None
    db_job.total_score = parsed["total_score"]
    db_job.tier = parsed["tier"]
    db_job.score_breakdown = parsed
    db_job.llm_scored = True
    db_job.llm_scored_at = _utcnow()


def rerank_shortlist(shortlist: list[tuple[Job, float]]) -> list[Job]:
    """LLM-score one shortlist batch and return non-knocked-out rows by total score."""

    if not shortlist:
        return []

    settings = get_settings()
    batch_job_ids = [job.id for job, _ in shortlist]
    reranked_jobs: list[Job] = []

    try:
        raw_text = complete(
            RERANKER_SYSTEM_PROMPT,
            _build_batch_user_message(shortlist),
            model=settings.SCORER_MODEL,
        )
        json_text = _extract_json_array_text(raw_text, batch_job_ids)
        if json_text is None:
            raise ValueError("Missing JSON array in batch reranker response.")

        parsed_batch = _loads_llm_json(json_text)
        if not isinstance(parsed_batch, list) or len(parsed_batch) != len(shortlist):
            raise ValueError(
                f"Expected {len(shortlist)} reranker results, got {type(parsed_batch).__name__}."
            )

        normalized_by_index: dict[int, dict[str, Any]] = {}
        for item in parsed_batch:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid batch reranker item: {item!r}")
            job_index = item.get("job_index")
            if not isinstance(job_index, int):
                raise ValueError(f"Invalid job_index in batch reranker item: {item!r}")
            if job_index < 0 or job_index >= len(shortlist):
                raise ValueError(f"job_index out of range: {job_index}")
            if job_index in normalized_by_index:
                raise ValueError(f"Duplicate job_index in batch reranker response: {job_index}")

            _, similarity_score = shortlist[job_index]
            normalized_by_index[job_index] = _normalize_result(item, similarity_score)

        missing_indexes = [
            job_index for job_index in range(len(shortlist)) if job_index not in normalized_by_index
        ]
        if missing_indexes:
            raise ValueError(f"Missing job_index values in batch reranker response: {missing_indexes}")

        with get_db() as session:
            db_jobs = {
                db_job.id: db_job
                for db_job in session.scalars(select(Job).where(Job.id.in_(batch_job_ids)))
            }

            for job_index, (job, _) in enumerate(shortlist):
                db_job = db_jobs.get(job.id)
                if db_job is None:
                    logger.warning("rerank_job_missing_db_row", job_id=job.id)
                    continue

                _update_job_from_result(db_job, normalized_by_index[job_index])
                if not db_job.knocked_out:
                    reranked_jobs.append(db_job)

            session.commit()
    except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
        logger.warning(
            "rerank_batch_failed",
            job_ids=batch_job_ids,
            error=str(exc),
        )
        return _rerank_jobs_individually(shortlist, settings)

    reranked_jobs.sort(
        key=lambda job: (job.total_score is None, -(job.total_score or 0), job.id),
    )
    return reranked_jobs


def _rerank_jobs_individually(
    shortlist: list[tuple[Job, float]],
    settings,
) -> list[Job]:
    """Score one batch job-by-job as a fallback path."""

    reranked_jobs: list[Job] = []
    for job, similarity_score in shortlist:
        try:
            raw_text = complete(
                RERANKER_SYSTEM_PROMPT,
                _build_user_message(job, similarity_score),
                model=settings.SCORER_MODEL,
            )
            json_text = _extract_json_object_text(raw_text, job.id)
            if json_text is None:
                continue

            parsed = _loads_llm_json(json_text)
            normalized = _normalize_result(parsed, similarity_score)
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            logger.warning("rerank_job_failed", job_id=job.id, error=str(exc))
            continue

        with get_db() as session:
            db_job = session.get(Job, job.id)
            if db_job is None:
                logger.warning("rerank_job_missing_db_row", job_id=job.id)
                continue

            _update_job_from_result(db_job, normalized)
            session.commit()
            if not db_job.knocked_out:
                reranked_jobs.append(db_job)

    reranked_jobs.sort(
        key=lambda job: (job.total_score is None, -(job.total_score or 0), job.id),
    )
    return reranked_jobs


def run_reranking_pipeline() -> dict[str, int]:
    """Embed unprocessed jobs, shortlist them, rerank them, and summarize results."""

    with get_db() as session:
        unembedded_jobs = list(
            session.scalars(select(Job).where(Job.embedding_computed.is_(False)))
        )

    embedded = embed_jobs_batch(unembedded_jobs)
    shortlist = get_adaptive_shortlist()
    for batch_start in range(0, len(shortlist), RERANK_BATCH_SIZE):
        rerank_shortlist(shortlist[batch_start : batch_start + RERANK_BATCH_SIZE])

    shortlist_ids = [job.id for job, _ in shortlist]
    if not shortlist_ids:
        return {
            "embedded": embedded,
            "shortlisted": 0,
            "scored": 0,
            "knocked_out": 0,
            "tier_a": 0,
            "tier_b": 0,
        }

    with get_db() as session:
        scored = session.scalar(
            select(func.count(Job.id)).where(
                Job.id.in_(shortlist_ids),
                Job.llm_scored.is_(True),
            )
        ) or 0
        knocked_out = session.scalar(
            select(func.count(Job.id)).where(
                Job.id.in_(shortlist_ids),
                Job.knocked_out.is_(True),
            )
        ) or 0
        tier_a = session.scalar(
            select(func.count(Job.id)).where(
                Job.id.in_(shortlist_ids),
                Job.tier == "A",
            )
        ) or 0
        tier_b = session.scalar(
            select(func.count(Job.id)).where(
                Job.id.in_(shortlist_ids),
                Job.tier == "B",
            )
        ) or 0

    return {
        "embedded": embedded,
        "shortlisted": len(shortlist),
        "scored": scored,
        "knocked_out": knocked_out,
        "tier_a": tier_a,
        "tier_b": tier_b,
    }
