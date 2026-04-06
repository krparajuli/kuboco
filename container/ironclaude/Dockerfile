FROM ubuntu:22.04 AS go-stage
# ─────────────────────────────────────────────────────────────────────────────
# Downloads the official Go toolchain tarball and extracts it to /usr/local/go.
# ─────────────────────────────────────────────────────────────────────────────
ARG GO_VERSION=1.22.5

RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    DEB_ARCH=$(dpkg --print-architecture); \
    case "$DEB_ARCH" in \
        amd64)  GO_ARCH=amd64  ;; \
        arm64)  GO_ARCH=arm64  ;; \
        armhf)  GO_ARCH=armv6l ;; \
        *)      GO_ARCH=$DEB_ARCH ;; \
    esac; \
    curl -fL "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" \
         | tar -xz -C /usr/local; \
    /usr/local/go/bin/go version


FROM ubuntu:22.04 AS rust-stage
# ─────────────────────────────────────────────────────────────────────────────
# Installs the Rust toolchain (cargo, rustc, rustup) into /opt/{rustup,cargo}.
# Using explicit paths so the final image can COPY the directories directly.
# ─────────────────────────────────────────────────────────────────────────────
ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    PATH=/opt/cargo/bin:$PATH

RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --default-toolchain stable \
 && cargo --version \
 && rustc --version


FROM ubuntu:22.04 AS zellij-stage
# ─────────────────────────────────────────────────────────────────────────────
# Downloads the pre-built zellij binary (musl-linked, no libc dependency).
# Supported architectures: amd64, arm64.
# ─────────────────────────────────────────────────────────────────────────────
ARG ZELLIJ_VERSION=0.40.1

RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    DEB_ARCH=$(dpkg --print-architecture); \
    case "$DEB_ARCH" in \
        amd64)  ZJ_ARCH=x86_64-unknown-linux-musl  ;; \
        arm64)  ZJ_ARCH=aarch64-unknown-linux-musl  ;; \
        *)      echo "zellij: unsupported arch $DEB_ARCH" && exit 1 ;; \
    esac; \
    curl -fL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-${ZJ_ARCH}.tar.gz" \
         | tar -xz -C /usr/local/bin; \
    chmod +x /usr/local/bin/zellij; \
    zellij --version


FROM ubuntu:22.04 AS node-stage
# ─────────────────────────────────────────────────────────────────────────────
# Installs nvm and a default Node.js LTS version into /opt/nvm.
# nvm is kept intact so users can run `nvm install` / `nvm use` in the shell.
# ─────────────────────────────────────────────────────────────────────────────
ARG NVM_VERSION=0.39.7
ARG NODE_VERSION=20

ENV NVM_DIR=/opt/nvm

RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/nvm \
 && curl -o- "https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh" \
        | NVM_DIR=/opt/nvm bash \
 && . /opt/nvm/nvm.sh \
 && nvm install "${NODE_VERSION}" \
 && nvm alias default "${NODE_VERSION}" \
 && node --version \
 && npm --version


FROM ubuntu:22.04 AS ttyd-stage
# ─────────────────────────────────────────────────────────────────────────────
# Downloads the ttyd binary from GitHub releases.
# Architecture mapping follows the ttyd release naming convention.
# ─────────────────────────────────────────────────────────────────────────────
ARG TTYD_VERSION=1.7.7

RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    DEB_ARCH=$(dpkg --print-architecture); \
    case "$DEB_ARCH" in \
        amd64)  TTYD_ARCH=x86_64  ;; \
        arm64)  TTYD_ARCH=aarch64 ;; \
        armhf)  TTYD_ARCH=armhf   ;; \
        i386)   TTYD_ARCH=i686    ;; \
        *)      TTYD_ARCH=$DEB_ARCH ;; \
    esac; \
    curl -fL "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${TTYD_ARCH}" \
         -o /usr/local/bin/ttyd; \
    chmod +x /usr/local/bin/ttyd; \
    ttyd --version


