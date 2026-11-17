"""opportunities, evidence and validation plans (phase 12)

Revision ID: 0008
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("niche", sa.String(200), nullable=False),
        sa.Column("problem", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("product", sa.String(80)),
        sa.Column("score_detail", sa.JSON),
        sa.Column("decision_reason", sa.Text),
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opportunity_id", sa.Integer, sa.ForeignKey("opportunities.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("fetched_on", sa.Date, nullable=False),
    )
    op.create_index("ix_evidence_opportunity_id", "evidence", ["opportunity_id"])
    op.create_table(
        "validation_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "opportunity_id",
            sa.Integer,
            sa.ForeignKey("opportunities.id"),
            nullable=False,
            unique=True,
        ),  # fmt: skip
        sa.Column("kit", sa.Text, nullable=False),
        sa.Column("thresholds", sa.JSON, nullable=False),
        sa.Column("deadline", sa.Date, nullable=False),
        sa.Column("kill_criteria", sa.Text, nullable=False),
        sa.Column("approved", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("validation_plans")
    op.drop_table("evidence")
    op.drop_table("opportunities")
