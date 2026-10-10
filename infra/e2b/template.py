"""Operator-invoked remote E2B template build. Never called by API or tests.

Running this script creates paid remote infrastructure and requires operator
authorization. Qualify the resulting unique template before selecting it.
"""

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path


def recipe():
    from e2b import Template

    template = (
        Template(file_context_path=str(Path(__file__).resolve().parent))
        .from_python_image("3.12-slim-bookworm")
        .run_cmd("apt-get update && apt-get install -y --no-install-recommends "
                 "ca-certificates curl xz-utils util-linux libstdc++6 libatomic1 "
                 "&& rm -rf /var/lib/apt/lists/*", user="root")
        .copy("install_node.sh", "/opt/agentforge-install-node.sh", user="root:root", mode=0o444)
        .run_cmd("/bin/sh /opt/agentforge-install-node.sh && rm /opt/agentforge-install-node.sh", user="root")
        .run_cmd("python3 -m pip install --no-cache-dir pytest==8.4.2", user="root")
        .run_cmd("groupadd -g 10001 forge && useradd -u 10001 -g 10001 -M -d /nonexistent -s /bin/bash forge "
                 "&& mkdir -p /workspace /opt/agentforge "
                 "&& chmod 755 /workspace /opt/agentforge "
                 "&& find / -xdev -type f -perm /6000 -exec chmod a-s {} +", user="root")
        .copy(["prepare_workspace.py", "run_profile.py"], "/opt/agentforge/", user="root:root", mode=0o444)
        .set_user("forge")
    )
    return template


async def build() -> None:
    from e2b import AsyncTemplate
    from agentforge.settings import get_settings

    settings = get_settings()
    if not settings.e2b_api_key:
        raise RuntimeError("E2B_NOT_CONFIGURED")
    options = dict(api_key=settings.e2b_api_key.get_secret_value(), request_timeout=30, retries=0)
    # Each invocation gets a new name: never silently overwrite another template.
    name = "agentforge-phase4-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    async with asyncio.timeout(600):
        result = await AsyncTemplate.build_in_background(recipe(), name=name, cpu_count=1,
            memory_mb=1024, min_free_disk_mb=0, **options)
        print(f"template_id={result.template_id} build_id={result.build_id} name={name}", flush=True)
        deadline = time.monotonic() + 540
        offset = 0
        while time.monotonic() < deadline:
            status = await AsyncTemplate.get_build_status(result, logs_offset=offset, **options)
            offset += len(status.log_entries)
            print(f"build_status={status.status.value}", flush=True)
            if status.status.value == "ready":
                return
            if status.status.value == "error":
                # SDK errors/logs may contain presigned URLs; emit no raw provider data.
                raise RuntimeError("E2B_TEMPLATE_BUILD_FAILED")
            await asyncio.sleep(10)
        raise TimeoutError("E2B_TEMPLATE_BUILD_TIMEOUT")


def main() -> None:
    try:
        asyncio.run(build())
    except Exception as exc:
        print(f"build_error_type={type(exc).__name__}", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
