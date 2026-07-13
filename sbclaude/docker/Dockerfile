# sbclaude: a throwaway container for running Claude Code with no sandbox
# and no permission prompts against host-mounted paths.
# The `claude` executable is NOT installed here. It is bind-mounted from the host at run time.
# Base must be glibc (the binary needs GLIBC_2.17+). Alpine/musl will not work.
FROM debian:bookworm-slim

LABEL org.opencontainers.image.description="Throwaway Claude Code container (no sandbox, no prompts)." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/Tatsh/sbclaude" \
      org.opencontainers.image.title="sbclaude"

ENV DEBIAN_FRONTEND=noninteractive
ARG DEBIAN_MIRROR=""

RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i -E "s|^(URIs:[[:space:]]*)https?://deb.debian.org/debian[[:space:]]*\$|\1$DEBIAN_MIRROR|" \
            /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates curl wget \
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
          ninja-build \
        build-essential \
        gh \
        git \
        gnupg \
        gosu \
        jq \
        less \
        libcairo2-dev \
        libffi-dev \
        locales \
        nodejs \
        openssh-client \
        pkg-config \
        python3 \
        python3-pip \
        python3-venv \
        qt6-base-dev \
        qt6-base-dev-tools \
        ripgrep \
        tini \
    && corepack enable yarn \
    && sed -i 's/^# *\(en_GB.UTF-8\)/\1/' /etc/locale.gen && locale-gen \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir mcp pre-commit \
    && rm -rf /var/lib/apt/lists/*
# glab
RUN set -eux; \
    ver="$(wget -qO- 'https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases?per_page=1' \
           | grep -oP '"tag_name":"v\K[^"]+' | head -1)"; \
    wget -qO /tmp/glab.tar.gz \
        "https://gitlab.com/gitlab-org/cli/-/releases/v${ver}/downloads/glab_${ver}_linux_amd64.tar.gz"; \
    tar -xzf /tmp/glab.tar.gz -C /tmp bin/glab; \
    install -m 0755 /tmp/bin/glab /usr/local/bin/glab; \
    rm -rf /tmp/glab.tar.gz /tmp/bin
# cc-session-recover with a patch.
# https://github.com/softcane/cc-session-recover/pull/2
RUN git clone https://github.com/softcane/cc-session-recover.git /opt/cc-session-recover \
    && cd /opt/cc-session-recover \
    && git fetch origin pull/2/head:pr2 \
    && git diff main...pr2 | git apply --3way \
    && sed -i 's#^GITIGNORE=.*#GITIGNORE=/dev/null#' scripts/install-into-project.sh \
    && sed -i '/settings\.example\.json" "\$TARGET/d' scripts/install-into-project.sh \
    && rm -rf .git
# Reverse-engineering tools.
RUN curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
        -o /etc/apt/keyrings/adoptium.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/adoptium.asc] https://packages.adoptium.net/artifactory/deb bookworm main" \
        > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        temurin-21-jdk \
        cmake \
        binutils file xxd bsdmainutils \
        openssl unzip xz-utils p7zip-full \
        usbutils \
        ffmpeg sox libsox-fmt-all flac vorbis-tools opus-tools lame mpg123 wavpack shntool \
        libao4 libmpg123-0 libvorbis0a libvorbisfile3 libspeex1 \
        imagemagick zbar-tools \
        x11-utils x11-xserver-utils xauth \
        libgl1 libgl1-mesa-dri libglu1-mesa \
        libpulse0 libasound2 libnss3 \
        libx11-6 libxcb1 libxcursor1 libxrandr2 libxi6 libxtst6 libxrender1 \
        libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxss1 \
        libfontconfig1 libfreetype6 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
# Frida and mitmproxy.
RUN /opt/venv/bin/pip install --no-cache-dir "frida==17.9.11" frida-tools mitmproxy
# dex2jar with a d2j-dex2jar convenience launcher.
RUN wget -qO /tmp/d2j.zip \
        https://github.com/pxb1988/dex2jar/releases/download/v2.4/dex-tools-v2.4.zip \
    && unzip -q /tmp/d2j.zip -d /opt && mv /opt/dex-tools-v2.4 /opt/dex2jar \
    && chmod +x /opt/dex2jar/*.sh && rm /tmp/d2j.zip \
    && printf '#!/usr/bin/env bash\nexec /opt/dex2jar/d2j-dex2jar.sh "$@"\n' \
        > /usr/local/bin/d2j-dex2jar \
    && chmod +x /usr/local/bin/d2j-dex2jar
# baksmali + smali fat jars; track the maintained fork's latest release.
RUN set -eux; \
    ver="$(wget -qO- https://api.github.com/repos/baksmali/smali/releases/latest \
           | grep -oP '"tag_name":\s*"\K[^"]+')"; \
    for t in baksmali smali; do \
        wget -qO "/opt/${t}.jar" \
          "https://github.com/baksmali/smali/releases/download/${ver}/${t}-${ver}-fat-release.jar"; \
        printf '#!/usr/bin/env bash\nexec java -jar /opt/%s.jar "$@"\n' "$t" > "/usr/local/bin/${t}"; \
        chmod +x "/usr/local/bin/${t}"; \
    done
# vgmstream-cli
RUN wget -qO /tmp/vgmstream.zip \
        https://github.com/vgmstream/vgmstream/releases/latest/download/vgmstream-linux.zip \
    && unzip -q /tmp/vgmstream.zip -d /tmp/vgmstream \
    && install -m 0755 /tmp/vgmstream/vgmstream-cli /usr/local/bin/vgmstream-cli \
    && rm -rf /tmp/vgmstream.zip /tmp/vgmstream
# deark
RUN git clone --depth 1 https://github.com/jsummers/deark /tmp/deark \
    && make -C /tmp/deark \
    && install -m 0755 /tmp/deark/deark /usr/local/bin/deark \
    && rm -rf /tmp/deark
# czkawka-cli
RUN wget -qO /usr/local/bin/czkawka_cli \
        https://github.com/qarmin/czkawka/releases/latest/download/linux_czkawka_cli_x86_64 \
    && chmod +x /usr/local/bin/czkawka_cli
# pngdefry
RUN git clone --depth 1 https://github.com/Tatsh/pngdefry /tmp/pngdefry \
    && cc -O2 -o /usr/local/bin/pngdefry /tmp/pngdefry/source/pngdefry.c \
    && rm -rf /tmp/pngdefry
# Launchers for the host-mounted tools (jars/scripts arrive via bind mounts at run time).
COPY bin/apktool bin/analyzeHeadless bin/ghidraRun /usr/local/bin/
RUN chmod +x /usr/local/bin/apktool /usr/local/bin/analyzeHeadless /usr/local/bin/ghidraRun
# Export the toolchain for login shells (which re-read /etc/profile and would otherwise
# reset PATH), so tools resolve no matter how a shell is spawned.
RUN printf '%s\n' \
    'export JAVA_HOME=/usr/lib/jvm/temurin-21-jdk-amd64' \
    'export ANDROID_HOME=/opt/android-sdk' \
    'export ANDROID_SDK_ROOT=/opt/android-sdk' \
    'export GHIDRA_INSTALL_DIR=/usr/share/ghidra' \
    'export PATH=/opt/venv/bin:$JAVA_HOME/bin:/opt/jadx-bin/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:/opt/android-sdk/cmdline-tools/latest/bin:/usr/local/bin:$PATH' \
    > /etc/profile.d/sbclaude.sh
# Environment (Python virtualenv first).
ENV PATH=/opt/venv/bin:/usr/lib/jvm/temurin-21-jdk-amd64/bin:/opt/jadx-bin/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:/opt/android-sdk/cmdline-tools/latest/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
    JAVA_HOME=/usr/lib/jvm/temurin-21-jdk-amd64 \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GHIDRA_INSTALL_DIR=/usr/share/ghidra \
    LANG=en_GB.UTF-8 \
    LC_ALL=en_GB.UTF-8 \
    LANGUAGE=en_GB:en \
    DISABLE_AUTOUPDATER=1
# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
# Force the bash sandbox off regardless of the mounted user settings.json.
# Managed settings are the highest-precedence layer, so this wins over
# (and does not modify) the user's ~/.claude/settings.json.
RUN mkdir -p /etc/claude-code
COPY managed-settings.json /etc/claude-code/managed-settings.json
# Claude will abort if these are not world-readable.
RUN chmod 0644 /etc/claude-code/managed-settings.json

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
# Strip every setuid/setgid bit. Removes setuid-root escalation paths.
# Combined with runtime --security-opt no-new-privileges + --cap-drop ALL.
RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} + 2>/dev/null || true
# tini as PID 1 -> entrypoint sets up identity and drops privileges via gosu.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
