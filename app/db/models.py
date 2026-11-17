from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running")
    user_message: Mapped[str] = mapped_column(Text)
    final_answer: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="")
    marked_bad: Mapped[bool] = mapped_column(Boolean, default=False)
    steps: Mapped[list["Step"]] = relationship(order_by="Step.idx", back_populates="run")


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    title: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)


class Todo(Base):
    __tablename__ = "todos"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    text: Mapped[str] = mapped_column(Text)
    due: Mapped[date | None] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class Action(Base):
    """Audit log of every write/confirm tool execution: who, what, when, outcome, proof."""

    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    permission: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict | list | None] = mapped_column(JSON)
    output: Mapped[dict | list | None] = mapped_column(JSON)  # the proof (issue URL, note id...)
    error: Mapped[str | None] = mapped_column(Text)


class Memory(Base):
    """A verified fact worth remembering across conversations (not a chat transcript)."""

    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16), default="fact")
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer)


class Conversation(Base):
    """Persistent chat history, so a restart continues where the conversation was."""

    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Step(Base):
    __tablename__ = "steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict | list | None] = mapped_column(JSON)
    output: Mapped[dict | list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    run: Mapped[Run] = relationship(back_populates="steps")


class Reminder(Base):
    """Something Jarvis must tell the user at a given time (delivered on Telegram)."""

    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Spec(Base):
    """A short specification of something to build on a product (phase 10)."""

    __tablename__ = "specs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    product: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft|approved
    version: Mapped[int] = mapped_column(default=1)


class BuildJob(Base):
    """A build job. The worker process reads these rows; Jarvis only writes them."""

    __tablename__ = "build_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    spec_id: Mapped[int] = mapped_column(ForeignKey("specs.id"), index=True)
    product: Mapped[str] = mapped_column(String(80), index=True)
    # queued | running | paused_quota | failed | cancelled | pr_opened
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    branch: Mapped[str | None] = mapped_column(String(200))
    pr_url: Mapped[str | None] = mapped_column(String(300))
    log_summary: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[int | None] = mapped_column()
    stop_reason: Mapped[str | None] = mapped_column(String(300))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_status: Mapped[str | None] = mapped_column(String(16))  # last status told to the user


class KnowledgeChunk(Base):
    """A section of a product document imported from knowledge/<product>/ (phase 11)."""

    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(300), index=True)  # file name inside the product dir
    heading: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProductBrief(Base):
    """One structured summary per product, with the date it was last updated."""

    __tablename__ = "product_briefs"
    product: Mapped[str] = mapped_column(String(80), primary_key=True)
    objective: Mapped[str] = mapped_column(Text, default="")
    customer: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="")
    key_numbers: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[str] = mapped_column(Text, default="")
    next_moves: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Decision(Base):
    """A decision with its reasons, the discarded alternatives and a date to look at it again."""

    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    product: Mapped[str] = mapped_column(String(80), index=True)
    decision: Mapped[str] = mapped_column(Text)
    reasons: Mapped[str] = mapped_column(Text, default="")
    alternatives: Mapped[str] = mapped_column(Text, default="")
    review_on: Mapped[date | None] = mapped_column(Date)
    contradicts: Mapped[list | None] = mapped_column(JSON)  # ids of decisions this one contradicts


class AdvisorSession(Base):
    """The summary of an advisor session, saved only after the user's yes."""

    __tablename__ = "advisor_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    product: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(Text)


class AdvisorSpend(Base):
    """Estimated spend of the advisor model per day (USD), for the daily budget."""

    __tablename__ = "advisor_spend"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    usd: Mapped[float] = mapped_column(default=0.0)


class ReviewMove(Base):
    """One of the three moves proposed by the weekly review; a button turns it into a spec."""

    __tablename__ = "review_moves"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    product: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text, default="")
    spec_id: Mapped[int | None] = mapped_column()  # set once the move became a draft spec
