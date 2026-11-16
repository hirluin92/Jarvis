"""runs.marked_bad: flag set by the /bad command

Revision ID: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("marked_bad", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("runs", "marked_bad")
