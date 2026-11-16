"""memories and persistent conversations (phase 5)

Revision ID: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("source_run_id", sa.Integer, sa.ForeignKey("runs.id")),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("superseded_by", sa.Integer),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("history", sa.JSON, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversations")
    op.drop_table("memories")
