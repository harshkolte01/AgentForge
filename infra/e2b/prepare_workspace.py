"""Trusted template helper. Run ONLY inside a freshly allocated E2B sandbox."""

import base64
import hashlib
import json
import os
import pathlib
import pwd
import stat
import subprocess


def main() -> None:
    if os.getuid() != 0:
        raise RuntimeError("template_preparation_requires_root")
    # E2B's API currently rejects IPv6 deny CIDRs. Disable IPv6 inside the VM
    # before any repository process runs; never rely on today's routing table.
    ipv6 = pathlib.Path("/proc/sys/net/ipv6/conf")
    if ipv6.exists():
        controls = sorted(ipv6.glob("*/disable_ipv6"))
        if not controls or not (ipv6 / "all/disable_ipv6").exists() or not (ipv6 / "default/disable_ipv6").exists():
            raise RuntimeError("sandbox_ipv6_controls_missing")
        for control in controls:
            control.write_text("1")
        if any(control.read_text().strip() != "1" for control in controls):
            raise RuntimeError("sandbox_ipv6_disable_failed")
        if pathlib.Path("/proc/net/if_inet6").read_text().strip():
            raise RuntimeError("sandbox_ipv6_address_remains")
    forge = pwd.getpwnam("forge")
    if forge.pw_uid != 10001 or forge.pw_gid != 10001:
        raise RuntimeError("template_user_invalid")
    source = pathlib.Path("/tmp/agentforge-import.json")
    if source.is_symlink() or source.stat().st_size > 15_000_000:
        raise ValueError("invalid_manifest")
    manifest = json.loads(source.read_bytes())
    if set(manifest) != {"version", "revision", "profile_id", "files"} or manifest["version"] != 1:
        raise ValueError("invalid_manifest")
    encoded = manifest["files"]
    if not isinstance(encoded, dict) or len(encoded) > 2000:
        raise ValueError("invalid_manifest")
    root = pathlib.Path("/workspace/repo")
    root.mkdir(parents=True, exist_ok=False, mode=0o755)
    digest = hashlib.sha256(b"agentforge-workspace-v1\0")
    total = 0
    for path, value in sorted(encoded.items()):
        parts = path.split("/")
        if (not path or path.startswith("/") or "\\" in path
                or any(p in ("", ".", "..") for p in parts)
                or any(ord(c) < 32 for c in path)):
            raise ValueError("invalid_workspace_path")
        data = base64.b64decode(value, validate=True)
        total += len(data)
        if len(data) > 1_000_000 or total > 10_000_000:
            raise ValueError("workspace_limit")
        name = path.encode()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        destination = root.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with destination.open("xb") as handle:
            handle.write(data)
        destination.chmod(0o444)
    if digest.hexdigest() != manifest["revision"]:
        raise ValueError("workspace_revision_mismatch")
    source.unlink()
    # Separate bounded scratch mount; no writable source tree or unbounded /tmp.
    scratch = pathlib.Path("/workspace/scratch")
    scratch.mkdir(mode=0o700)
    subprocess.run(("/usr/bin/mount", "-t", "tmpfs", "-o",
                    "size=64m,nosuid,nodev,noexec,uid=10001,gid=10001,mode=700",
                    "tmpfs", str(scratch)), check=True, timeout=10,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if manifest["profile_id"] == "node_build":
        for name in ("dist", "build", "out", ".next", "coverage"):
            destination = root / name
            if destination.exists():
                raise ValueError("build_output_collides_source")
            output = scratch / name
            output.mkdir(mode=0o700)
            os.chown(output, 10001, 10001)
            destination.symlink_to(output, target_is_directory=True)
    for temporary in ("/tmp", "/var/tmp"):
        os.chmod(temporary, 0o755)
    for directory, _, _ in os.walk(root):
        os.chmod(directory, 0o555)
    # Root and all parents must be immutable to the execution UID.
    for path in (pathlib.Path("/workspace"), pathlib.Path("/opt/agentforge")):
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError("template_directory_writable")
    print(f"prepared:{digest.hexdigest()}")


if __name__ == "__main__":
    main()
