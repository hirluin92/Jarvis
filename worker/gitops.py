"""Git operations of the worker. Every command runs with a clean environment."""

import asyncio
import base64
import logging
import re
import shutil
from pathlib import Path

from worker.envclean import clean_env

log = logging.getLogger("jarvis.worker.git")
BOT_NAME = "Jarvis build"
BOT_EMAIL = "jarvis-build@users.noreply.github.com"


class GitError(RuntimeError):
    pass


async def run(
    args: list[str], cwd: Path | str, *, env: dict[str, str] | None = None, timeout: float = 180
) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        env=env or clean_env({"GIT_TERMINAL_PROMPT": "0"}),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise GitError(f"git {args[0]} timed out") from None
    text = out.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {text.strip()[-300:]}")
    return text


def slug(title: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s or "lavoro")[:limit].strip("-")


def branch_name(job_id: int, title: str) -> str:
    return f"jarvis/{job_id}-{slug(title)}"


def assert_safe_branch(branch: str, base: str) -> None:
    """The worker may only ever push a branch of its own, never the main one."""
    short_base = base.split("/", 1)[-1]
    if not branch.startswith("jarvis/") or branch in (base, short_base, "main", "master"):
        raise GitError(f"refusing to push branch {branch!r}")


async def default_base(local: Path) -> str:
    """The remote default branch as `origin/<name>`."""
    try:
        head = (await run(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], local)).strip()
        if head:
            return head
    except GitError:
        pass
    for name in ("origin/main", "origin/master"):
        try:
            await run(["rev-parse", "--verify", "--quiet", name], local)
            return name
        except GitError:
            continue
    raise GitError("cannot find the default branch of origin")


async def prepare_worktree(local: Path, path: Path, branch: str, base_ref: str) -> None:
    """An isolated working copy on a new branch cut from the remote default branch."""
    if path.exists():  # a resumed job keeps its copy
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    await run(["fetch", "origin"], local, timeout=300)
    try:
        await run(["worktree", "add", "-b", branch, str(path), base_ref], local)
    except GitError:  # the branch exists from an earlier try
        await run(["worktree", "add", str(path), branch], local)


# build leftovers that must never end up in a commit, whatever the product's .gitignore says
JUNK = (
    ":(glob)**/__pycache__/**", ":(glob)**/*.pyc", ":(glob)**/node_modules/**",
    ":(glob)**/.pytest_cache/**", ":(glob)**/.venv/**", ":(glob)**/.DS_Store",
)  # fmt: skip


async def changed_files(worktree: Path, base_ref: str) -> list[str]:
    """Everything the build changed, committed or not, compared with the base branch."""
    await run(["add", "-A"], worktree)
    await run(["rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", *JUNK], worktree)
    out = await run(["diff", "--cached", "--name-only", "--no-renames", "-z", base_ref], worktree)
    return [f for f in out.split("\0") if f]


async def commit(worktree: Path, message: str) -> None:
    await run(
        ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "-m", message],
        worktree,
    )


async def push(worktree: Path, branch: str, base_ref: str, remote: str, token: str = "") -> None:
    """Push the job branch (and only it). The token travels in the environment of this one
    process, not on a command line."""
    assert_safe_branch(branch, base_ref)
    extra = {"GIT_TERMINAL_PROMPT": "0"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        extra |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        }
    await run(["push", remote, f"HEAD:refs/heads/{branch}"], worktree, env=clean_env(extra))


async def remove_worktree(local: Path, path: Path) -> None:
    try:
        await run(["worktree", "remove", "--force", str(path)], local)
    except GitError:
        pass
    shutil.rmtree(path, ignore_errors=True)
    try:
        await run(["worktree", "prune"], local)
    except GitError:
        pass
