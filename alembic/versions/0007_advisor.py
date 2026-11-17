"""knowledge base, product briefs, decisions, advisor sessions and spend, review moves (phase 11)

Revision ID: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product", sa.String(80), nullable=False, index=True),
        sa.Column("source", sa.String(300), nullable=False, index=True),
        sa.Column("heading", sa.String(300), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "product_briefs",
        sa.Column("product", sa.String(80), primary_key=True),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("customer", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("key_numbers", sa.Text, nullable=False),
        sa.Column("risks", sa.Text, nullable=False),
        sa.Column("next_moves", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product", sa.String(80), nullable=False, index=True),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("reasons", sa.Text, nullable=False),
        sa.Column("alternatives", sa.Text, nullable=False),
        sa.Column("review_on", sa.Date),
        sa.Column("contradicts", sa.JSON),
    )
    op.create_table(
        "advisor_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product", sa.String(80), nullable=False, index=True),
        sa.Column("summary", sa.Text, nullable=False),
    )
    op.create_table(
        "advisor_spend",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("usd", sa.Float, nullable=False),
    )
    op.create_table(
        "review_moves",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product", sa.String(80), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("spec_id", sa.Integer),
    )


def downgrade() -> None:
    for t in (
        "review_moves", "advisor_spend", "advisor_sessions", "decisions", "product_briefs",
        "knowledge_chunks",
    ):  # fmt: skip
        op.drop_table(t)
