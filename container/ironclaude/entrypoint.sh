#!/usr/bin/env bash
# entrypoint.sh — Docker ENTRYPOINT for the webclaude container.
#
# Prepares the runtime environment and starts ttyd, which opens shell.sh
# for every browser connection on port 7681.
set -euo pipefail

# Create the Go workspace directory tree on first boot.
mkdir -p /root/go/{bin,pkg,src}

# Ensure Cargo's bin directory exists so `cargo install` works immediately
# without needing a prior `cargo` invocation.
mkdir -p /opt/cargo/{bin,registry}

exec /usr/local/bin/ttyd \
    --port      "${TTYD_PORT:-7681}" \
    --interface 0.0.0.0 \
    --writable  \
    /shell.sh
