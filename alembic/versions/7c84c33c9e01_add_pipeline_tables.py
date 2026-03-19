"""add pipeline tables

Revision ID: 7c84c33c9e01
Revises: 2f6f8f8f4b76
Create Date: 2026-03-18 16:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c84c33c9e01"
down_revision: Union[str, Sequence[str], None] = "2f6f8f8f4b76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "pipeline_roles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column(
            "state",
            sa.String(),
            nullable=False,
            server_default=sa.text("'discovered'"),
        ),
        sa.Column("danger_state", sa.String(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
        sa.Column("closed_reason", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_roles_job_id", "pipeline_roles", ["job_id"], unique=False)
    op.create_index("ix_pipeline_roles_state", "pipeline_roles", ["state"], unique=False)

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pipeline_role_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_role_id"],
            ["pipeline_roles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contacts_pipeline_role_id", "contacts", ["pipeline_role_id"], unique=False)

    op.create_table(
        "outreach_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pipeline_role_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("message_summary", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_role_id"],
            ["pipeline_roles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outreach_log_pipeline_role_id",
        "outreach_log",
        ["pipeline_role_id"],
        unique=False,
    )
    op.create_index("ix_outreach_log_sent_at", "outreach_log", ["sent_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index("ix_outreach_log_sent_at", table_name="outreach_log")
    op.drop_index("ix_outreach_log_pipeline_role_id", table_name="outreach_log")
    op.drop_table("outreach_log")

    op.drop_index("ix_contacts_pipeline_role_id", table_name="contacts")
    op.drop_table("contacts")

    op.drop_index("ix_pipeline_roles_state", table_name="pipeline_roles")
    op.drop_index("ix_pipeline_roles_job_id", table_name="pipeline_roles")
    op.drop_table("pipeline_roles")
