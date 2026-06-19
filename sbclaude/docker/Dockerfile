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
      org.opencontainers.image.description="Throwaway Claude Code container (no sandbox, no prompts)." \
      org.opencontainers.image.source="https://github.com/Tatsh/sbclaude" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive

# Optional Debian archive mirror (e.g. http://ftp.us.debian.org/debian) for when
# deb.debian.org is slow. Empty by default, leaving the stock sources untouched.
ARG DEBIAN_MIRROR=""

# Runtime deps in a single layer. When DEBIAN_MIRROR is set, the deb822 sources are
# first rewritten to that mirror (only the main archive URI is anchored, so the
# debian-security URI is left pointing at deb.debian.org). The slim base has empty apt
# indices, so the first update is unavoidable; it hits Debian's http mirror (verified by
# the preinstalled archive keyring, no CA certs needed) to bootstrap ca-certificates,
# curl, and wget (all absent in bookworm-slim). Only then can the NodeSource key be
# fetched and its HTTPS repo reached, so a second update + the main install follow. wget
# lives here, not just in Dockerfile.re, so the RE image inherits it for its jar
# downloads.
#   git       - core to most workflows / commit tooling
#   gnupg     - signing commits with the host's GPG keys (see --gpg)
#   openssh   - SSH git remotes using the host's keys (see --ssh)
#   build-ess - a C toolchain (gcc/g++/make) for building/installing native deps
#   gh        - GitHub CLI, from GitHub's own apt repo for a current version
#   jq        - JSON processor (cc-session-recover's installer merges settings.local.json
#               with it; its optional quota watcher needs it too)
#   ripgrep   - file search
#   gosu      - clean privilege drop to the host-mapped UID
#   tini      - PID 1 reaping (claude spawns many child processes)
#   ca-certs  - HTTPS to the API
#   nodejs    - Node.js 24 LTS from NodeSource (Debian's own nodejs is 18, EOL and with
#               no Corepack); the runtime for Yarn, since the Node bundled inside the
#               claude binary is not on PATH for general use. NodeSource bundles Corepack.
# The NodeSource key is an armoured .asc referenced in signed-by, so no gnupg/dearmor is
# needed; GitHub's keyring ships already-dearmoured. Same repo pattern as the Adoptium
# block in Dockerfile.re. glab (the GitLab CLI) is not in Debian and is released on
# gitlab.com, so it is fetched as a binary in a separate step below. The Corepack
# Yarn shim is enabled in the same layer so `yarn` resolves to whatever version each
# project pins via its `packageManager` field (this repo pins Yarn 4.x, checked into
# .yarn/releases, so it runs offline) instead of a single globally pinned version.
# Finally the MCP SDK and pre-commit are installed into a dedicated virtualenv at
# /opt/venv (never the PEP 668 externally-managed system interpreter) so Python MCP
# servers (e.g. the ghidra-mcp PEP 723 bridge) start with no first-run download that
# could trip the MCP connection timeout, and `pre-commit` is on PATH for commits made in
# the box. /opt/venv is outside any mounted project directory, and its bin is placed
# first on PATH below so `python3` and `pip` resolve to it.
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i -E "s|^(URIs:[[:space:]]*)https?://deb.debian.org/debian[[:space:]]*\$|\1$DEBIAN_MIRROR|" \
            /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl wget \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        -o /etc/apt/keyrings/nodesource.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.asc] https://deb.nodesource.com/node_24.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gh \
        git \
        gnupg \
        jq \
        libffi-dev \
        openssh-client \
        pkg-config \
        ripgrep \
        less \
        gosu \
        tini \
        locales \
        nodejs \
        python3 \
        python3-pip \
        python3-venv \
    && corepack enable yarn \
    && sed -i 's/^# *\(en_GB.UTF-8\)/\1/' /etc/locale.gen && locale-gen \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir mcp pre-commit \
    && rm -rf /var/lib/apt/lists/*

# glab (GitLab CLI): not in Debian and released on gitlab.com (not GitHub), so resolve the
# latest tag from the GitLab releases API and install the linux amd64 binary from the
# matching release download.
RUN set -eux; \
    ver="$(wget -qO- 'https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases?per_page=1' \
           | grep -oP '"tag_name":"v\K[^"]+' | head -1)"; \
    wget -qO /tmp/glab.tar.gz \
        "https://gitlab.com/gitlab-org/cli/-/releases/v${ver}/downloads/glab_${ver}_linux_amd64.tar.gz"; \
    tar -xzf /tmp/glab.tar.gz -C /tmp bin/glab; \
    install -m 0755 /tmp/bin/glab /usr/local/bin/glab; \
    rm -rf /tmp/glab.tar.gz /tmp/bin

# cc-session-recover: opt-in auto-resume helper that lets Claude Code pick a long task back
# up after a quota pause. Vendored as a git clone (not the npm package) with PR #2 applied
# on top (safer watcher argument handling + the configurable CC_REMIND_MODE prompt injection);
# the patch is computed against its merge base so it applies cleanly onto main. The
# entrypoint runs scripts/install-into-project.sh from here when --session-recover is given
# (it stays inert otherwise). The .git directory is dropped afterwards to slim the image.
# The two sed commands patch the installer before it is ever run: the first points its
# GITIGNORE variable at /dev/null so it never creates or appends to the project's .gitignore
# (every touch/grep/append in that block then no-ops; the entrypoint manages .gitignore
# itself instead); the second drops the line that copies settings.example.json into the
# project (the hook merge still reads the template from this vendored tree, so it keeps
# working). Both keep clutter out of the user's repository.
# https://github.com/softcane/cc-session-recover/pull/2
RUN git clone https://github.com/softcane/cc-session-recover.git /opt/cc-session-recover \
    && cd /opt/cc-session-recover \
    && git fetch origin pull/2/head:pr2 \
    && git diff main...pr2 | git apply --3way \
    && sed -i 's#^GITIGNORE=.*#GITIGNORE=/dev/null#' scripts/install-into-project.sh \
    && sed -i '/settings\.example\.json" "\$TARGET/d' scripts/install-into-project.sh \
    && rm -rf .git

# PATH puts the venv first so `python3`/`pip` are the venv's; LANG/LC_ALL/LANGUAGE pin
# the en_GB locale generated above; DISABLE_AUTOUPDATER stops the read-only mounted
# claude binary from ever trying to self-replace.
ENV PATH=/opt/venv/bin:$PATH \
    LANG=en_GB.UTF-8 \
    LC_ALL=en_GB.UTF-8 \
    LANGUAGE=en_GB:en \
    DISABLE_AUTOUPDATER=1

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

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Build-time hardening: strip every setuid/setgid bit. The container runs as a
# mapped non-root user via gosu (which is invoked by root and needs no setuid bit),
# and nothing here uses su/sudo/mount, so this removes setuid-root escalation paths.
# Combined with runtime --security-opt no-new-privileges + --cap-drop ALL.
RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} + 2>/dev/null || true

# tini as PID 1 -> entrypoint sets up identity and drops privileges via gosu.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
