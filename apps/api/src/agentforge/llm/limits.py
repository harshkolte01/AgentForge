"""Verified provider limits, independent of an operator's smaller operational window.

Source (reviewed 2026-10-11): https://developers.openai.com/api/docs/models/gpt-6-luna
Rates remain explicit configuration; this module never guesses prices or credentials.
"""

LUNA_WINDOW = 1_050_000
LUNA_MAX_INPUT = 922_000
LUNA_LONG_CONTEXT_THRESHOLD = 272_000


def is_luna(model: str | None) -> bool:
    return model == "gpt-6-luna" or bool(model and model.startswith("gpt-6-luna-"))


def input_limit(model: str | None, window: int | None, output: int) -> int | None:
    if window is None:
        return None
    usable = max(0, window - output)
    return min(usable, LUNA_MAX_INPUT) if is_luna(model) else usable
