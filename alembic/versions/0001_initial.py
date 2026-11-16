"""initial: runs and steps

Revision ID: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("user_message", sa.Text, nullable=False),
        sa.Column("final_answer", sa.Text),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
    )
    op.create_table(
        "steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id"), nullable=False, index=True),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("tool_name", sa.String(64)),
        sa.Column("input", sa.JSON),
        sa.Column("output", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("steps")
    op.drop_table("runs")
