# Single-EC2 release configuration

This configuration hosts nginx, Next.js, FastAPI, one worker and PostgreSQL on
one Linux EC2 instance. Managed E2B remains the coding execution boundary; the
host never runs submitted repositories. No AWS account, instance, domain or
deployment was created. Docker Compose rendering is locally checkable without a
daemon; image builds, TLS and restart behavior need deployment-host qualification.

## Host and storage prerequisites

Use a patched Linux host with Docker Engine and Compose v2. Start with enough RAM
for a Next.js build and PostgreSQL; measure before choosing instance size. Bind
only TCP 80/443 publicly. Restrict SSH to an operator address or use Session
Manager. Do not publish 3000/8000/5432 or mount the Docker socket in application
containers. Security groups are the host's network boundary; see
[AWS security groups](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-security-groups.html).

Mount a persistent encrypted EBS volume at `/srv`, including
`/srv/agentforge/postgres`, `/srv/agentforge/artifacts` and a private backup
directory. Configure encryption before storing data, retain the data volume on
instance termination, monitor free space, and keep encrypted off-instance backups.
[EBS encryption](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-encryption.html)
protects the storage layer; application credentials still need separate access
control. The current artifact implementation requires POSIX filesystem behavior.
API and worker share the same artifact mount and UID/GID 10001; create the artifact
directory with owner `10001:10001`, mode `0700`. Backups contain user material.

Provision a DNS name and trusted certificate using your chosen certificate
manager. Install `fullchain.pem` and `privkey.pem` beneath `/etc/agentforge/tls`.
nginx redirects HTTP to HTTPS and uses TLS1.2/1.3. Certificate renewal is an
operator responsibility: replace files atomically, run `nginx -t` and reload the
proxy before expiry. The supplied proxy does not provide an HTTP ACME challenge
handler; use DNS validation or adapt the proxy for the selected certificate tool.

## Separate operator and runtime configuration

Copy `deploy.env.example` to `/etc/agentforge/deploy.env` and
`runtime.env.example` to `/etc/agentforge/runtime.env`, both mode `0600`. Replace
every placeholder. The files deliberately fail to launch as shipped. Never bake
provider keys, OAuth secrets, TLS keys or environment files into images.

`deploy.env` contains Compose interpolation, database bootstrap/admin URLs and a
distinct runtime password. It is **not** passed into API/worker containers.
`runtime.env` contains only runtime credentials and app settings. The database
initialization script creates `agentforge_admin` as the PostgreSQL administrator
and `agentforge` as a non-superuser with DML permissions and no schema creation.
Only the explicit `migration` service receives administrator URLs; tables created
by that administrator grant runtime access via default privileges. A pre-existing
database does not rerun init scripts: review and apply equivalent role/grant setup
before using this configuration with existing data.

The Docker subnet is `172.29.237.0/24`; nginx has `172.29.237.10`. FastAPI trusts
forwarded headers only from that address. If this conflicts with host networking,
change both the subnet/static address and `--forwarded-allow-ips` together. All
other app ports remain private. Access logs and OAuth-route error logs are disabled
so callback query credentials are not logged. The browser and OAuth callback use one HTTPS origin.
GitHub tokens are encrypted in the database with the separately retained token
encryption key. Rotating that key requires a token re-encryption/reconnect plan.

## Explicit first launch or upgrade

Run from a reviewed checkout at the commit recorded as `RELEASE_TAG`:

```sh
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml config --quiet
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml build --pull
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml up -d db
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml run --rm migration
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml up -d api worker web proxy
```

