#!/bin/sh
# Trusted remote E2B template build step. Never a task/repository command.
# Official checksums: https://nodejs.org/dist/v22.23.3/SHASUMS256.txt
set -eu

node_version=22.23.3
case "$(dpkg --print-architecture)" in
    amd64)
        node_arch=x64
        node_sha256=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de
        ;;
    arm64)
        node_arch=arm64
        node_sha256=a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f
        ;;
    *) echo "unsupported Node.js architecture" >&2; exit 1 ;;
esac

node_archive="/tmp/agentforge-node-${node_version}.tar.xz"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-${node_arch}.tar.xz" \
    --output "$node_archive"
printf '%s  %s\n' "$node_sha256" "$node_archive" | sha256sum --check --status
tar --extract --xz --file "$node_archive" --directory /usr/local \
    --strip-components=1 --no-same-owner
rm "$node_archive"

# Preserve the provider's fixed /usr/bin argv paths. npm's complete bundled
# module tree and its relative /usr/local/bin npm/npx links come from the archive.
ln -s /usr/local/bin/node /usr/bin/node
ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm
ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx
test "$(/usr/bin/node --version)" = "v${node_version}"
/usr/bin/npm --version
