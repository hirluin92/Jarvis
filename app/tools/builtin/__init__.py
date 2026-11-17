from app.tools.builtin import (
    add_evidence,
    add_todo,
    build_status,
    cancel_build,
    cancel_reminder,
    create_github_issue,
    create_note,
    decide_opportunity,
    draft_email,
    draft_spec,
    draft_validation_kit,
    echo_confirm_demo,
    forget,
    get_current_time,
    get_opportunity,
    get_product_brief,
    github_activity,
    http_health_check,
    list_decisions,
    list_opportunities,
    list_reminders,
    log_decision,
    merge_pull_request,
    read_webpage,
    recall,
    remember,
    save_opportunity,
    save_session_summary,
    score_opportunity,
    search_docs,
    send_email,
    send_to_telegram,
    set_advisor_mode,
    set_reminder,
    site_status,
    start_build,
    start_validation,
    trigger_deploy,
    update_product_brief,
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
        draft_spec,
        start_build,
        build_status,
        cancel_build,
        send_to_telegram,
        set_advisor_mode,
        search_docs,
        get_product_brief,
        update_product_brief,
        log_decision,
        list_decisions,
        save_session_summary,
        save_opportunity,
        add_evidence,
        score_opportunity,
        list_opportunities,
        get_opportunity,
        draft_validation_kit,
        start_validation,
        decide_opportunity,
    ):
        reg.register(mod.TOOL)
    return reg


# Tools of the advisor mode only: leaving them out of everyday conversations keeps the prompt small.
ADVISOR_ONLY = frozenset(
    {
        "search_docs",
        "get_product_brief",
        "update_product_brief",
        "log_decision",
        "list_decisions",
        "save_session_summary",
        "save_opportunity",
        "add_evidence",
        "score_opportunity",
        "list_opportunities",
        "get_opportunity",
        "draft_validation_kit",
        "start_validation",
        "decide_opportunity",
    }
)


def everyday_registry() -> ToolRegistry:
    return default_registry().without(ADVISOR_ONLY)


NICHE_TOOLS = frozenset(
    {
        "save_opportunity",
        "add_evidence",
        "score_opportunity",
        "list_opportunities",
        "get_opportunity",
        "draft_validation_kit",
        "start_validation",
        "decide_opportunity",
    }
)
# what a conversation about niches needs: research, niche tools, decisions, a way to reach the user
NICHE_WORKSPACE_TOOLS = NICHE_TOOLS | {
    "web_search",
    "read_webpage",
    "get_current_time",
    "log_decision",
    "list_decisions",
    "save_session_summary",
    "send_to_telegram",
    "set_advisor_mode",
    "search_docs",
}


def advisor_registry(product: str) -> ToolRegistry:
    """The tools of an advisor conversation. Two smaller lists instead of one big one: the free
    model only accepts about 8000 tokens per request, tools included."""
    full = default_registry()
    if product.strip().lower() == "nicchie":
        return full.only(NICHE_WORKSPACE_TOOLS)
    return full.without(NICHE_TOOLS)
