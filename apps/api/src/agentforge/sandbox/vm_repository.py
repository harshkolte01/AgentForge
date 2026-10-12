"""Loading a repository into the sandbox by letting the VM download it, instead of the API uploading every file.

The API gets a short-lived archive link from GitHub (`ArchiveLink`) and hands it to the VM in a file (never in a command line); the
VM downloads and unpacks the archive into a staging folder and lists what is there (`vm_scan.py`). Which files are left out (secrets,
links, names the workspace cannot hold, letter-case twins) is still decided HERE, by `github_client.skip_reason`, so there is one
set of rules for this path and for the snapshot upload; the VM only deletes what the plan names. The repository's bytes never pass
through the API, so there is no limit on its size or number of files, and no per-file GitHub request.

The commands are built from module constants read at call time, so tests can point them at a temporary folder.
"""
import re
from dataclasses import dataclass
from importlib import resources

from agentforge.integrations.e2b_sandbox import WORKSPACE
from agentforge.integrations.github_client import skip_reason

PYTHON = "python3"
STAGE = "/home/user/.agentforge-stage"          # beside the workspace, so moving the unpacked files into it is instant
SCAN = "/tmp/.agentforge-scan.py"
LIST = "/tmp/.agentforge-list.json"
DROP = "/tmp/.agentforge-drop.json"
CURL_CONFIG = "/tmp/.agentforge-curl"
ARCHIVE = "/tmp/.agentforge-repo.tgz"
# Files of this load that may be left behind by a failure; removed before the snapshot upload takes over.
LEFTOVERS = (STAGE, LIST, DROP, CURL_CONFIG, ARCHIVE, SCAN)

_URL = re.compile(r"https://[^\s\"\\\x00-\x1f\x7f]{1,4000}")


def scan_source() -> str:
    """The text of `vm_scan.py`, which runs in the VM."""
    return resources.files(__package__).joinpath("vm_scan.py").read_text(encoding="utf-8")


def curl_config(url: str) -> str:
    """A curl config file that names `url`. The address is a credential (it is valid for a few minutes and can read one
    archive), so it goes in a file that is deleted at once, not on a command line where `ps` and logs would show it."""
    if not _URL.fullmatch(url):
        raise ValueError("unusable archive link")
    return f'url = "{url}"\n'


def fetch_script() -> str:
    """Downloads the archive, unpacks it into STAGE and writes the listing to LIST. Fails (nonzero) on any error."""
    return (f"set -e; trap 'rm -f {CURL_CONFIG} {ARCHIVE}' EXIT; rm -rf {STAGE} {LIST} {DROP}; mkdir -p {STAGE}; "
            "curl --fail --silent --show-error --location --max-redirs 3 --proto '=https' --proto-redir '=https' "
            f"--retry 3 --retry-delay 2 --retry-connrefused --connect-timeout 20 --config {CURL_CONFIG} --output {ARCHIVE}; "
            f"rm -f {CURL_CONFIG}; "
            f"tar -xzf {ARCHIVE} -C {STAGE} --strip-components=1; rm -f {ARCHIVE}; "
            f"{PYTHON} {SCAN} list {STAGE} {LIST}")


def merge_script() -> str:
    """Deletes what the plan (DROP) names, moves the rest into the workspace and removes every temporary file."""
    return (f"set -e; {PYTHON} {SCAN} drop {STAGE} {DROP}; "
            f"if [ -z \"$(ls -A {WORKSPACE})\" ]; then shopt -s dotglob nullglob; mv {STAGE}/* {WORKSPACE}/; "
            f"else cp -a {STAGE}/. {WORKSPACE}/; fi; "
            f"rm -rf {' '.join(LEFTOVERS)}")


def cleanup_script() -> str:
    return f"rm -rf {' '.join(LEFTOVERS)}; true"


@dataclass(frozen=True)
class SkipPlan:
    drop: list[dict]                       # what the VM deletes, as `vm_scan.py drop` reads it
    skipped: list[tuple[str, str]]         # (path, reason) of each, for the notice to the agent
    kept: int


def plan_skips(listing: object) -> SkipPlan:
    """Which of the VM's listed files stay out of the workspace. Raises ValueError for a listing that is not what the script writes."""
    if not isinstance(listing, list):
        raise ValueError("listing")
    drop: list[dict] = []
    skipped: list[tuple[str, str]] = []
    folded: set[str] = set()
    kept = 0
    for entry in listing:
        if not isinstance(entry, dict) or entry.get("k") not in ("f", "l", "x"):
            raise ValueError("listing entry")
        kind = entry["k"]
        if "h" in entry:
            if not isinstance(entry["h"], str):
                raise ValueError("listing entry")
            bytes.fromhex(entry["h"])  # ValueError when it is not hex
            shown, reason, item = bytes.fromhex(entry["h"]).decode("utf-8", "replace"), "unsafe_name", {"h": entry["h"]}
        else:
            path = entry.get("p")
            if not isinstance(path, str):
                raise ValueError("listing entry")
            shown, item = path, {"p": path}
            if kind == "l":
                reason = "symlink"
            elif kind == "x":
                reason = "special"
            else:
                reason = skip_reason(path)
                if reason is None and path.casefold() in folded:
                    reason = "case_clash"
        if reason:
            drop.append(item)
            skipped.append((shown, reason))
        else:
            folded.add(shown.casefold())
            kept += 1
    return SkipPlan(drop, skipped, kept)