Do not run plain `compose config` in recorded output: expanded configuration
contains secrets. `config --quiet` checks validity without printing values. On an
upgrade, close intake, stop app writers and take the consistent backup below
before migrations; DDL never runs at API/worker startup. Preserve migration output
and reviewed image digests as private release evidence. Pin resolved image digests
on the deployment host after reviewing the images and vulnerability scan. The
current exact upstream tags are Python3.12.15, Node24.21.0, PostgreSQL17.11 and
nginx1.30.5, verified against the
[official image inventory](https://github.com/docker-library/official-images/tree/master/library).
The local development checks ran on older Python3.12.12/Node24.12.0; these new
container runtimes still require build/smoke qualification.

AgentForge has no usage limits, so sign-in is the access control: list the people
who may use the deployment in `GITHUB_ALLOWED_LOGINS` and keep
`PUBLIC_SIGNUP_ENABLED=false`. Keep `ENABLE_GITHUB_WRITES=false` until OAuth,
repository selection, expiry/revocation and approval-bound delivery have passed
on a controlled live repository. Create the GitHub App as described in
[docs/github-app.md](../../docs/github-app.md). Configure provider keys and verified model
capabilities/prices, Neatlogs masking and the qualified E2B template before task
intake. Monitor provider-side spend too; the ledger cannot observe calls outside
AgentForge. The API refuses to start in production with a non-HTTPS URL or origin,
or without `AUTH_MODE=github_app` and its GitHub settings; the error names the
settings, never their values.

## Health and streaming qualification

`/api/v1/health/live` checks API process liveness; `/ready` checks database
connectivity. Neither proves the worker is ready. Run:

```sh
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml exec -T api python -m agentforge.operations.check
docker compose --env-file /etc/agentforge/deploy.env -f infra/deploy/compose.yaml ps
```

The read-only operational snapshot reports revision,
queued/running/expired lease counts and latest job heartbeat age. An idle worker
has no fresh job heartbeat, so its process state stays `not_observed`. Inspect
worker logs and run a controlled authenticated canary task to establish processing.
This command does not claim table-schema compatibility or test artifact writes.

nginx disables API response buffering/cache and preserves SSE cookies and cursor
headers. The normal upstream idle timeout is 75s, above the stream keepalive
interval. Upstream retries are disabled to avoid repeat writes. GitHub sign-in
(`/api/v1/auth/github/start|callback`) is rate limited per client IPv4 address or
IPv6 /64; the API's own limit is a DB-wide backstop.
Verify authenticated event arrival before task completion, reconnect with the
last cursor, owner denial, logout revocation and cancellation in a browser. Any
additional CDN/load balancer must preserve streaming and use a sufficient idle
timeout; see [Next.js self-hosting](https://nextjs.org/docs/app/guides/self-hosting)
and [nginx buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).

## Backup, restore and retention

`bash infra/deploy/backup.sh /etc/agentforge/deploy.env /srv/agentforge /srv/agentforge-backups`
briefly stops the initially running proxy/web/API/worker services, creates a
custom-format PostgreSQL dump plus the matching private artifact archive and
checksums, then starts only those same services even if a backup fails. A worker
that was stopped stays stopped. The DB remains running; app writers are quiesced to keep DB
references consistent with files. Do not run external writers during this window.
Inspect the exit code and checksums before treating a directory as a successful
backup. Keep the encryption key and credentials in a separately access-controlled
secret backup; the script deliberately excludes them.

Copy each verified set to encrypted off-instance storage and take coordinated
encrypted EBS snapshots after quiescing writers. Configure an operator-reviewed
retention policy, initially seven daily and four weekly backup sets. No automatic
deletion is implemented. Monitor backup age, disk capacity, certificate expiry,
queued-job age, expired leases, task failures, provider spend and missing artifacts.
Use [PostgreSQL dump guidance](https://www.postgresql.org/docs/current/backup-dump.html)
and [EBS snapshot guidance](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-creating-snapshot.html).

Restore into a **new** isolated disk and Compose project first, with public ports
disabled and provider/write keys absent. Verify `SHA256SUMS`, inspect archive
members, unpack artifacts under the new data root and restore owner `10001:10001`.
Start only the fresh DB, then restore:

```sh
# Use the recovery project's own compose/env paths, never the serving project.
docker compose --env-file /etc/agentforge-recovery/deploy.env -f /opt/agentforge-recovery/infra/deploy/compose.yaml exec -T db pg_restore --username agentforge_admin --dbname agentforge --no-owner --no-privileges --exit-on-error < /secure-backup/database.dump
```

Restores using `--no-privileges` omit table grants, so explicitly grant runtime
SELECT/INSERT/UPDATE/DELETE on all public tables and USAGE/SELECT on all public
sequences as `agentforge_admin` before testing. Compare restored revision, row
counts, artifact hashes and owner-only downloads. Verify a controlled task and
restart before cutover. Never overwrite the live data directory as a restore test.
Record backup/restore times; no RPO/RTO is promised until this drill succeeds.

For service recovery, stop intake, inspect unknown paid/external operations, then
restart worker/API. Do not clear ledgers or replay unknown writes. Lease fencing
and durable task state resume eligible work; ambiguous paid child calls stay conservative
and GitHub delivery reconciles its operation ledger. Roll back application images
only when the previous revision is schema-compatible; otherwise restore the
verified DB/artifact pair to a new volume. Never run `compose down --volumes` on
the serving data or backups.
