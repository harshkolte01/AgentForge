"""Turn a conversation's measured usage into the context status shown beside the composer.

Each turn starts a fresh agent with a bounded conversation extract. Its estimated starting input
is separate from the previous run's measured peak; adding the two would double count different
requests. Estimates use the same text rules as the runtime, with protocol/tool overhead included.
"""
import math
from functools import lru_cache

from agentforge.contracts.context import ContextStatus, LastRunContext
from agentforge.llm.limits import input_limit
from agentforge.llm.models import conservative_input_tokens

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
    """Tokens every turn begins with before any conversation: the platform rules, the agent's
    instructions and the schemas of the tools it is offered."""
    from agentforge.tools.catalog import DESCRIPTIONS, clean_schema
    from agentforge.tools.contracts import ARGUMENT_MODELS

    from .prompt_builder import compile_chat
    from types import SimpleNamespace
    from .registry import AGENT_VERSION, load_builtin

    definition = load_builtin("agent", AGENT_VERSION)
    tools = [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name], "parameters": clean_schema(ARGUMENT_MODELS[name].model_json_schema())}}
             for name in definition.allowed_tools]
    messages = compile_chat(definition, SimpleNamespace(context_refs=()), {}, definition.allowed_tools, [])
    return conservative_input_tokens(messages, tools)


def level_for(percent: float) -> str:
    return "high" if percent >= HIGH_PERCENT else "notice" if percent >= NOTICE_PERCENT else "ok"


def build_status(*, model, provider, window, max_output, usage, history_budget_chars, extra_baseline=0) -> ContextStatus:
    """`usage` is SessionRepository.context_usage output (or EMPTY_USAGE for a conversation not yet created).
    `extra_baseline` is what the conversation's plugin tools add to every request."""
    baseline = entry_overhead_tokens() + extra_baseline
    history = usage.get("history_tokens", estimate_tokens(usage["history_chars"])) if usage["messages_total"] else 0
    start = baseline + history
    last = usage.get("last_run")
    coverage = usage.get("coverage", {})
    used = start
    available = input_limit(model, window, max_output)
    percent = round(min(100.0, used / available * 100), 1) if available else 0.0
    omitted = max(0, usage["messages_total"] - usage["messages_in_full"] - usage["messages_condensed"])
    return ContextStatus(
        model=model, provider=provider, window_tokens=window, max_output_tokens=max_output,
        input_limit_tokens=available,
        level=level_for(percent) if available else "unknown", percent=percent, used_tokens=used,
        used_basis="next_turn",
        baseline_tokens=baseline, history_tokens=history, history_budget_tokens=math.ceil(history_budget_chars / 3),
        last_run=LastRunContext(**last) if last else None,
        messages_total=usage["messages_total"], messages_in_full=usage["messages_in_full"],
        messages_condensed=usage["messages_condensed"], messages_omitted=omitted,
        summary_through_ordinal=coverage.get("summary_through_ordinal", 0),
        uncovered_turns=coverage.get("uncovered_turns", 0), retrieval_available=coverage.get("retrieval_available", False),
        snapshot_revision=coverage.get("revision", 0))
