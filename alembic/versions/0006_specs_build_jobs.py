"""specs and build jobs (phase 10)

Revision ID: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.create_table(
        "specs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product", sa.String(80), nullable=False, index=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
    )
    op.create_table(
        "build_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("spec_id", sa.Integer, sa.ForeignKey("specs.id"), nullable=False, index=True),
        sa.Column("product", sa.String(80), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("branch", sa.String(200)),
        sa.Column("pr_url", sa.String(300)),
        sa.Column("log_summary", sa.Text),
        sa.Column("duration_s", sa.Integer),
        sa.Column("stop_reason", sa.String(300)),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("notified_status", sa.String(16)),
    )


def downgrade() -> None:
    op.drop_table("build_jobs")
    op.drop_table("specs")
