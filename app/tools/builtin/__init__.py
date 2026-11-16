from app.tools.builtin import (
    echo_confirm_demo,
    get_current_time,
    github_activity,
    http_health_check,
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
    ):
        reg.register(mod.TOOL)
    return reg
