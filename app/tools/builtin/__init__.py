from app.tools.builtin import (
    add_todo,
    create_github_issue,
    create_note,
    echo_confirm_demo,
    get_current_time,
    github_activity,
    http_health_check,
    merge_pull_request,
    site_status,
)
from app.tools.registry import ToolRegistry


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for mod in (
        get_current_time,
        http_health_check,
        echo_confirm_demo,
        site_status,
        github_activity,
        create_note,
        add_todo,
        create_github_issue,
        merge_pull_request,
    ):
        reg.register(mod.TOOL)
    return reg
