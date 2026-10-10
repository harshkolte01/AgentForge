"""Turn a conversation's measured usage into the context status shown beside the composer.

Each turn starts a fresh agent that is given a bounded digest of the conversation, so the window is
filled by two things: what every turn starts with (instructions, tool schemas and that digest) and
how far the agent's own working context grew during the latest run (tool results). The headline is
the larger of the two. Starting context is estimated (about four characters per token); the latest
run's peak is the provider's reported input size for its largest request.
"""
import json
import math
from functools import lru_cache

from agentforge.contracts.context import ContextStatus, LastRunContext

CHARS_PER_TOKEN = 4
NOTICE_PERCENT = 50
HIGH_PERCENT = 80
# Shape of the brief around the task: ids, limits, role header, empty previews.
BRIEF_OVERHEAD_CHARS = 700

EMPTY_USAGE = {"history_chars": 0, "messages_total": 0, "messages_in_full": 0, "messages_condensed": 0, "last_run": None}


def estimate_tokens(characters: int) -> int:
    return math.ceil(max(0, characters) / CHARS_PER_TOKEN)


@lru_cache(maxsize=1)
def entry_overhead_tokens() -> int:
    """Tokens every turn begins with before any conversation: the platform rules, the front-door
    agent's instructions and the schemas of the tools it is offered."""
    from agentforge.tools.catalog import DESCRIPTIONS, clean_schema
    from agentforge.tools.contracts import ARGUMENT_MODELS

    from .prompt_builder import PLATFORM_INSTRUCTIONS
    from .registry import load_builtin
    from .runtime import submit_tool

    definition = load_builtin("intent-router", 3)
    tools = [{"name": name, "description": DESCRIPTIONS[name], "parameters": clean_schema(ARGUMENT_MODELS[name].model_json_schema())}
             for name in definition.allowed_tools]
    tools.append(submit_tool(definition))
    text = PLATFORM_INSTRUCTIONS + definition.system_prompt + json.dumps(tools, ensure_ascii=False)
    return estimate_tokens(len(text) + BRIEF_OVERHEAD_CHARS)


def level_for(percent: float) -> str:
    return "high" if percent >= HIGH_PERCENT else "notice" if percent >= NOTICE_PERCENT else "ok"


def build_status(*, model, provider, window, max_output, usage, history_budget_chars) -> ContextStatus:
    """`usage` is SessionRepository.context_usage output (or EMPTY_USAGE for a conversation not yet created)."""
    baseline = entry_overhead_tokens()
    history = estimate_tokens(usage["history_chars"]) if usage["messages_total"] else 0
    start = baseline + history
    last = usage.get("last_run")
    peak = last["peak_input_tokens"] if last else 0
    used = max(start, peak)
    percent = round(min(100.0, used / window * 100), 1) if window else 0.0
    omitted = max(0, usage["messages_total"] - usage["messages_in_full"] - usage["messages_condensed"])
    return ContextStatus(
        model=model, provider=provider, window_tokens=window, max_output_tokens=max_output,
        level=level_for(percent) if window else "unknown", percent=percent, used_tokens=used,
        used_basis="last_run_peak" if peak > start else "next_turn",
        baseline_tokens=baseline, history_tokens=history, history_budget_tokens=estimate_tokens(history_budget_chars),
        last_run=LastRunContext(**last) if last else None,
        messages_total=usage["messages_total"], messages_in_full=usage["messages_in_full"],
        messages_condensed=usage["messages_condensed"], messages_omitted=omitted)
