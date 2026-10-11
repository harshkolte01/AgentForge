"""The skills that ship with the app: one SKILL.md per skill under `skills/builtin/`, read once and checked like any other skill.

They are packaged files, so every person gets the same ones and an upgrade replaces them. A person can switch one off, or copy it to
their own skills and change the copy; they cannot edit a built-in in place.
"""
from functools import lru_cache
from importlib.resources import files

from .format import SkillDocument, parse_skill_markdown

# The order they are listed in. A file under builtin/ that is not named here, or a name here without a file, is a packaging mistake.
ORDER = ("deep-research", "code-review", "debug-and-fix", "implement-feature", "write-tests", "explore-codebase",
         "pull-request", "write-report", "analyze-data", "write-skill")


@lru_cache(maxsize=1)
def builtin_skills() -> tuple[SkillDocument, ...]:
    folder = files("agentforge.skills").joinpath("builtin")
    found = {item.name.removesuffix(".md") for item in folder.iterdir() if item.name.endswith(".md")}
    if found != set(ORDER):
        raise RuntimeError(f"built-in skill files do not match the list: {sorted(found ^ set(ORDER))}")
    skills = []
    for name in ORDER:
        skill = parse_skill_markdown(folder.joinpath(f"{name}.md").read_text(encoding="utf-8"))
        if skill.name != name:
            raise RuntimeError(f"built-in skill file {name}.md is named {skill.name}")
        skills.append(skill)
    return tuple(skills)


@lru_cache(maxsize=1)
def builtin_by_name() -> dict[str, SkillDocument]:
    return {skill.name: skill for skill in builtin_skills()}
