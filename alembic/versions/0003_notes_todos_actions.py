"""notes, todos and the actions audit log (phase 4)

Revision ID: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("body", sa.Text, nullable=False),
    )
    op.create_table(
        "todos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("due", sa.Date),
        sa.Column("done", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "actions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id"), index=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(16), nullable=False),
        sa.Column("input", sa.JSON),
        sa.Column("output", sa.JSON),
        sa.Column("error", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("actions")
    op.drop_table("todos")
    op.drop_table("notes")
