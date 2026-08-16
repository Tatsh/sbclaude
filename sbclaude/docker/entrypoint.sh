#!/usr/bin/env bash
# Recreate the host user's identity inside the container, then drop privileges
# and launch Claude Code with no permission prompts.
#
# Why mirror the host identity (UID/GID/USER/HOME) instead of using a baked-in
# user? Two reasons:
#   1. Mounted files in ~/.claude (notably .credentials.json, mode 0600) must be
#      owned by the same UID we run as, or auth breaks.
#   2. Mirroring HOME means "~"-relative settings (e.g. additionalDirectories:
#      ["~/project-name"]) resolve to the SAME absolute path inside and outside the
#      container, so host paths "just work".
set -euo pipefail

: "${HOST_UID:=1000}"
: "${HOST_GID:=1000}"
: "${HOST_USER:=claude}"
: "${HOST_HOME:=/home/${HOST_USER}}"

# --dangerously-skip-permissions refuses to run as root. Mirroring root would also defeat the point,
# so fail loudly with a clear message.
if [ "$HOST_UID" -eq 0 ]; then
    echo "sbclaude: refusing to run as UID 0 (--dangerously-skip-permissions disallows root)." >&2
    echo "            Re-run as a non-root host user." >&2
    exit 1
fi

# Create the group for HOST_GID if one does not already exist.
if ! getent group "$HOST_GID" >/dev/null 2>&1; then
    groupadd -g "$HOST_GID" "$HOST_USER" >/dev/null 2>&1 || true
fi

# Create the user for HOST_UID if one does not already exist. -M: do not create or touch the home
# dir here (it is provided by mounts); we only need a passwd entry so getpwuid()-based tools (git,
# claude) resolve a name and home.
if ! getent passwd "$HOST_UID" >/dev/null 2>&1; then
    useradd -u "$HOST_UID" -g "$HOST_GID" -d "$HOST_HOME" -s /bin/bash \
        -M "$HOST_USER" >/dev/null 2>&1 || true
fi
USER_NAME="$(getent passwd "$HOST_UID" | cut -d: -f1)"
USER_NAME="${USER_NAME:-$HOST_USER}"

# Carry over the supplementary groups Docker granted with --group-add (GPU/video/render/kvm
# device nodes). Those are granted to PID 1; `gosu uid:gid` sets the group list to EXACTLY that
# one primary group and silently drops them, which is why --gpus alone leaves /dev/nvidia*
# unreadable ("Failed to initialize NVML: Insufficient Permissions"). Re-add them to the user so
# the later `gosu "$HOST_UID"` (user only) picks up the full list from /etc/group.
for _gid in $(id -G); do
    if [ "$_gid" != '0' ] && [ "$_gid" != "$HOST_GID" ]; then
        if ! getent group "$_gid" >/dev/null 2>&1; then
            groupadd -g "$_gid" "hostgid$_gid" >/dev/null 2>&1 || true
        fi
        _gname="$(getent group "$_gid" | cut -d: -f1 || true)"
        if [ -n "$_gname" ]; then
            usermod -aG "$_gname" "$USER_NAME" >/dev/null 2>&1 || true
        fi
    fi
done

# The NVIDIA Container Toolkit injects the driver libraries but does not always install the
# glvnd/Vulkan manifests that tell the loaders those libraries exist -- leaving libEGL to fall
# back to Mesa and any GPU-accelerated GL client to crash. Synthesise them when the library is
# present but its manifest is not. Cheap, idempotent, and a no-op without the NVIDIA libs.
if ldconfig -p 2>/dev/null | grep -q libEGL_nvidia.so.0; then
    mkdir -p /usr/share/glvnd/egl_vendor.d
    if [ ! -e /usr/share/glvnd/egl_vendor.d/10_nvidia.json ]; then
        printf '%s\n' \
            '{"file_format_version":"1.0.0","ICD":{"library_path":"libEGL_nvidia.so.0"}}' \
            > /usr/share/glvnd/egl_vendor.d/10_nvidia.json
    fi
fi
# NVIDIA's GBM backend. The toolkit injects libnvidia-allocator.so.1 but not the
# gbm/nvidia-drm_gbm.so name Mesa's libgbm looks for. Headless does not care, but a headed
# Wayland window cannot get hardware buffers without it: chrome://gpu reports "Software only"
# and no WebGL context is created at all.
NV_ALLOC="$(ldconfig -p 2>/dev/null | awk '/libnvidia-allocator\.so\.1 /{print $NF; exit}')"
if [ -n "$NV_ALLOC" ] && [ -e "$NV_ALLOC" ]; then
    GBM_DIR="$(dirname "$NV_ALLOC")/gbm"
    mkdir -p "$GBM_DIR"
    [ -e "$GBM_DIR/nvidia-drm_gbm.so" ] || ln -sf "$NV_ALLOC" "$GBM_DIR/nvidia-drm_gbm.so"
fi

if ldconfig -p 2>/dev/null | grep -q libGLX_nvidia.so.0; then
    mkdir -p /usr/share/vulkan/icd.d
    if [ ! -e /usr/share/vulkan/icd.d/nvidia_icd.json ]; then
        printf '%s\n' \
            '{"file_format_version":"1.0.0","ICD":{"library_path":"libGLX_nvidia.so.0","api_version":"1.3.0"}}' \
            > /usr/share/vulkan/icd.d/nvidia_icd.json
    fi
fi

