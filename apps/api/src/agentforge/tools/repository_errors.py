"""What a failure to load the selected repository, or a file left out of it, is called when the agent and the person hear of it.

Codes and wording are written here, never taken from GitHub or from the repository, so nothing untrusted reaches the model.
"""
from agentforge.integrations.github_client import MAX_FILE_BYTES, MAX_FILES, GitHubError

from .errors import ToolError

_MB = 1_000_000
# Shown at most this many at a time; the rest is a count.
LISTED_SKIPPED = 20

_NO_ACCESS = ("The repository or commit could not be read: it may be private, renamed or deleted, or the person's GitHub connection "
              "no longer gives access to it. Tell the person to check the repository and their connection in Settings.")
_TOO_LARGE = (f"The repository is too large to load into the workspace (at most {MAX_FILES:,} files and 10 MB in total). "
              "Tell the person; a smaller repository or a smaller commit is needed.")
_UNAVAILABLE = {
    "github_rate_limited": "GitHub is rate limiting requests right now. Tell the person to try again in a few minutes.",
    "github_read_unavailable": "GitHub did not answer in time. Try once more; if it fails again, tell the person.",
    "github_unavailable": "GitHub did not answer in time. Try once more; if it fails again, tell the person.",
    "github_public_repository_or_ref_not_found": _NO_ACCESS,
    "github_public_repository_required": _NO_ACCESS,
    "github_repository_not_authorized": _NO_ACCESS,
    "github_repository_identity_changed": _NO_ACCESS,
    "github_access_denied": _NO_ACCESS,
    "github_commit_mismatch": "The commit changed while the repository was being loaded. Try again.",
    "github_reauthorization_required": "The person's GitHub connection has ended. Tell them to reconnect GitHub in Settings.",
    "github_authorization_failed": "The person's GitHub connection has ended. Tell them to reconnect GitHub in Settings.",
    "github_app_user_token_required": "The person's GitHub connection has ended. Tell them to reconnect GitHub in Settings.",
    "github_repository_empty": "The repository has no files that can be loaded (every file in it is left out as a secret, link or oversized file).",
}
_LIMITS = {"github_archive_compressed_limit", "github_archive_decompressed_limit", "github_archive_file_count_limit",
           "github_repository_discovery_limit"}

REASONS = {
    "sensitive": "may hold secrets",
    "unsafe_name": "its name cannot be used in the workspace",
    "symlink": "symbolic link",
    "submodule": "git submodule",
    "special": "not a regular file",
    "too_large": f"larger than {MAX_FILE_BYTES // _MB} MB",
    "case_clash": "same name as another file apart from letter case",
}


def repository_error(error: GitHubError) -> ToolError:
    """The typed error for a repository that could not be read. Retryable only when `is_transient` says so."""
    code = str(error)
    if code in _LIMITS:
        return ToolError("REPOSITORY_TOO_LARGE", _TOO_LARGE)
    if code in _UNAVAILABLE:
        return ToolError("REPOSITORY_UNAVAILABLE", _UNAVAILABLE[code])
    return ToolError("REPOSITORY_UNAVAILABLE", "The repository could not be loaded: its archive was not usable. Tell the person.")


def _shown(path: str) -> str:
    """A repository's file name is untrusted text: no control characters (line breaks included) and a bounded length."""
    cleaned = "".join(char if char.isprintable() else "?" for char in str(path))
    return cleaned if len(cleaned) <= 100 else cleaned[:97] + "..."


def describe_skipped(skipped) -> str | None:
    """A note for the agent about the files that were left out of the repository, or None when nothing was."""
    if not skipped:
        return None
    shown = "; ".join(f"{_shown(path)} ({REASONS.get(reason, reason)})" for path, reason in list(skipped)[:LISTED_SKIPPED])
    more = f" and {len(skipped) - LISTED_SKIPPED} more" if len(skipped) > LISTED_SKIPPED else ""
    return (f"{len(skipped)} file(s) of the repository were deliberately not copied into the workspace and do not exist in it: "
            f"{shown}{more}. This is expected (secrets and files that cannot be handled are never loaded); the rest of the repository is complete. "
            "They are not changes: publishing will not delete them.")
