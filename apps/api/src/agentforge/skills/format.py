"""What a skill is, and the rules every skill (built in or written by a person) must meet before it can be saved or offered.

A skill is a name, a one-paragraph description and a body of instructions, written as a `SKILL.md` file:

    ---
    name: code-review
    description: Review code changes and report prioritized findings. Use when asked to review a diff or pull request.
    ---
    # Code review
    ...

The description is the only thing the agent sees before it decides to load a skill, so it must say what the skill does and when to use
it. The instructions are loaded in one tool result, so their size is bounded by what that result can carry.
"""
import json
import re
from dataclasses import dataclass

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
NAME_MIN, NAME_MAX = 3, 40
DESCRIPTION_MIN, DESCRIPTION_MAX = 10, 300
INSTRUCTIONS_MIN, INSTRUCTIONS_MAX = 20, 10_000
# A tool result must stay under 16,000 bytes as JSON (see tools/gateway.py); the instructions are most of it. Counted as JSON text, so
# newlines and quotes (two bytes each once escaped) count for what they cost.
INSTRUCTIONS_JSON_BYTES = 13_000


class SkillInvalid(ValueError):
    """A skill that cannot be saved: the field at fault and a sentence a person can act on."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field, self.message = field, message


@dataclass(frozen=True)
class SkillDocument:
    name: str
    description: str
    instructions: str


def clean_name(value: object) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not NAME_MIN <= len(name) <= NAME_MAX or not NAME_PATTERN.fullmatch(name):
        raise SkillInvalid("name", f"The name must be {NAME_MIN}-{NAME_MAX} characters: lowercase letters, digits and single hyphens, "
                                    "starting with a letter (for example review-contract).")
    return name


def clean_description(value: object) -> str:
    """One line of printable text: a description that runs over several lines is joined."""
    text = " ".join("".join(char if char.isprintable() or char in "\n\t" else " " for char in value).split()) if isinstance(value, str) else ""
    if not DESCRIPTION_MIN <= len(text) <= DESCRIPTION_MAX:
        raise SkillInvalid("description", f"The description must be {DESCRIPTION_MIN}-{DESCRIPTION_MAX} characters. Say what the skill does "
                                          "and when to use it: it is all the agent sees before it decides to load the skill.")
    return text


def clean_instructions(value: object) -> str:
    """Instructions with line endings normalized and control characters removed, or SkillInvalid."""
    if not isinstance(value, str):
        raise SkillInvalid("instructions", "Write the instructions the agent should follow.")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or char.isprintable())
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if not INSTRUCTIONS_MIN <= len(text.strip()) <= INSTRUCTIONS_MAX:
        raise SkillInvalid("instructions", f"The instructions must be {INSTRUCTIONS_MIN}-{INSTRUCTIONS_MAX:,} characters.")
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > INSTRUCTIONS_JSON_BYTES:
        raise SkillInvalid("instructions", "The instructions are too long once saved (many quotes, line breaks or non-English characters take more "
                                           "room). Shorten them.")
    return text


def validated(name: object, description: object, instructions: object) -> SkillDocument:
    return SkillDocument(clean_name(name), clean_description(description), clean_instructions(instructions))


def parse_skill_markdown(source: str) -> SkillDocument:
    """A `SKILL.md` (front matter with name and description, then the instructions) as a validated skill. Raises SkillInvalid."""
    text = source.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillInvalid("instructions", "A SKILL.md starts with a line of three dashes, then name: and description:, then three dashes.")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        raise SkillInvalid("instructions", "The front matter is not closed with a line of three dashes.") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator or key != key.strip() or not key:
            raise SkillInvalid("instructions", f"The front matter line “{line.strip()[:60]}” is not in the form key: value.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip().lower()] = value
    return validated(fields.get("name"), fields.get("description"), "\n".join(lines[end + 1:]))


def format_skill_markdown(skill: SkillDocument) -> str:
    """The `SKILL.md` text of a skill, which parse_skill_markdown reads back."""
    return f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n{skill.instructions}\n"
