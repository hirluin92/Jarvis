import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import builds
from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.loop import resume_confirmed, run_agent
from app.catalog import Catalog, Product
from app.db import session as db
from app.db.models import BuildJob
from app.tools.builtin import cancel_build, draft_spec, start_build
from worker import claude_runner, gitops, runner
from worker.envclean import clean_env
from worker.protected import violations

FAKE = [sys.executable, str(Path(__file__).parent / "fake_claude.py")]


# ---------- pure pieces ----------


def test_protected_paths_globs():
    patterns = ["src/orders/**", "*.pem", "config/secrets.yaml", "infra/"]
    changed = [
        "src/orders/execute.py",
        "src/orders/deep/er/x.py",
        "src/ordersx/ok.py",
        "key.pem",
        "docs/key.pem",
        "config/secrets.yaml",
        "config/secrets.yaml.bak",
        "infra/main.tf",
        "README.md",
    ]
    assert violations(changed, patterns) == [
        "src/orders/execute.py",
        "src/orders/deep/er/x.py",
        "key.pem",
        "config/secrets.yaml",
        "infra/main.tf",
    ]
    assert violations(["a.py"], []) == []


def test_claude_code_does_not_see_the_environment_of_jarvis(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GH_WRITE_TOKEN", "BUILD_GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(name, "SECRET-" + name)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:SECRET@h/db")
    env = clean_env()
    assert not any("SECRET" in v for v in env.values())
    assert "ANTHROPIC_API_KEY" not in env and "DATABASE_URL" not in env
    assert "PATH" in {k.upper() for k in env}
    # and a real child process confirms it
    out = subprocess.run(
        [sys.executable, "-c", "import os,json;print(json.dumps(sorted(os.environ)))"],
        env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip
    seen = json.loads(out.stdout)
    assert not {"ANTHROPIC_API_KEY", "GH_WRITE_TOKEN", "BUILD_GITHUB_TOKEN", "DATABASE_URL"} & set(
        seen
    )


def test_claude_command_is_locked_down():
    cmd = claude_runner.build_command(["claude"], "pytest -q", model="haiku", budget_usd=2)
    assert cmd[:2] == ["claude", "-p"]
    for flag in ("--restricted", "--no-session-persistence", "--verbose"):
        assert flag in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert "--dangerously-skip-permissions" not in cmd and "bypassPermissions" not in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1].split(" ")
    assert "Bash(pytest -q)" in " ".join(cmd) and "Bash" not in allowed  # no blanket shell
    assert "push" not in " ".join(allowed)
    assert (
        cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--max-budget-usd") + 1] == "2"
    )
    assert "--max-turns" not in cmd  # this Claude Code version has no such flag: we count turns


def test_only_job_branches_can_be_pushed():
    gitops.assert_safe_branch("jarvis/3-export-pdf", "origin/main")
    for bad in ("main", "master", "origin/main", "feature/x", "jarvis"):
        with pytest.raises(gitops.GitError):
            gitops.assert_safe_branch(bad, "origin/main")


# ---------- running the program ----------


async def _never() -> bool:
    return False


async def test_run_claude_reads_the_result_and_denials():
    out = await claude_runner.run_claude(
        FAKE, Path("."), "MODE=noop", timeout_s=30, max_turns=10, should_cancel=_never
    )
    assert out.kind == "done" and out.turns == 3 and out.cost_usd == 0.01


@pytest.mark.parametrize(
    "mode,kind",
    [("sleep", "timeout"), ("manyturns", "turns"), ("quota", "quota"), ("error", "error")],
)
async def test_run_claude_limits_and_failures(mode, kind):
    out = await claude_runner.run_claude(
        FAKE, Path("."), f"MODE={mode}", timeout_s=2, max_turns=5, should_cancel=_never, poll_s=0.2
    )
    assert out.kind == kind


async def test_run_claude_is_killed_on_cancel():
    calls = {"n": 0}

    async def cancel_soon() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    out = await claude_runner.run_claude(
        FAKE,
        Path("."),
        "MODE=sleep",
        timeout_s=60,
        max_turns=5,
        should_cancel=cancel_soon,
        poll_s=0.2,
    )
    assert out.kind == "cancelled"


# ---------- a whole job against real git repositories ----------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A bare 'origin', a clone used as the product's local copy, and the settings of a worker."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "t@t")
    git(seed, "config", "user.name", "t")
    (seed / "app.py").write_text("print('hi')\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "init")
    git(seed, "push", "-q", "origin", "HEAD:main")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True, capture_output=True)
    settings = SimpleNamespace(
        build_workdir=str(tmp_path / "work"), build_github_token="tok", build_timeout_minutes=1,
        build_max_turns=20, build_model="", build_max_budget_usd=0, build_claude_bin="x",
        build_max_parallel=1,
    )  # fmt: skip
    return SimpleNamespace(origin=origin, local=local, settings=settings, tmp=tmp_path)


def catalog(monkeypatch, repo, **over):
    prod = dict(
        name="Demo", github_repo="me/demo", local_path=str(repo.local), build_enabled=True,
        test_command='python -c "print(1)"', protected_paths=["secret/**"],
    )  # fmt: skip
    prod.update(over)
    cat = Catalog(products=[Product(**prod)])
    for module in (builds, runner):
        monkeypatch.setattr(module, "get_catalog", lambda: cat)
    return cat


async def make_job(mode: str) -> BuildJob:
    spec = await builds.save_spec(
        "Demo", "Aggiungi l'export", f"Obiettivo: export. MODE={mode} " + "x" * 40
    )
    return await builds.enqueue(spec.id)


class PRs:
    def __init__(self):
        self.calls = []

    async def __call__(self, token, repo, title, head, base, body):
        self.calls.append(dict(repo=repo, title=title, head=head, base=base, body=body))
        return "https://github.com/me/demo/pull/7"


async def run_job(repo, mode, prs=None, **kw):
    job = await make_job(mode)
    claimed = await runner.claim_next()
    assert claimed.id == job.id and claimed.status == "running"
    await runner.process(
        claimed, repo.settings, claude_cmd=FAKE, remote_for=lambda r: str(repo.origin),
        open_pr=prs or PRs(), poll_s=0.2, **kw,
    )  # fmt: skip
    return await builds.get_job(job.id)


def origin_branches(repo) -> list[str]:
    return git(repo.origin, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()


async def test_a_real_job_pushes_its_own_branch_and_opens_a_draft_pr(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    main_before = git(repo.origin, "rev-parse", "main")
    prs = PRs()
    job = await run_job(repo, "edit", prs)
    assert job.status == "pr_opened" and job.pr_url.endswith("/pull/7")
    assert job.branch == f"jarvis/{job.id}-aggiungi-l-export"
    assert sorted(origin_branches(repo)) == sorted(["main", job.branch])
    assert git(repo.origin, "rev-parse", "main") == main_before  # main never receives a commit
    (call,) = prs.calls
    assert call["base"] == "main" and call["head"] == job.branch and "test" in call["body"].lower()
    assert "feature.txt" in git(repo.origin, "show", "--name-only", "--format=", job.branch)
    assert "test verdi" in job.log_summary
    assert not (Path(repo.settings.build_workdir) / f"job-{job.id}").exists()  # copy cleaned up


async def test_a_change_to_a_protected_path_stops_the_job_before_any_push(
    tracer, repo, monkeypatch
):
    catalog(monkeypatch, repo)
    prs = PRs()
    job = await run_job(repo, "protected", prs)
    assert job.status == "failed" and "secret/key.txt" in job.stop_reason
    assert origin_branches(repo) == ["main"] and prs.calls == []  # nothing was pushed


async def test_a_job_without_changes_fails(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    job = await run_job(repo, "noop")
    assert job.status == "failed" and "nessun file" in job.stop_reason
    assert origin_branches(repo) == ["main"]


async def test_a_job_over_the_time_limit_is_stopped_and_reported(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    repo.settings.build_timeout_minutes = 0.03  # ~2 seconds
    job = await run_job(repo, "sleep")
    assert job.status == "failed" and "tempo massimo" in job.stop_reason
    assert origin_branches(repo) == ["main"]


async def test_too_many_turns_stop_the_job(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    repo.settings.build_max_turns = 5
    job = await run_job(repo, "manyturns")
    assert job.status == "failed" and "passaggi" in job.stop_reason


async def test_a_running_job_is_stopped_by_cancel_and_its_copy_is_removed(
    tracer, repo, monkeypatch
):
    catalog(monkeypatch, repo)
    job = await make_job("sleep")
    claimed = await runner.claim_next()
    task = asyncio.create_task(
        runner.process(
            claimed,
            repo.settings,
            claude_cmd=FAKE,
            remote_for=lambda r: str(repo.origin),
            open_pr=PRs(),
            poll_s=0.2,
        )  # fmt: skip
    )
    work = Path(repo.settings.build_workdir) / f"job-{job.id}"
    for _ in range(50):  # wait until the working copy exists: the program is running
        if work.exists():
            break
        await asyncio.sleep(0.2)
    assert work.exists()
    assert (await builds.cancel(job.id)).cancel_requested is True
    await asyncio.wait_for(task, 30)
    done = await builds.get_job(job.id)
    assert done.status == "cancelled" and not work.exists()
    assert origin_branches(repo) == ["main"]


async def test_quota_pauses_the_job_and_resume_queues_it_again(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    job = await run_job(repo, "quota")
    assert job.status == "paused_quota"
    assert (Path(repo.settings.build_workdir) / f"job-{job.id}").exists()  # work is kept
    again = await builds.resume(job.id)
    assert again.status == "queued"
    with pytest.raises(ValueError, match="non è in pausa"):
        await builds.resume(job.id)


async def test_a_product_without_build_enabled_is_refused_everywhere(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo, build_enabled=False)
    with pytest.raises(ValueError, match="non è abilitato"):
        await builds.save_spec("Demo", "Titolo", "x" * 60)
    with pytest.raises(ValueError, match="non è abilitato"):
        await draft_spec.draft_spec(
            draft_spec.SpecInput(product="Demo", title="Titolo", text="x" * 60)
        )
    # a job that reached the worker anyway is refused there too
    catalog(monkeypatch, repo)
    job = await make_job("edit")
    catalog(monkeypatch, repo, build_enabled=False)
    claimed = await runner.claim_next()
    await runner.process(claimed, repo.settings, claude_cmd=FAKE, poll_s=0.2)
    assert (await builds.get_job(job.id)).status == "failed"


async def test_orphaned_running_jobs_are_failed_on_restart(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    job = await make_job("edit")
    await runner.claim_next()
    assert await runner.recover_orphans(repo.settings) == 1
    assert (await builds.get_job(job.id)).status == "failed"


async def test_claude_never_sees_the_environment_of_the_worker_in_a_real_job(
    tracer, repo, monkeypatch
):
    catalog(monkeypatch, repo, protected_paths=[])
    monkeypatch.setenv("BUILD_GITHUB_TOKEN", "SECRET-TOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SECRET-KEY")
    job = await run_job(repo, "env")
    seen = json.loads(git(repo.origin, "show", f"{job.branch}:env.txt"))
    assert not {"BUILD_GITHUB_TOKEN", "ANTHROPIC_API_KEY", "DATABASE_URL"} & set(seen)


# ---------- the tools and the confirmation ----------


async def test_start_build_needs_the_confirmation_and_shows_the_whole_spec(
    tracer, make_ctx, repo, monkeypatch
):
    catalog(monkeypatch, repo)
    text = (
        "Obiettivo: esportare in PDF.\nIncluso: pulsante.\nEscluso: stampa.\n" + "dettaglio " * 20
    )
    spec = await builds.save_spec("Demo", "Export PDF", text)
    use = ToolUse("1", "start_build", {"spec_id": spec.id})
    ctx = make_ctx(
        FakeLLMClient([LLMResponse(tool_uses=[use]), LLMResponse(text="Messo in coda.")])
    )
    result = await run_agent("avvia", ctx)

    async def jobs():
        async with db.session_maker()() as s:
            return list((await s.execute(select(BuildJob))).scalars())

    assert result.status == "awaiting_confirmation" and await jobs() == []  # nothing queued yet
    assert text in result.answer and "Non fa merge" in result.answer  # the whole spec, on screen
    await resume_confirmed(ctx, result)
    (job,) = await jobs()
    assert job.status == "queued" and job.spec_id == spec.id


async def test_spec_versions_jobs_cancel_and_notifications(tracer, repo, monkeypatch):
    catalog(monkeypatch, repo)
    spec = await builds.save_spec("Demo", "Prima", "a" * 60)
    again = await builds.save_spec("Demo", "Seconda", "b" * 60, spec.id)
    assert (again.id, again.version, again.status) == (spec.id, 2, "draft")
    job = await builds.enqueue(spec.id)
    assert (await builds.get_spec(spec.id)).status == "approved"
    with pytest.raises(ValueError, match="già il lavoro"):
        await builds.enqueue(spec.id)
    cancelled = await cancel_build.cancel_build(cancel_build.CancelInput(job_id=job.id))
    assert cancelled["status"] == "cancelled"
    with pytest.raises(ValueError, match="già finito"):
        await builds.cancel(job.id)

    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    assert await builds.notify_changes(send) == 1 and "annullato" in sent[0]
    assert await builds.notify_changes(send) == 0  # told once
    assert "Seconda" in (await start_build.describe(start_build.StartInput(spec_id=spec.id)))
