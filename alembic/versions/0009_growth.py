"""leads, outreach queue, suppression, inbound messages, metrics, growth state (phase 13)

Revision ID: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opportunity_id", sa.Integer, sa.ForeignKey("opportunities.id"), index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contact", sa.String(200), nullable=False, index=True),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("legal_basis", sa.Text, nullable=False),
        sa.Column("consent", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text, nullable=False),
    )
    op.create_table(
        "outreach_queue",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("thread_id", sa.String(80)),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("blocked_reason", sa.String(300)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "suppression",
        sa.Column("email", sa.String(200), primary_key=True),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gmail_id", sa.String(64), nullable=False, unique=True),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("from_addr", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("classification", sa.String(16), nullable=False, index=True),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("proposed_reply", sa.Text, nullable=False),
        sa.Column("handled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id")),
    )
    op.create_table(
        "metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opportunity_id", sa.Integer, sa.ForeignKey("opportunities.id"), index=True),
        sa.Column("week", sa.Date, nullable=False, index=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
    )
    op.create_table(
        "growth_state",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    for t in (
        "growth_state",
        "metrics",
        "inbound_messages",
        "suppression",
        "outreach_queue",
        "leads",
    ):
        op.drop_table(t)
