# Phase 4 E2B execution

The API stores repository files as bounded bytes. Each command consumes a fresh
copy in a new E2B sandbox and kills the whole sandbox afterwards. There is no
host checkout, local repository execution, Docker socket, AWS provisioning, or
dependency install during a task. This provider-neutral `SandboxRunner` seam can
support another isolated provider later.

`e2b==2.55.1` was checked against the published PyPI wheel source on 2026-10-10.
The adapter uses the real async SDK. It requests provider IPv4 egress denial,
public ingress, resource-creation retries, and automatic resume; sets a finite
kill TTL; and passes no provider, application, database, or cloud credentials in
sandbox environment variables. `secure=True` is accepted by this SDK for
compatibility; current SDK sandboxes always secure envd access. Live allocation on 2026-10-10 exposed two provider policy details: the API
rejects IPv6 deny CIDRs, and its IPv4 deny policy still permits the link-local
IMDSv2 token endpoint. The trusted preparation now disables and verifies IPv6
per invocation. The launcher installs a fail-closed Linux seccomp filter allowing
only Unix IPC socket creation, denying io_uring and closing inherited non-stdio
file descriptors. This guest enforcement is required in addition to provider
policy; metadata GET returning 401 is insufficient evidence of isolation.

## Template setup (operator action, requires E2B credentials)

`template.py` is a remote E2B builder script, **not an application startup hook**.
It loads the same typed Settings as the API, creates a unique name, disables
SDK retries, and bounds build submission/polling to ten minutes. A timed-out build
is an unknown outcome: inspect the printed template/build IDs before submitting
again; remote build cancellation is not guaranteed by the client deadline.
It uploads only the trusted Node installer and two runtime helper scripts in this directory. It never
uploads the application checkout, `.env`, or credentials. Build with the locked
API environment after reviewing the template, then record the returned
template/build identifiers. A mutable alias alone is not a production pin. Review
the currently resolved base image and package versions and pin their digests for
a release; the example builder is a development template recipe.

The supported toolchain is Python **3.12**, pytest **8.4.2**, and Node.js
**22.23.3** with its bundled npm, on Debian Bookworm. Node comes from the official
Node.js binary distribution, with hardcoded x64/arm64 SHA256 checksums checked
against the release's `SHASUMS256.txt`; Debian's older `nodejs`/`npm` packages are
not installed. This satisfies the current app's Node >=20.9 requirement.
The complete npm module tree is retained under `/usr/local/lib/node_modules`,
and `/usr/bin/node`, `/usr/bin/npm`, and `/usr/bin/npx` point to the official
installed binaries/entrypoints. Updating Node requires reviewing a supported
release and changing both architecture checksums together.

The pinned E2B SDK's Dockerfile converter rejects multi-stage builds and
`COPY --from`. The recipe therefore uses the supported Template SDK with a
fixed remote installation step; it does not require a local Docker daemon.

From the repository root, install the locked API environment, then run the
builder explicitly after adding `E2B_API_KEY` to the root `.env`:

```sh
uv sync --locked --project apps/api
cd apps/api
uv run --env-file ../../.env python ../../infra/e2b/template.py
```

**The final command builds remote infrastructure and can incur E2B charges.**
The builder uses Settings precedence: process environment, then `apps/api/.env`,
then the root `.env`. The explicit `--env-file` is optional for the builder; avoid
it if the API `.env` is intended to override root values.

Record both returned template/build identifiers and set `E2B_TEMPLATE_ID` to the
unique template ID. The current create API resolves IDs/names with optional
**mutable tags**, not a direct immutable build UUID. Do not invent `id:build_id`
syntax. Keep each qualified template version under a unique name and never
rebuild it; rebuilding or retagging requires qualification again. The build ID
record is provenance, not an API-enforced immutable selector.
Keep `E2B_TEMPLATE_QUALIFIED=false` until the following live checks pass; a
successful build alone does not qualify isolation or command behavior.

Before setting `E2B_TEMPLATE_QUALIFIED=true`, verify the resulting template:

- 1 CPU, 1024 MiB memory, bounded provider disk; no cloud IAM identity, secrets,
  external volumes, inherited credential files, or user startup scripts.
- `forge` UID/GID 10001 has no sudo rights, setuid/setgid binaries, supplementary
  privileged groups, or writable system paths. The SDK control-plane agent must
  remain inaccessible without its access token, including from inside the VM.
- `/opt/agentforge` and scripts are root-owned and immutable to `forge`.
- Trusted preparation as root constructs a root-owned read-only source tree,
  checks its uploaded revision, creates a **64 MiB tmpfs scratch** with
  `nosuid,nodev,noexec`, and removes writable `/tmp`/`/var/tmp` access. A failed
  mount/preparation is an execution failure; there is no writable fallback.
- The SDK invokes only the fixed root-owned launcher as root. That launcher
  drops supplementary groups, GID and UID to 10001 and verifies zero effective
  capabilities before executing any repository code. Its ancestors remain root
  to prevent same-UID parent injection. Generated code runs as `forge`, with
  a cleared environment, 64-process, 128-FD, 8 MB per-file and CPU limits.
  The launcher requires Linux `PR_SET_NO_NEW_PRIVS` and a persistent seccomp
  filter before executing a profile;
  failure aborts execution.
  Test filesystem/network/metadata denial, resource exhaustion, output overflow,
  cancellation, and sandbox-wide termination against the actual template.

