from app.tools.builtin import (
    add_todo,
    cancel_reminder,
    create_github_issue,
    create_note,
    draft_email,
    echo_confirm_demo,
    forget,
    get_current_time,
    github_activity,
    http_health_check,
    list_reminders,
    merge_pull_request,
    read_webpage,
    recall,
    remember,
    send_email,
    set_reminder,
    site_status,
    trigger_deploy,
    web_search,
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
        draft_email,
        send_email,
        remember,
        recall,
        forget,
        set_reminder,
        list_reminders,
        cancel_reminder,
        web_search,
        read_webpage,
        trigger_deploy,
    ):
        reg.register(mod.TOOL)
    return reg
