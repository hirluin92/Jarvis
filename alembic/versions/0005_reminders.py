"""reminders (phase 9)

Revision ID: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("reminders")
