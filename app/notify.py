"""Hooks that let tools reach the running interfaces (set by app.main at startup)."""

from collections.abc import Awaitable, Callable

# send a text to the user on Telegram
sender: Callable[[str], Awaitable[None]] | None = None
# switch the advisor mode of the user's chat: a product name, or None to leave it
advisor_switch: Callable[[str | None], Awaitable[str]] | None = None
