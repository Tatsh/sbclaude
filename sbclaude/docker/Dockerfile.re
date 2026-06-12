# sbclaude-re: the base sbclaude plus a mobile reverse-engineering toolchain.
#
# Philosophy (matches the host setup in mobile-app-re/ai/prompts/*.md):
#   - MOUNT big self-contained host tools at run time (Ghidra, Android SDK, jadx,
#     apktool) via the wrapper's --ghidra/--android flags — exact host versions,
#     no duplication. This image only provides LAUNCHERS + the runtime libraries
#     those mounted binaries dynamically link against.
#   - INSTALL the things that cannot be cleanly mounted: a JDK (Ghidra 12 needs
#     21), build tools, and Python-ABI-bound tools (frida, mitmproxy), plus
#     dex2jar/baksmali/smali which are absent on the host.
FROM sbclaude:latest

ENV DEBIAN_FRONTEND=noninteractive

# --- Temurin (Adoptium) JDK 21 (Ghidra 12.x needs it; fine for jadx/apktool/baksmali),
#     the build toolchain, binutils, analysis utilities, and the X11/GL/audio libs the
#     MOUNTED Ghidra GUI, jadx-gui and Android emulator dynamically link against.
#     usbutils is for `adb` over USB. One apt layer: register the Adoptium repo
#     (armoured .asc key via curl inherited from the base — no gnupg/dearmor), then a
#     single update + install. python3*, curl, and wget already come from the base
#     image (wget pulls the dex2jar/baksmali jars below). ---
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
        -o /etc/apt/keyrings/adoptium.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/adoptium.asc] https://packages.adoptium.net/artifactory/deb bookworm main" \
        > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        temurin-21-jdk \
        build-essential gcc g++ make pkg-config cmake \
        binutils file xxd bsdmainutils \
        openssl unzip zip xz-utils p7zip-full \
        usbutils \
        x11-utils x11-xserver-utils xauth \
        libgl1 libgl1-mesa-dri libglu1-mesa \
        libpulse0 libasound2 libnss3 \
        libx11-6 libxcb1 libxcursor1 libxrandr2 libxi6 libxtst6 libxrender1 \
        libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxss1 \
        libfontconfig1 libfreetype6 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/temurin-21-jdk-amd64

# --- Python RE tooling. The base image already provides /opt/venv (with the MCP SDK)
#     on PATH, so add the RE tools to that same venv rather than recreating it. Frida
#     is pinned to the host version so a host-built frida-server matches the client
#     (see dynamic-analysis-recipes.md). ---
RUN /opt/venv/bin/pip install --no-cache-dir \
        "frida==17.9.11" frida-tools mitmproxy

# --- dex2jar (absent on host) ---
RUN wget -qO /tmp/d2j.zip \
        https://github.com/pxb1988/dex2jar/releases/download/v2.4/dex-tools-v2.4.zip && \
    unzip -q /tmp/d2j.zip -d /opt && mv /opt/dex-tools-v2.4 /opt/dex2jar && \
    chmod +x /opt/dex2jar/*.sh && rm /tmp/d2j.zip

# --- baksmali + smali fat jars (absent on host); track the maintained fork's latest ---
RUN set -eux; \
    ver="$(wget -qO- https://api.github.com/repos/baksmali/smali/releases/latest \
           | grep -oP '"tag_name":\s*"\K[^"]+')"; \
    for t in baksmali smali; do \
        wget -qO "/opt/${t}.jar" \
          "https://github.com/baksmali/smali/releases/download/${ver}/${t}-${ver}-fat-release.jar"; \
        printf '#!/usr/bin/env bash\nexec java -jar /opt/%s.jar "$@"\n' "$t" > "/usr/local/bin/${t}"; \
        chmod +x "/usr/local/bin/${t}"; \
    done

# dex2jar convenience launcher (d2j-dex2jar)
RUN printf '#!/usr/bin/env bash\nexec /opt/dex2jar/d2j-dex2jar.sh "$@"\n' > /usr/local/bin/d2j-dex2jar && \
    chmod +x /usr/local/bin/d2j-dex2jar

# Launchers for the host-MOUNTED tools (jars/scripts arrive via bind mounts at run time).
COPY bin/apktool bin/analyzeHeadless bin/ghidraRun /usr/local/bin/
RUN chmod +x /usr/local/bin/apktool /usr/local/bin/analyzeHeadless /usr/local/bin/ghidraRun

# Environment for the mounted toolchains. These paths are valid once the wrapper
# bind-mounts them; harmless otherwise.
ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GHIDRA_INSTALL_DIR=/usr/share/ghidra \
    PATH=/opt/venv/bin:/usr/lib/jvm/temurin-21-jdk-amd64/bin:/opt/jadx-bin/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:/opt/android-sdk/cmdline-tools/latest/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# Also export the toolchain for LOGIN shells (which re-read /etc/profile and would
# otherwise reset PATH), so tools resolve no matter how a shell is spawned.
RUN printf '%s\n' \
    'export JAVA_HOME=/usr/lib/jvm/temurin-21-jdk-amd64' \
    'export ANDROID_HOME=/opt/android-sdk' \
    'export ANDROID_SDK_ROOT=/opt/android-sdk' \
    'export GHIDRA_INSTALL_DIR=/usr/share/ghidra' \
    'export PATH=/opt/venv/bin:$JAVA_HOME/bin:/opt/jadx-bin/bin:/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:/opt/android-sdk/cmdline-tools/latest/bin:/usr/local/bin:$PATH' \
    > /etc/profile.d/sbclaude.sh

# Build-time hardening: strip setuid/setgid bits added by the RE packages too
# (same rationale as the base image).
RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} + 2>/dev/null || true

# ENTRYPOINT is inherited from sbclaude (tini -> entrypoint.sh -> claude).