# ─────────────────────────────────────────────────────────────────────────────
# Final image
# Assembles all toolchains into a single Ubuntu 22.04 image.
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# System packages — includes Python 3, build tools, and common utilities
RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
 && apt-get install -y --no-install-recommends \
        bash \
        curl \
        wget \
        git \
        vim \
        nano \
        less \
        unzip \
        zip \
        jq \
        python3 \
        python3-pip \
        python3-venv \
        build-essential \
        pkg-config \
        libssl-dev \
        net-tools \
        procps \
        netcat-openbsd \
        iputils-ping \
        dnsutils \
        socat \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Go
COPY --from=go-stage /usr/local/go /usr/local/go

# Rust + Cargo (includes rustup for toolchain management)
COPY --from=rust-stage /opt/rustup /opt/rustup
COPY --from=rust-stage /opt/cargo  /opt/cargo

# Zellij terminal multiplexer
COPY --from=zellij-stage /usr/local/bin/zellij /usr/local/bin/zellij

# nvm + default Node.js
COPY --from=node-stage /opt/nvm /opt/nvm

# Symlink the default node/npm/npx into PATH so they work without sourcing nvm.
# Uses find rather than `nvm which` — nvm alias resolution is unreliable
# in a plain COPY of the nvm directory without a live nvm environment.
RUN node_bin=$(find /opt/nvm/versions/node -maxdepth 3 -name node -type f | sort | tail -1 | xargs dirname) \
 && ln -sf "${node_bin}/node" /usr/local/bin/node \
 && ln -sf "${node_bin}/npm"  /usr/local/bin/npm  \
 && ln -sf "${node_bin}/npx"  /usr/local/bin/npx

# Claude Code CLI
# npm and node are called by full path (found via find) so no nvm sourcing is
# needed — nvm.sh requires bash, but Dockerfile RUN uses /bin/sh by default.
# The symlink exposes claude in /usr/local/bin for zellij pane commands.
RUN node_bin=$(find /opt/nvm/versions/node -maxdepth 3 -name node -type f | sort | tail -1 | xargs dirname) \
 && "${node_bin}/npm" install -g @anthropic-ai/claude-code \
 && ln -sf "${node_bin}/claude" /usr/local/bin/claude

# ttyd
COPY --from=ttyd-stage /usr/local/bin/ttyd /usr/local/bin/ttyd

# ── devusr — UID/GID 30002 ────────────────────────────────────────────────────
RUN groupadd -g 30002 devusr && \
    useradd -u 30002 -g 30002 -m -s /bin/bash devusr && \
    echo 'devusr ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

# Hand ownership of the tool directories to devusr so nvm install,
# cargo install, and rustup work without sudo at runtime.
RUN chown -R devusr:devusr /opt/nvm /opt/cargo /opt/rustup

# ── Environment profile ───────────────────────────────────────────────────────
RUN cat > /etc/profile.d/ironclaude.sh <<'EOF'
# Go
export PATH=/usr/local/go/bin:$PATH
export GOPATH=/home/devusr/go
export GOMODCACHE=/home/devusr/go/pkg/mod
export PATH=$GOPATH/bin:$PATH

# Rust / Cargo
export RUSTUP_HOME=/opt/rustup
export CARGO_HOME=/opt/cargo
export PATH=/opt/cargo/bin:$PATH

# Node.js / nvm
export NVM_DIR=/opt/nvm
[ -s "$NVM_DIR/nvm.sh" ]          && . "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"
EOF

# ── Zellij layout ─────────────────────────────────────────────────────────────
RUN mkdir -p /home/devusr/.config/zellij/layouts \
 && cat > /home/devusr/.config/zellij/layouts/ironclaude.kdl <<'EOF'
layout {
    tab name="claude" focus=true {
        pane command="claude" {
            args "--dangerously-skip-permissions"
            cwd "/home/devusr"
        }
    }
    tab name="shell" {
        pane cwd="/home/devusr"
    }
}
EOF
RUN chown -R devusr:devusr /home/devusr/.config

# ── Entrypoint and shell ──────────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
COPY shell.sh      /shell.sh
RUN  chmod +x /entrypoint.sh /shell.sh

USER devusr
WORKDIR /home/devusr

EXPOSE 7681

ENTRYPOINT ["/entrypoint.sh"]
