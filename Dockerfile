# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

LABEL maintainer="backapp team"
LABEL description="Zero-impact lightweight backup daemon and interactive TUI"

# Prevent interactive prompts and Python output buffering
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BACKUP_JOBS_DIR=/etc/backup/jobs \
    BACKAPP_SOCKET_PATH=/var/run/backapp/backapp.sock

# Install core archive, compression, database clients, and util-linux (for ionice)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    zstd \
    gzip \
    pigz \
    bzip2 \
    zip \
    unzip \
    util-linux \
    procps \
    postgresql-client \
    default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

# Install rcon-cli for Minecraft remote server hooks
ARG RCON_CLI_VER="1.7.8"
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
        amd64) RCON_ARCH="amd64" ;; \
        arm64) RCON_ARCH="arm64" ;; \
        *) echo "Unsupported architecture for rcon-cli: ${ARCH}" && exit 0 ;; \
    esac; \
    curl -fsSL -o /tmp/rcon.tar.gz "https://github.com/itzg/rcon-cli/releases/download/${RCON_CLI_VER}/rcon-cli_${RCON_CLI_VER}_linux_${RCON_ARCH}.tar.gz" \
    && tar -xzf /tmp/rcon.tar.gz -C /usr/local/bin rcon-cli \
    && mv /usr/local/bin/rcon-cli /usr/local/bin/rcon-cli-bin \
    && rm -f /tmp/rcon.tar.gz

# Provide rcon-cli wrapper accepting both -H/-P (mcrcon style) and --host/--port flags
RUN printf '#!/usr/bin/env python3\n\
import sys, subprocess\n\
args = sys.argv[1:]\n\
new_args = []\n\
i = 0\n\
while i < len(args):\n\
    arg = args[i]\n\
    if arg == "-H" and i + 1 < len(args):\n\
        new_args.extend(["--host", args[i+1]])\n\
        i += 2\n\
    elif arg == "-P" and i + 1 < len(args):\n\
        new_args.extend(["--port", args[i+1]])\n\
        i += 2\n\
    elif arg == "-p" and i + 1 < len(args):\n\
        new_args.extend(["--password", args[i+1]])\n\
        i += 2\n\
    else:\n\
        new_args.append(arg)\n\
        i += 1\n\
res = subprocess.run(["/usr/local/bin/rcon-cli-bin"] + new_args)\n\
sys.exit(res.returncode)\n' > /usr/local/bin/rcon-cli \
    && chmod +x /usr/local/bin/rcon-cli

# Setup backapp application directory
WORKDIR /app

# Copy dependency specifications first to leverage Docker cache
COPY pyproject.toml requirements.txt README.md /app/
COPY src/ /app/src/
COPY examples/ /app/examples/

# Install Python package
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Create default directories
RUN mkdir -p /etc/backup/jobs /etc/backup/examples /mnt/backups /var/run/backapp

VOLUME ["/etc/backup/jobs", "/etc/backup/examples", "/mnt/backups", "/var/run/backapp"]

# Default entrypoint runs the background scheduler daemon
ENTRYPOINT ["backapp"]
CMD ["daemon"]
