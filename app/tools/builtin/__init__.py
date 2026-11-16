from app.tools.builtin import echo_confirm_demo, get_current_time, http_health_check
from app.tools.registry import ToolRegistry


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for mod in (get_current_time, http_health_check, echo_confirm_demo):
        reg.register(mod.TOOL)
    return reg