# Ensure HOME exists (Docker auto-creates mount targets as root; the top-level home dir itself may
# not be a mount). Chown only the top dir, never recurse into mounted subtrees.
mkdir -p "$HOST_HOME"
chown "$HOST_UID:$HOST_GID" "$HOST_HOME" 2>/dev/null || true

# ~/.config is created root-owned by Docker whenever something is mounted beneath it (e.g.
# ~/.config/gh), leaving the user unable to write there. Anything that stores state under
# XDG_CONFIG_HOME then breaks in confusing ways -- Chrome, for instance, cannot create its
# crashpad database, passes an empty --database to the handler, and aborts at startup with
# SIGTRAP. Chown the directory ONLY; never recurse, since the mounts inside it are host paths.
mkdir -p "$HOST_HOME/.config"
chown "$HOST_UID:$HOST_GID" "$HOST_HOME/.config" 2>/dev/null || true

# When a socket is forwarded into /run/user/<uid> (e.g. --wayland), Docker auto-creates that
# parent as root:root 0755, but XDG_RUNTIME_DIR must be owned by the user and mode 0700 or
# clients refuse it. Chown the directory ONLY, never its contents: the sockets inside are host
# bind mounts and chowning them would alter the host's files.
RUNTIME_DIR="/run/user/$HOST_UID"
if [ -d "$RUNTIME_DIR" ]; then
    chown "$HOST_UID:$HOST_GID" "$RUNTIME_DIR" 2>/dev/null || true
    chmod 700 "$RUNTIME_DIR" 2>/dev/null || true
fi

# The host config records installMethod=native, so claude expects its own binary at
# ~/.local/bin/claude. We bind-mount it at /usr/local/bin/claude instead, so create the expected
# symlink to silence the "claude command not found at ~/.local/bin/claude" warning.
mkdir -p "$HOST_HOME/.local/bin"
ln -sf /usr/local/bin/claude "$HOST_HOME/.local/bin/claude"
chown -R "$HOST_UID:$HOST_GID" "$HOST_HOME/.local" 2>/dev/null || true

# Inject the no-prompt flag unless explicitly disabled (SBCLAUDE_SKIP_PERMISSIONS=0).
SKIP_FLAG=()
if [ "${SBCLAUDE_SKIP_PERMISSIONS:-1}" = "1" ]; then
    SKIP_FLAG=(--dangerously-skip-permissions)
fi

# Put the user's ~/.local/bin first on PATH (claude warns when it is missing) while keeping the
# image's toolchain PATH after it.
export PATH="$HOST_HOME/.local/bin:$PATH"

# Set up cc-session-recover in the project (auto-resume across quota pauses) when SBCLAUDE_RECOVER=1
# (sbclaude sets this for --session-recover). The installer is the clone at /opt/cc-session-recover;
# it copies its hook templates relative to its own location and merges them into the project's
# .claude/settings.local.json. A failed install aborts the session.
RECOVER_INSTALLER=/opt/cc-session-recover/scripts/install-into-project.sh
# The recovery artifacts to keep out of the project's git history. The installer's own
# .gitignore handling is patched out in the image; we append exactly these entries instead
# (each only when absent), so the set is explicit and stable.
RECOVER_GITIGNORE_ENTRIES=(
    /.claude/auto-continue.md
    /.claude/hooks/inject-standing-instructions.sh
    /.claude/hooks/log-stop-failure.sh
    /.claude/hooks/remind-on-prompt.sh
    /.claude/session-recover.js
    /.claude/standing-instructions.md
    /.claude/statusline-quota-cache.sh
    /HANDOFF.md
    /session-recover.yaml
)
if [ "${SBCLAUDE_RECOVER:-0}" = "1" ]; then
    if [ ! -f "$RECOVER_INSTALLER" ]; then
        echo "sbclaude: cc-session-recover installer not found at $RECOVER_INSTALLER; aborting." >&2
        exit 1
    fi
    if ! gosu "$HOST_UID:$HOST_GID" \
        env HOME="$HOST_HOME" USER="$USER_NAME" LOGNAME="$USER_NAME" PATH="$PATH" \
        bash "$RECOVER_INSTALLER" "$PWD"; then
        echo "sbclaude: cc-session-recover install failed; aborting (no session started)." >&2
        exit 1
    fi
    # Append each recovery artifact to the project's .gitignore when missing (creating the file if
    # needed). The array is expanded into the inner shell's positional parameters. Runs as the
    # mapped user for correct ownership. A write failure here will not abort the session.
    # shellcheck disable=SC2016
    gosu "$HOST_UID:$HOST_GID" \
        env HOME="$HOST_HOME" USER="$USER_NAME" LOGNAME="$USER_NAME" PATH="$PATH" \
        bash -c '
            target=$1
            shift
            cd "$target" 2>/dev/null || exit 0
            for entry in "$@"; do
                grep -qxF "$entry" .gitignore 2>/dev/null || printf "%s\n" "$entry" >> .gitignore
            done
            exit 0
        ' _ "$PWD" "${RECOVER_GITIGNORE_ENTRIES[@]}"
fi

# Drop to the mapped user with a correct HOME/USER/PATH environment and exec claude.
# NOTE: gosu is given the UID only, deliberately. Passing "uid:gid" would set the group list to
# exactly that one group, discarding the device groups re-added above. The primary group is
# already HOST_GID via the passwd entry.
exec gosu "$HOST_UID" \
    env HOME="$HOST_HOME" USER="$USER_NAME" LOGNAME="$USER_NAME" PATH="$PATH" \
    claude "${SKIP_FLAG[@]}" "$@"
