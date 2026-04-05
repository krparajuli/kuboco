#!/usr/bin/env bash
# shell.sh — interactive session opened by ttyd for each browser connection.
#
# Loads the full development environment, changes to the home directory,
# then starts zellij. If zellij is unavailable, falls back to a plain
# interactive bash shell.

# Source the login profile to pick up all toolchain PATH entries
# (/etc/profile.d/webclaude.sh covers Go, Rust, and Node.js).
[ -f /etc/profile ] && . /etc/profile

# Source nvm explicitly — some environments skip profile.d sourcing.
export NVM_DIR=/opt/nvm
[ -s "$NVM_DIR/nvm.sh" ]          && . "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"

# Start in the home directory.
cd /root

if command -v zellij &>/dev/null; then
    exec zellij --layout /root/.config/zellij/layouts/ironclaude.kdl
else
    exec bash -i
fi
