"""add feedback and embedding columns

Revision ID: 2f6f8f8f4b76
Revises: f783c1d0b760
Create Date: 2026-03-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2f6f8f8f4b76"
down_revision: Union[str, Sequence[str], None] = "f783c1d0b760"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "jobs",
        sa.Column(
            "embedding_computed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "job_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("signal_type", sa.String(), nullable=False),
        sa.Column("label", sa.Integer(), nullable=False),
        sa.Column("rank_position", sa.Integer(), nullable=True),
        sa.Column("embedding_score", sa.Float(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_feedback_created_at"), "job_feedback", ["created_at"], unique=False)
    op.create_index(op.f("ix_job_feedback_job_id"), "job_feedback", ["job_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(op.f("ix_job_feedback_job_id"), table_name="job_feedback")
    op.drop_index(op.f("ix_job_feedback_created_at"), table_name="job_feedback")
    op.drop_table("job_feedback")
    op.drop_column("jobs", "embedding_computed")
