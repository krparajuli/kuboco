FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TTYD_VERSION=1.7.7

# Install useful tools
RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update \
&&  apt-get install -y --no-install-recommends \
        bash \
        curl \
        ca-certificates \
    wget \
    vim \
    nano \
    python3 \
    python3-pip \
    gcc \
    make \
    net-tools \
    procps \
    netcat-openbsd \
    ca-certificates \
    iputils-ping \
    dnsutils \
    socat \
    && rm -rf /var/lib/apt/lists/*

# Install ttyd binary from GitHub releases
RUN DEB_ARCH=$(dpkg --print-architecture) && \
    case "$DEB_ARCH" in \
        amd64)   TTYD_ARCH=x86_64 ;; \
        arm64)   TTYD_ARCH=aarch64 ;; \
        armhf)   TTYD_ARCH=armhf ;; \
        i386)    TTYD_ARCH=i686 ;; \
        *)       TTYD_ARCH=$DEB_ARCH ;; \
    esac && \
    echo "Installing ttyd ${TTYD_VERSION} for arch: ${TTYD_ARCH}" && \
    curl -fL "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${TTYD_ARCH}" \
         -o /usr/local/bin/ttyd && \
    chmod +x /usr/local/bin/ttyd && \
    ttyd --version

# Create devusr with UID/GID 30002
RUN groupadd -g 30002 devusr && \
    useradd -u 30002 -g 30002 -m -s /bin/bash devusr && \
    echo 'devusr ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

USER devusr
WORKDIR /home/devusr

# ttyd listens on 7681 (unprivileged port — no root needed)
EXPOSE 7681

CMD ["/usr/local/bin/ttyd", \
     "--port",      "7681", \
     "--interface", "0.0.0.0", \
     "--writable",  \
     "bash", "-l"]
