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
