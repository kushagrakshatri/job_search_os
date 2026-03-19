"""Embedding storage and similarity search for job ranking."""

from __future__ import annotations

import time
from pathlib import Path

import chromadb
import structlog
from openai import OpenAI
from sqlalchemy import select

from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.models import Job
from jobsearch.resume import get_resume_embedding

logger = structlog.get_logger(__name__)

COLLECTION_NAME = "job_embeddings"
_OPENAI_CLIENT: OpenAI | None = None
_CHROMA_CLIENT: chromadb.PersistentClient | None = None
_COLLECTION = None


def _get_openai_client() -> OpenAI:
    """Return a cached OpenAI client for job embedding requests."""

    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT

    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Job embeddings require it.")

    _OPENAI_CLIENT = OpenAI(api_key=settings.openai_api_key)
    return _OPENAI_CLIENT


def _get_chroma_client() -> chromadb.PersistentClient:
    """Return a cached persistent Chroma client."""

    global _CHROMA_CLIENT
    if _CHROMA_CLIENT is not None:
        return _CHROMA_CLIENT

    settings = get_settings()
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
    _CHROMA_CLIENT = chromadb.PersistentClient(path=settings.chroma_path)
    return _CHROMA_CLIENT


def _get_collection():
    """Return the single cosine-distance Chroma collection."""

    global _COLLECTION
    if _COLLECTION is not None:
        return _COLLECTION

    _COLLECTION = _get_chroma_client().get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return _COLLECTION


def get_chroma_collection():
    """Return the shared Chroma collection used for job embeddings."""

    return _get_collection()


def _job_embedding_text(job: Job) -> str:
    """Build the text used to embed a job row."""

    return f"{job.title} at {job.company}. {job.jd_text[:2000]}"


def _job_metadata(job: Job) -> dict[str, str]:
    """Build the Chroma metadata payload for one job."""

    return {
        "title": job.title,
        "company": job.company,
        "source": job.source,
        "tier": job.tier or "unscored",
        "scraped_at": job.scraped_at.isoformat(),
    }


def _mark_jobs_embedded(job_ids: list[str]) -> None:
    """Mark persisted job rows as having computed embeddings."""

    if not job_ids:
        return

    with get_db() as session:
        jobs = list(session.scalars(select(Job).where(Job.id.in_(job_ids))))
        for job in jobs:
            job.embedding_computed = True
        session.commit()


def _existing_embedding_ids(job_ids: list[str]) -> set[str]:
    """Return IDs already present in Chroma."""

    if not job_ids:
        return set()

    result = _get_collection().get(ids=job_ids)
    ids = result.get("ids") or []
    return {str(job_id) for job_id in ids}


def _embed_job_once(job: Job) -> bool:
    """Embed one job row and mark it as persisted in Chroma and SQLite."""

    settings = get_settings()
    response = _get_openai_client().embeddings.create(
        model=settings.embedding_model,
        input=_job_embedding_text(job),
    )
    embedding = list(response.data[0].embedding)
    _get_collection().upsert(
        ids=[job.id],
        embeddings=[embedding],
        metadatas=[_job_metadata(job)],
    )
    _mark_jobs_embedded([job.id])
    return True


def embed_job(job: Job) -> None:
    """Embed and upsert one job row, logging failures without raising."""

    if job.embedding_computed:
        return

    try:
        _embed_job_once(job)
    except Exception as exc:
        logger.warning("job_embedding_failed", job_id=job.id, error=str(exc))


def embed_jobs_batch(jobs: list[Job]) -> int:
    """Embed unpersisted jobs in OpenAI batches of 100."""

    candidates = [job for job in jobs if not job.embedding_computed]
    if not candidates:
        return 0

    settings = get_settings()
    embedded_count = 0

    for start in range(0, len(candidates), 100):
        batch = candidates[start : start + 100]
        batch_ids = [job.id for job in batch]

        try:
            existing_ids = _existing_embedding_ids(batch_ids)
        except Exception as exc:
            logger.warning("job_embedding_lookup_failed", job_ids=batch_ids, error=str(exc))
            existing_ids = set()

        if existing_ids:
            _mark_jobs_embedded(sorted(existing_ids))

        to_embed = [job for job in batch if job.id not in existing_ids]
        if not to_embed:
            time.sleep(0.1)
            continue

        try:
            response = _get_openai_client().embeddings.create(
                model=settings.embedding_model,
                input=[_job_embedding_text(job) for job in to_embed],
            )
            embeddings = [list(item.embedding) for item in response.data]
            _get_collection().upsert(
                ids=[job.id for job in to_embed],
                embeddings=embeddings,
                metadatas=[_job_metadata(job) for job in to_embed],
            )
            _mark_jobs_embedded([job.id for job in to_embed])
            embedded_count += len(to_embed)
        except Exception as exc:
            logger.warning(
                "job_embedding_batch_failed",
                job_ids=[job.id for job in to_embed],
                error=str(exc),
            )
            for job in to_embed:
                try:
                    if _embed_job_once(job):
                        embedded_count += 1
                except Exception as single_exc:
                    logger.warning("job_embedding_failed", job_id=job.id, error=str(single_exc))

        time.sleep(0.1)

    return embedded_count


def get_similar_jobs(
    top_k: int = 200,
    min_score: float = 0.3,
) -> list[tuple[Job, float]]:
    """Query Chroma with the resume embedding and return matching unscored jobs."""

    if top_k <= 0:
        return []

    try:
        collection = _get_collection()
        collection_size = collection.count()
        if collection_size == 0:
            logger.warning("job_embedding_collection_empty")
            return []

        result = collection.query(
            query_embeddings=[get_resume_embedding()],
            n_results=min(top_k, collection_size),
            include=["distances"],
        )
    except Exception as exc:
        logger.warning("job_similarity_query_failed", error=str(exc))
        return []

    ids = [str(job_id) for job_id in (result.get("ids") or [[]])[0]]
    distances = list(result.get("distances") or [[]])[0]
    if not ids:
        return []

    with get_db() as session:
        jobs_by_id = {
            job.id: job
            for job in session.scalars(select(Job).where(Job.id.in_(ids)))
        }

    matches: list[tuple[Job, float]] = []
    for job_id, distance in zip(ids, distances, strict=False):
        job = jobs_by_id.get(job_id)
        if job is None or job.llm_scored or job.knocked_out:
            continue
        similarity = 1.0 - float(distance)
        if similarity < min_score:
            continue
        matches.append((job, similarity))

    matches.sort(key=lambda item: item[1], reverse=True)
    return matches


def get_adaptive_shortlist(
    min_score: float = 0.4,
    daily_cap: int = 500,
) -> list[tuple[Job, float]]:
    """Build the shortlist from the embedding threshold only."""

    try:
        collection_size = _get_collection().count()
    except Exception as exc:
        logger.warning("job_shortlist_count_failed", error=str(exc))
        return []

    if collection_size <= 0:
        logger.info(
            "embedding_shortlist",
            min_score=min_score,
            passed_threshold=0,
            daily_cap=daily_cap,
            final_count=0,
        )
        return []

    threshold_matches = get_similar_jobs(top_k=collection_size, min_score=min_score)
    shortlisted = threshold_matches[: max(daily_cap, 0)]
    logger.info(
        "embedding_shortlist",
        min_score=min_score,
        passed_threshold=len(threshold_matches),
        daily_cap=daily_cap,
        final_count=len(shortlisted),
    )
    return shortlisted
