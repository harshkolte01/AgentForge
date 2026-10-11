"""Which tool calls a person is shown as activity.

One definition, used by the gateway when it decides whether to emit lifecycle events and by the read APIs when they
count and list calls, so the live timeline and the saved counts cannot disagree.
"""

# The model correcting its own malformed, repeated or over-limit call is not a step the person needs to see.
SELF_CORRECTION_CODES = ("SCHEMA_INVALID", "DUPLICATE_CALL", "TOOL_LIMIT_REACHED")

# `read_task_input` is the run reading its own request; "unknown" is how a call to an unregistered or
# unauthorized name is stored (the model's text never becomes a tool name).
HIDDEN_TOOLS = frozenset({"read_task_input", "unknown"})


def is_visible_call(tool_name: str, error_code: str | None = None) -> bool:
    """`tool_name` is the stored name, so an unauthorized name already reads "unknown"."""
    return tool_name not in HIDDEN_TOOLS and error_code not in SELF_CORRECTION_CODES
