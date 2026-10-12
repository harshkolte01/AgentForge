"""Reasoning effort: how much a model thinks before it answers, chosen per person in the composer (never from `.env`).

Which efforts a model accepts is a property of the model, so it lives here beside the other verified model facts
(`limits.py`). A model that is not listed offers no control at all and is called without the parameter, exactly as before:
guessing the efforts of a model nobody verified would turn a harmless menu into failed runs.
"""
from dataclasses import dataclass
from typing import Final, Literal, get_args

from .limits import is_luna

Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
EFFORTS: Final[tuple[str, ...]] = get_args(Effort)

#: What a person gets until they choose.
DEFAULT_EFFORT: Final = "high"


@dataclass(frozen=True)
class EffortSupport:
    """The efforts a model accepts, lowest first."""

    efforts: tuple[str, ...]
    #: The provider's Chat Completions endpoint takes function calls only without reasoning, so any other effort is sent
    #: through the Responses endpoint instead (see `OpenAIAdapter`).
    tools_need_responses: bool = False


# Source (reviewed 2026-10-12): https://developers.openai.com/api/docs/models/gpt-6-luna. "`reasoning.effort` supports `none`,
# `low`, `medium`, `high`, `xhigh`, and `max`" and "Chat Completions supports function calling only with `reasoning_effort`
# set to `none`".
LUNA = EffortSupport(("none", "low", "medium", "high", "xhigh", "max"), tools_need_responses=True)


def support_for(model: str | None) -> EffortSupport | None:
    """The efforts `model` accepts, or None when the model offers no effort control."""
    return LUNA if is_luna(model) else None


def efforts_for(model: str | None) -> tuple[str, ...]:
    support = support_for(model)
    return support.efforts if support else ()


def default_for(model: str | None) -> str | None:
    """`DEFAULT_EFFORT` when the model takes it, otherwise the model's highest effort; None when it takes none."""
    efforts = efforts_for(model)
    if not efforts:
        return None
    return DEFAULT_EFFORT if DEFAULT_EFFORT in efforts else efforts[-1]


def resolve(model: str | None, chosen: str | None) -> str | None:
    """The effort a run uses: the person's choice when this model accepts it, otherwise the default."""
    efforts = efforts_for(model)
    if not efforts:
        return None
    return chosen if chosen in efforts else default_for(model)


def uses_responses(model: str | None, effort: str | None) -> bool:
    """Whether a request at `effort` must go through the Responses endpoint to keep its tools."""
    support = support_for(model)
    return bool(support and support.tools_need_responses and effort not in (None, "none"))


# How much longer than the configured wait a request may stay silent before its first token: a model that thinks longer
# says nothing for longer. (The wait for the next piece of a reply is `llm_timeout_sec`.)
_PATIENCE = {"high": 2.0, "xhigh": 3.0, "max": 4.0}
MAX_WAIT_SEC: Final = 900.0


def read_timeout(base_sec: float, effort: str | None) -> float:
    return min(MAX_WAIT_SEC, base_sec * _PATIENCE.get(effort or "", 1.0))