Configure `E2B_API_KEY`, `E2B_TEMPLATE_ID`, and the qualification flag only after
these checks. Missing configuration fails closed with `E2B_NOT_CONFIGURED`.
SDK endpoint overrides are not accepted for this hosted adapter. App health does
not allocate sandboxes or make paid provider calls.

## Execution and evidence

Fixed profiles: `python_unittest`, `python_pytest`, `node_test`, `node_npm_test`,
and `node_build`. No model-supplied argv is accepted. Test/build dependencies must
already exist in the reviewed template. Python pytest disables automatic plugin
loading and caching; Python bytecode writes are disabled. Node scripts are
untrusted and remain isolated. For `node_build`, trusted preparation links the
fixed output directories `dist`, `build`, `out`, `.next`, and `coverage` into the
bounded scratch mount. A source snapshot already containing one of those paths
fails with `build_output_collides_source`; it is never replaced. Other build
output must use `TMPDIR`; attempts to modify source fail. Build output files are not
imported into the durable workspace; Phase 4 returns bounded logs and exit codes.

Evidence binds the uploaded input revision and actual SDK process exit code.
It does not claim a measured post-execution revision, trusted test counts, or
correctness merely because stdout says PASS. The durable baseline is external to
the repository's `.git`; core code guards baseline test/config files. Output is
bounded across both streams, redacted, and overflow invalidates success. Timeout,
cancellation, output overflow, and normal completion all terminate the entire
sandbox, preventing detached children from surviving into future checks.

An allocation timeout is an unknown outcome: no duplicate allocation is issued.
The finite server TTL is the backstop. Known sandboxes are killed under bounded
cancellation shielding; `cleanup_confirmed=false` is not passing evidence.
Provider charge is unknown (`cost_usd=None`); core retains its reserved cap.

## Live qualification on 10 October 2026

The configured development template is `8xr2dnzhv3ffh7khw5u3`, build
`f8c8516a-ea27-43a0-9af9-aa0531418bc7`, with provider-observed **1 CPU,
1024 MiB RAM, 11362 MiB disk**, private visibility and no mounted volumes.
The ignored root `.env` records that template ID and `E2B_TEMPLATE_QUALIFIED=true`;
the existing key and other environment values/comments were preserved. A fresh
Settings read confirmed the values. Running API/workers must restart to reload.

All five fixed profiles executed actual synthetic fixtures through `E2BRunner`:
Python unittest produced exit 1 then exit 0; pytest, Node `--test`, `npm test` and
`npm run build` produced exit 0. Build output went through the fixed scratch-linked
`dist` path. Grouped live checks passed UID/GID 10001, zero capabilities, no
privileged groups, root ancestors, NoNewPrivs, seccomp mode 2, IPv4/IPv6 socket
EPERM, Unix IPC allowance, IPv6 disable verification, root process-environment
denial, source/system/helper/tmp write denial, scratch mount flags and exhaustion,
64-process/128-FD/8 MB-file exhaustion. Direct syscall checks denied ptrace,
process_vm_readv/writev, pidfd_getfd, io_uring and x32 bypass attempts.

Output overflow invalidated success; timeout killed a sandbox containing a
detached child; cancellation propagated after sandbox cleanup. A separate 5-second
provider TTL expired with `SandboxNotFoundException`. All known sandbox IDs were
killed, and the final read-only provider inventory contained **zero active
sandboxes**. Three uniquely named template builds were necessary: the first exposed
the unsupported IPv6 CIDR, the second exposed link-local metadata access, and the
third contains the qualified guest enforcement. Earlier templates were retained,
never selected by Settings or overwritten. No user repository, application DB,
LLM call, AWS resource, commit or push was involved.

The metadata finding was a successful token-endpoint HTTP status only; no
metadata token/body or credentials were read or printed. Final checks assert
socket denial at the kernel boundary and make no metadata request. All 38 offline focused checks, including the 15 provider/workspace checks, pass after these changes.

The configured $0.10 operation reservation remains conservative relative to
published 1-vCPU plus 1-GiB compute rates ($0.0000185/s; at most $0.0111 for the
600-second configured TTL ceiling). This is a compute-rate comparison, not a
measured bill or guarantee about builder/account charges; actual command cost
remains unknown and its reservation is retained. See [current E2B pricing](https://e2b.dev/pricing).

This qualifies the observed Linux x86_64 development template and controlled
fixtures. AArch64, arbitrary dependencies, production image digest pinning,
provider billing, live LLM/GitHub orchestration, hosted tracing, browser flows,
AWS deployment and concurrent worker recovery remain outside this evidence.
Never rebuild/retag this selected version without resetting qualification and
repeating the checks. Current SDK/API build selectors are mutable; the build ID
above is provenance rather than an immutable allocation selector.


Sources: [E2B Python SDK reference](https://docs.e2b.dev/sdk-reference/python-sdk/v2.55.1/sandbox_async),
[official SDK source](https://github.com/e2b-dev/E2B/tree/main/packages/python-sdk),
[published pinned package](https://pypi.org/project/e2b/2.55.1/),
[provider template resolution source](https://github.com/e2b-dev/infra/blob/main/packages/api/internal/handlers/sandbox_create.go),
[Linux seccomp filter contract](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html).
Node sources: [official release checksums](https://nodejs.org/dist/v22.23.3/SHASUMS256.txt),
[official Node 22 image recipe](https://github.com/nodejs/docker-node/blob/main/22/bookworm-slim/Dockerfile).
