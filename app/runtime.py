from contextvars import ContextVar

# The run being executed, so tools can record where a write came from (e.g. a memory's origin).
current_run_id: ContextVar[int | None] = ContextVar("current_run_id", default=None)
