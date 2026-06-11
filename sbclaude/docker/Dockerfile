# sbclaude: a throwaway container for running Claude Code with no sandbox
# and no permission prompts, against host-mounted paths.
#
# The `claude` executable is NOT installed here. It is a self-contained native
# binary (Node is bundled in) and is bind-mounted from the host at run time, so
# the image stays generic and always tracks whatever version the host has.
#
# Base must be glibc (the binary needs GLIBC_2.17+). Alpine/musl would not work.
FROM debian:bookworm-slim

LABEL org.opencontainers.image.title="sbclaude" \
      org.opencontainers.image.description="Throwaway Claude Code container (no sandbox, no prompts)" \
      org.opencontainers.image.source="https://github.com/Tatsh/sbclaude" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive

# Runtime deps Claude Code expects to find on PATH:
#   git      - core to most workflows / commit tooling
#   ripgrep  - file search
#   gosu     - clean privilege drop to the host-mapped UID
#   tini     - PID 1 reaping (claude spawns many child processes)
#   ca-certs - HTTPS to the API
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        ripgrep \
        curl \
        less \
        gosu \
        tini \
        locales \
        python3 \
        python3-pip \
    && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# Pre-install the MCP SDK into the system interpreter so Python MCP servers (e.g.
# the ghidra-mcp PEP 723 bridge) start immediately with `/usr/bin/python3 server.py`,
# with no first-run download that could trip the MCP connection timeout.
RUN python3 -m pip install --break-system-packages --no-cache-dir \
        'mcp>=1.5.0,<2.0.0' 'requests>=2.32.0,<3.0.0'

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    LANGUAGE=en_US:en

# uv: lets Python MCP servers / tools run with an auto-managed interpreter. A host
# venv's `bin/python` symlinks to a host-only interpreter that does not exist in the
# box (ENOENT); `uv run <script>` instead provisions the interpreter + deps from the
# script's PEP 723 metadata, and the same command works on the host too.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Force the bash sandbox OFF regardless of the mounted user settings.json.
# Managed settings are the highest-precedence layer, so this wins over
# (and does not modify) the user's ~/.claude/settings.json.
RUN mkdir -p /etc/claude-code
COPY managed-settings.json /etc/claude-code/managed-settings.json
# COPY keeps the build-context file's restrictive 0640 mode and assigns root
# ownership, so the mapped non-root user cannot read it and Claude Code aborts
# with EACCES while loading managed settings. Make it world-readable.
RUN chmod 0644 /etc/claude-code/managed-settings.json

# The binary is mounted read-only; never let it try to self-replace.
ENV DISABLE_AUTOUPDATER=1

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Build-time hardening: strip every setuid/setgid bit. The container runs as a
# mapped non-root user via gosu (which is invoked by root and needs no setuid bit),
# and nothing here uses su/sudo/mount, so this removes setuid-root escalation paths.
# Combined with runtime --security-opt no-new-privileges + --cap-drop ALL.
RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} + 2>/dev/null || true

# tini as PID 1 -> entrypoint sets up identity and drops privileges via gosu.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
