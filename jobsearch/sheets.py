"""Google Sheets sync for the scored job board and Module 2 pipeline tracker."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Final

import gspread
from gspread.utils import ValidationConditionType
from sqlalchemy import select

from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.models import Job, JobFeedback, PipelineRole

JOB_BOARD_TITLE: Final = "Job Board"
PIPELINE_TITLE: Final = "Pipeline"
JOB_BOARD_HEADERS: Final[tuple[str, ...]] = (
    "Tier",
    "Score",
    "Company",
    "Title",
    "Location",
    "Remote",
    "Tech Stack",
    "Work Auth",
    "Interviewability",
    "Role Fit",
    "Embed Score",
    "URL",
    "Scraped At",
    "Status",
    "Notes",
)
PIPELINE_HEADERS: Final[tuple[str, ...]] = (
    "State",
    "Danger",
    "Company",
    "Title",
    "URL",
    "Applied At",
    "Last Activity",
    "Contacts",
    "Outreach Sent",
    "Outreach Received",
    "Closed Reason",
    "Notes",
    "Role ID",
)
JOB_BOARD_LAST_COLUMN: Final = "O"
PIPELINE_LAST_COLUMN: Final = "M"
VALID_STATUSES: Final[tuple[str, ...]] = (
    "New",
    "Applied",
    "Dismissed",
    "Screening",
    "Offer",
    "Closed",
)
STATUS_TO_FEEDBACK: Final[dict[str, tuple[int, str]]] = {
    "applied": (2, "add_to_pipeline"),
    "dismissed": (1, "dismissed"),
    "screening": (4, "interview"),
    "offer": (5, "offer"),
    "closed": (0, "implicit_reject"),
}
TIER_FORMATS: Final[dict[str, dict[str, dict[str, float]]]] = {
    "A": {
        "backgroundColor": {
            "red": 217 / 255,
            "green": 234 / 255,
            "blue": 211 / 255,
        }
    },
    "B": {
        "backgroundColor": {
            "red": 1.0,
            "green": 242 / 255,
            "blue": 204 / 255,
        }
    },
}
PIPELINE_ROW_FORMATS: Final[dict[str, dict[str, dict[str, float]]]] = {
    "danger": {
        "backgroundColor": {
            "red": 244 / 255,
            "green": 204 / 255,
            "blue": 204 / 255,
        }
    },
    "closed": {
        "backgroundColor": {
            "red": 239 / 255,
            "green": 239 / 255,
            "blue": 239 / 255,
        }
    },
    "loop": {
        "backgroundColor": {
            "red": 207 / 255,
            "green": 226 / 255,
            "blue": 243 / 255,
        }
    },
    "screen": {
        "backgroundColor": {
            "red": 217 / 255,
            "green": 210 / 255,
            "blue": 233 / 255,
        }
    },
    "human_touched": {
        "backgroundColor": {
            "red": 252 / 255,
            "green": 229 / 255,
            "blue": 205 / 255,
        }
    },
    "applied": {
        "backgroundColor": {
            "red": 1.0,
            "green": 242 / 255,
            "blue": 204 / 255,
        }
    },
    "discovered": {
        "backgroundColor": {
            "red": 1.0,
            "green": 1.0,
            "blue": 1.0,
        }
    },
}

_SPREADSHEET: gspread.Spreadsheet | None = None


def get_sheet_url() -> str:
    """Return the canonical URL for the configured spreadsheet."""

    settings = get_settings()
    if not settings.google_sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set.")
    return f"https://docs.google.com/spreadsheets/d/{settings.google_sheet_id}"


def get_sheet_client() -> gspread.Spreadsheet:
    """Open the configured spreadsheet and cache it as a singleton."""

    global _SPREADSHEET
    if _SPREADSHEET is not None:
        return _SPREADSHEET

    settings = get_settings()
    if not settings.google_service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set.")
    if not settings.google_sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set.")

    credentials_path = Path(settings.google_service_account_json).expanduser()
    if not credentials_path.exists():
        raise RuntimeError(
            f"Google service account credentials not found at {credentials_path}."
        )

    client = gspread.service_account(filename=str(credentials_path))
    _SPREADSHEET = client.open_by_key(settings.google_sheet_id)
    return _SPREADSHEET


def ensure_sheet_structure(sheet: gspread.Spreadsheet) -> None:
    """Create and initialize the required worksheets if they do not exist."""

    job_board = _get_or_create_worksheet(sheet, JOB_BOARD_TITLE, len(JOB_BOARD_HEADERS))
    _ensure_header_row(job_board, JOB_BOARD_HEADERS, JOB_BOARD_LAST_COLUMN)
    job_board.freeze(rows=1)
    _apply_status_validation(job_board, job_board.row_count)

    pipeline = _get_or_create_worksheet(sheet, PIPELINE_TITLE, len(PIPELINE_HEADERS))
    _ensure_header_row(pipeline, PIPELINE_HEADERS, PIPELINE_LAST_COLUMN)
    pipeline.freeze(rows=1)


def append_jobs_to_sheet(jobs: list[Job]) -> int:
    """Append new Tier A/B jobs to the Job Board worksheet."""

    if not jobs:
        return 0

    sheet = get_sheet_client()
    ensure_sheet_structure(sheet)
    worksheet = sheet.worksheet(JOB_BOARD_TITLE)

    existing_urls = {
        url.strip()
        for url in worksheet.col_values(12)[1:]
        if isinstance(url, str) and url.strip()
    }
    seen_urls = set(existing_urls)
    rows_to_append: list[list[object]] = []
    row_tiers: list[str] = []

    for job in jobs:
        if job.tier not in {"A", "B"}:
            continue
        if not job.url or job.url in seen_urls:
            continue

        rows_to_append.append(_build_sheet_row(job))
        row_tiers.append(job.tier)
        seen_urls.add(job.url)

    if not rows_to_append:
        return 0

    start_row = len(worksheet.get_all_values()) + 1
    worksheet.append_rows(rows_to_append, value_input_option="RAW")
    end_row = start_row + len(rows_to_append) - 1

    _apply_tier_colors(worksheet, start_row=start_row, row_tiers=row_tiers)
    _apply_status_validation(worksheet, max(worksheet.row_count, end_row))
    return len(rows_to_append)


def sync_feedback_from_sheet() -> int:
    """Persist sheet status changes into job_feedback rows."""

    sheet = get_sheet_client()
    ensure_sheet_structure(sheet)
    worksheet = sheet.worksheet(JOB_BOARD_TITLE)

    values = worksheet.get_all_values()
    if len(values) <= 1:
        return 0

    pending_statuses: dict[str, tuple[int, str]] = {}
    applied_urls: set[str] = set()
    for row in values[1:]:
        padded = row + [""] * max(0, len(JOB_BOARD_HEADERS) - len(row))
        url = padded[11].strip()
        status = padded[13].strip().lower()

        if not url or status in {"", "new"}:
            continue

        if status == "applied":
            applied_urls.add(url)

        feedback_config = STATUS_TO_FEEDBACK.get(status)
        if feedback_config is None or url in pending_statuses:
            continue
        pending_statuses[url] = feedback_config

    urls_to_lookup = set(pending_statuses) | applied_urls
    if not urls_to_lookup:
        return 0

    pending_pipeline_jobs: list[tuple[str, str, str, str]] = []
    with get_db() as session:
        jobs = list(session.scalars(select(Job).where(Job.url.in_(urls_to_lookup))))
        jobs_by_url = {job.url: job for job in jobs}
        existing_sheet_feedback_job_ids = {
            job_id
            for job_id in session.scalars(
                select(JobFeedback.job_id).where(JobFeedback.source == "sheet")
            )
        }
        existing_pipeline_job_ids = {
            job_id
            for job_id in session.scalars(
                select(PipelineRole.job_id).where(PipelineRole.job_id.is_not(None))
            )
        }

        inserted = 0
        for url, (label, signal_type) in pending_statuses.items():
            job = jobs_by_url.get(url)
            if job is None or job.id in existing_sheet_feedback_job_ids:
                continue

            session.add(
                JobFeedback(
                    job_id=job.id,
                    signal_type=signal_type,
                    label=label,
                    source="sheet",
                )
            )
            existing_sheet_feedback_job_ids.add(job.id)
            inserted += 1

        for url in applied_urls:
            job = jobs_by_url.get(url)
            if job is None or job.id in existing_pipeline_job_ids:
                continue
            pending_pipeline_jobs.append((job.id, job.company, job.title, job.url))
            existing_pipeline_job_ids.add(job.id)

        session.commit()

    if pending_pipeline_jobs:
        from jobsearch import pipeline

        for job_id, company, title, url in pending_pipeline_jobs:
            role = pipeline.create_pipeline_role(job_id, company, title, url)
            if role.state == "discovered":
                pipeline.advance_state(role.id, "applied")

    return inserted


def sync_pipeline_to_sheet(roles: list[PipelineRole]) -> None:
    """Rewrite the Pipeline tab from the current active pipeline roles."""

    sheet = get_sheet_client()
    ensure_sheet_structure(sheet)
    worksheet = sheet.worksheet(PIPELINE_TITLE)

    _ensure_row_capacity(worksheet, len(roles) + 1, len(PIPELINE_HEADERS))
    _clear_pipeline_rows(worksheet)

    if not roles:
        return

    rows = [_build_pipeline_row(role) for role in roles]
    end_row = len(rows) + 1
    worksheet.update(rows, f"A2:{PIPELINE_LAST_COLUMN}{end_row}")
    _apply_pipeline_colors(worksheet, start_row=2, roles=roles)


def highlight_danger_rows() -> None:
    """Re-apply pipeline row colors so danger rows stay visually prominent."""

    sheet = get_sheet_client()
    ensure_sheet_structure(sheet)
    worksheet = sheet.worksheet(PIPELINE_TITLE)

    values = worksheet.get_all_values()
    if len(values) <= 1:
        return

    ranges_by_key: dict[str, list[str]] = {}
    for row_number, row in enumerate(values[1:], start=2):
        padded = row + [""] * max(0, len(PIPELINE_HEADERS) - len(row))
        state = padded[0].strip().lower()
        danger = padded[1].strip()
        format_key = _pipeline_format_key(state, danger or None)
        ranges_by_key.setdefault(format_key, []).append(
            f"A{row_number}:{PIPELINE_LAST_COLUMN}{row_number}"
        )

    for format_key, ranges in ranges_by_key.items():
        worksheet.format(ranges, PIPELINE_ROW_FORMATS[format_key])


def _get_or_create_worksheet(
    sheet: gspread.Spreadsheet,
    title: str,
    column_count: int,
) -> gspread.Worksheet:
    """Return an existing worksheet or create it with a default shape."""

    try:
        worksheet = sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return sheet.add_worksheet(title=title, rows=1000, cols=column_count)

    if worksheet.col_count < column_count:
        worksheet.add_cols(column_count - worksheet.col_count)
    return worksheet


def _ensure_header_row(
    worksheet: gspread.Worksheet,
    headers: tuple[str, ...],
    last_column: str,
) -> None:
    """Write the header row and keep the sheet wide enough for it."""

    missing_cols = len(headers) - worksheet.col_count
    if missing_cols > 0:
        worksheet.add_cols(missing_cols)

    worksheet.update([list(headers)], f"A1:{last_column}1")
    worksheet.format(
        f"A1:{last_column}1",
        {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        },
    )


def _apply_status_validation(worksheet: gspread.Worksheet, row_limit: int) -> None:
    """Apply the status dropdown to the Status column."""

    worksheet.add_validation(
        f"N2:N{max(row_limit, 2)}",
        ValidationConditionType.one_of_list,
        list(VALID_STATUSES),
        inputMessage="Choose a valid workflow status.",
        strict=True,
        showCustomUi=True,
    )


def _build_sheet_row(job: Job) -> list[object]:
    """Convert a job row into the Job Board worksheet schema."""

    return [
        job.tier or "",
        job.total_score if job.total_score is not None else "",
        job.company,
        job.title,
        job.location,
        "Yes" if job.is_remote else "No",
        job.score_tech_stack if job.score_tech_stack is not None else "",
        job.score_work_auth if job.score_work_auth is not None else "",
        job.score_interviewability if job.score_interviewability is not None else "",
        job.score_role_fit if job.score_role_fit is not None else "",
        _embedding_similarity(job),
        job.url,
        _iso_date(job.scraped_at),
        "New",
        "",
    ]


def _build_pipeline_row(role: PipelineRole) -> list[object]:
    """Convert one pipeline role into the Pipeline worksheet schema."""

    sent_count = sum(1 for entry in role.outreach_log if entry.direction == "sent")
    received_count = sum(
        1 for entry in role.outreach_log if entry.direction == "received"
    )

    return [
        role.state,
        role.danger_state or "",
        role.company,
        role.title,
        role.url,
        _iso_date(role.applied_at),
        _iso_date(role.last_activity_at),
        ", ".join(contact.name for contact in role.contacts),
        sent_count,
        received_count,
        role.closed_reason or "",
        role.notes or "",
        role.id,
    ]


def _embedding_similarity(job: Job) -> str:
    """Extract the embedding similarity stored in the reranker payload."""

    breakdown = job.score_breakdown
    if not isinstance(breakdown, dict):
        return ""

    similarity = breakdown.get("embedding_similarity")
    if not isinstance(similarity, (int, float)):
        return ""
    return f"{float(similarity):.3f}"


def _iso_date(value: datetime | None) -> str:
    """Render a datetime as YYYY-MM-DD for the sheet."""

    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def _apply_tier_colors(
    worksheet: gspread.Worksheet,
    *,
    start_row: int,
    row_tiers: Iterable[str],
) -> None:
    """Apply the per-tier background colors to appended Job Board rows."""

    ranges_by_tier: dict[str, list[str]] = {"A": [], "B": []}
    for offset, tier in enumerate(row_tiers):
        if tier not in ranges_by_tier:
            continue
        row_number = start_row + offset
        ranges_by_tier[tier].append(
            f"A{row_number}:{JOB_BOARD_LAST_COLUMN}{row_number}"
        )

    for tier, ranges in ranges_by_tier.items():
        if not ranges:
            continue
        worksheet.format(ranges, TIER_FORMATS[tier])


def _apply_pipeline_colors(
    worksheet: gspread.Worksheet,
    *,
    start_row: int,
    roles: list[PipelineRole],
) -> None:
    """Apply state-based background colors to Pipeline rows."""

    ranges_by_key: dict[str, list[str]] = {}
    for offset, role in enumerate(roles):
        row_number = start_row + offset
        format_key = _pipeline_format_key(role.state, role.danger_state)
        ranges_by_key.setdefault(format_key, []).append(
            f"A{row_number}:{PIPELINE_LAST_COLUMN}{row_number}"
        )

    for format_key, ranges in ranges_by_key.items():
        worksheet.format(ranges, PIPELINE_ROW_FORMATS[format_key])


def _pipeline_format_key(state: str, danger_state: str | None) -> str:
    """Map one pipeline row to its background color key."""

    if danger_state:
        return "danger"

    normalized_state = state.strip().lower()
    if normalized_state in PIPELINE_ROW_FORMATS:
        return normalized_state
    return "discovered"


def _ensure_row_capacity(
    worksheet: gspread.Worksheet,
    minimum_rows: int,
    minimum_cols: int,
) -> None:
    """Expand the worksheet when the current shape is too small."""

    if worksheet.row_count < minimum_rows:
        worksheet.add_rows(minimum_rows - worksheet.row_count)
    if worksheet.col_count < minimum_cols:
        worksheet.add_cols(minimum_cols - worksheet.col_count)


def _clear_pipeline_rows(worksheet: gspread.Worksheet) -> None:
    """Remove old Pipeline values and reset row backgrounds to white."""

    row_limit = max(worksheet.row_count, 2)
    worksheet.batch_clear([f"A2:{PIPELINE_LAST_COLUMN}{row_limit}"])
    worksheet.format(
        f"A2:{PIPELINE_LAST_COLUMN}{row_limit}",
        PIPELINE_ROW_FORMATS["discovered"],
    )
