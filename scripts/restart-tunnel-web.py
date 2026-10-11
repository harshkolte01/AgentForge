"""Restart only the tunnel's frontend using a staged, immutable build.

Run from an unrestricted local terminal. Does not restart or reconfigure the
tunnel, gateway, API, worker, database, or authentication.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/tunnel"


def output(args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def main():
    candidate = json.loads((LOCAL / "frontend-candidate.json").read_text())
    release = Path(candidate["directory"]).resolve()
    assert release.is_relative_to(LOCAL / "frontend-releases"), "Invalid release directory"
    assert (release / ".next/BUILD_ID").read_text().strip() == candidate["build_id"], "Build identity mismatch"
    assert (release / ".next/required-server-files.json").is_file(), "Incomplete production build"
    node = shutil.which("node")
    assert node, "Node.js is required"
    binary = ROOT / "apps/web/node_modules/next/dist/bin/next"
    assert binary.is_file(), "Next.js is unavailable"

    listeners = output(["lsof", "-t", "-iTCP:3000", "-sTCP:LISTEN"]).splitlines()
    if len(set(listeners)) > 1:
        raise SystemExit("Multiple frontend listeners found; nothing was stopped.")
    if listeners:
        pid = int(listeners[0])
        fields = output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).splitlines()
        directory = next((Path(line[1:]).resolve() for line in fields if line.startswith("n")), None)
        if directory != ROOT / "apps/web" and not (directory and directory.is_relative_to(LOCAL / "frontend-releases")):
            raise SystemExit("Port 3000 is not owned by this frontend; nothing was stopped.")
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if str(pid) not in output(["lsof", "-t", "-iTCP:3000", "-sTCP:LISTEN"]).splitlines():
                break
            time.sleep(0.1)
        else:
            raise SystemExit("The frontend did not stop cleanly; no second process was started.")

    with (LOCAL / "web.log").open("ab") as log:
        process = subprocess.Popen([node, str(binary), "start", "--port", "3000"],
            cwd=release, env={**os.environ, "API_ORIGIN": "http://127.0.0.1:8000"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (LOCAL / "web.pid").write_text(f"{process.pid}\n")
    for _ in range(40):
        if process.poll() is not None:
            raise SystemExit("Frontend exited. See .local/tunnel/web.log.")
        try:
            with urllib.request.urlopen("http://127.0.0.1:3000/chat", timeout=2) as response:
                if response.status == 200:
                    print("Frontend healthy on port 3000. Refresh your existing tunnel tab.")
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit("Frontend did not become healthy. See .local/tunnel/web.log.")


if __name__ == "__main__":
    main()
