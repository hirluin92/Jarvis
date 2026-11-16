from pathlib import Path

import yaml
from pydantic import BaseModel

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
DRAFTS_DIR = SCENARIOS_DIR / "drafts"  # not picked up by the suite until moved up one level


class Scenario(BaseModel):
    name: str
    input: str  # "@report" means the real morning-report prompt
    expected_status: str = "completed"  # e.g. awaiting_confirmation for a confirm-level tool
    catalog: str | None = None  # fixture catalog file name in evals/fixtures
    extra_tools: list[str] = []  # eval-only tools from evals/fixture_tools.py (poisoned sources)
    memories: list[str] = []  # facts saved before the run (memory is wiped between scenarios)
    expected_tools: list[str] = []
    forbidden_tools: list[str] = []
    answer_must_contain: list[str] = []
    rubric: str | None = None  # graded by a judge model, score in [0, 1]
    min_score: float = 0.7  # pass threshold for the rubric score
    note: str | None = None


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def write_scenario(scenario: Scenario, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{scenario.name}.yaml"
    data = scenario.model_dump(exclude_none=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8"
    )
    return path
