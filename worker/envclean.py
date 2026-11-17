"""The environment given to Claude Code and to product commands: a short allow-list.

Nothing from Jarvis (.env values, tokens, keys) is passed on: only what a program needs to run
and to find the user's own Claude Code login in their profile.
"""

import os

ALLOWED = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "HOME", "USER", "LANG", "TMPDIR", "TERM",
)  # fmt: skip


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A fresh environment with only the allow-listed variables, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if k.upper() in ALLOWED}
    env.update(extra or {})
    return env
