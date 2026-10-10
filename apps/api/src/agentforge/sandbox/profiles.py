"""Server-owned argv only. No model-supplied command strings or arguments."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class CommandProfile:
    id: str
    argv: tuple[str, ...]
    test: bool


PROFILES = MappingProxyType({
    "python_unittest": CommandProfile("python_unittest", ("/usr/local/bin/python3", "-I", "-m", "unittest", "discover", "-s", ".", "-v"), True),
    "python_pytest": CommandProfile("python_pytest", ("/usr/local/bin/python3", "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider"), True),
    "node_test": CommandProfile("node_test", ("/usr/bin/node", "--test"), True),
    "node_npm_test": CommandProfile("node_npm_test", ("/usr/bin/npm", "test"), True),
    "node_build": CommandProfile("node_build", ("/usr/bin/npm", "run", "build"), False),
})


def get_profile(profile_id: str) -> CommandProfile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise ValueError("unknown_command_profile") from None
