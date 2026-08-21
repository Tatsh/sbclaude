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

# Passwordless sudo for the mapped user, when sbclaude was started with --sudo.
#
# Opt-in, because the box is a no-prompt autonomous agent: whether it can reach container root is
# a decision for whoever starts the box. The image strips every setuid bit at build time, so
# sudo's is restored here rather than baked in, which leaves a box started without --sudo with no
# setuid binary at all. secure_path is overridden with this image's PATH so that the toolchain
# (the JDK, /opt/venv, /usr/local/bin) resolves under sudo as it does without it; the default
# would cut it down to the system directories. A drop-in with a syntax error disables sudo
# altogether, so it is checked and discarded if bad rather than trusted.
if [ "${SBCLAUDE_SUDO:-0}" = "1" ]; then
    if ! SUDO_BIN=$(command -v sudo); then
        echo 'sbclaude: sudo is not installed in this image; --sudo has no effect.' >&2
    else
        printf '%s\n' "$USER_NAME ALL=(ALL) NOPASSWD: ALL" "Defaults secure_path=\"$PATH\"" \
            > /etc/sudoers.d/sbclaude
        chmod 0440 /etc/sudoers.d/sbclaude
        if visudo -cqf /etc/sudoers.d/sbclaude >/dev/null 2>&1; then
            chmod u+s "$SUDO_BIN"
        else
            rm -f /etc/sudoers.d/sbclaude
            echo 'sbclaude: generated sudoers file is invalid; sudo left disabled.' >&2
        fi
        # The kernel ignores a setuid bit once no_new_privs is set, and sudo refuses to run at
        # all. sbclaude omits --security-opt no-new-privileges for a --sudo box, so this only
        # trips when the box was started some other way; say so up front rather than leave sudo
        # failing later with a message about container configuration.
        if grep -qs '^NoNewPrivs:[[:space:]]*1' /proc/self/status; then
            echo 'sbclaude: no_new_privs is set for this container, so sudo cannot escalate.' >&2
        fi
    fi
fi

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

# The forwarded gpg-agent socket lands in a gnupg subdirectory that Docker likewise creates as
# root:root 0755. GnuPG requires its socket directory to be owned by the user, so give it the
# same treatment. Directory only, for the same reason as above.
if [ -d "$RUNTIME_DIR/gnupg" ]; then
    chown "$HOST_UID:$HOST_GID" "$RUNTIME_DIR/gnupg" 2>/dev/null || true
    chmod 700 "$RUNTIME_DIR/gnupg" 2>/dev/null || true
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

# Provision the box's own virtualenv when the working directory looks like a Python project.
#
# The host's .venv is mounted read-only and is useless in here anyway: a virtualenv hard-codes the
# absolute path of the interpreter it was built against, and the host's does not exist in this
# image. Building our own at a different path is what keeps the two from destroying each other.
# SBCLAUDE_VENV is set by sbclaude and lives beside the project so it survives --rm.
#
# Best-effort: a project whose dependencies cannot be resolved here (a package needing host
# headers, say) must still get a session, so a failure warns and carries on.
if [ -n "${SBCLAUDE_VENV:-}" ] && [ "${SBCLAUDE_SETUP_VENV:-1}" = "1" ]; then
    venv_marker=''
    for candidate in pyproject.toml setup.py setup.cfg requirements.txt Pipfile; do
        if [ -f "$candidate" ]; then
            venv_marker=$candidate
            break
        fi
    done
    if [ -n "$venv_marker" ]; then
        echo "sbclaude: $venv_marker found; preparing $SBCLAUDE_VENV" >&2
        # Runs as the mapped user so everything it writes is owned by them, not root. The body is
        # single-quoted deliberately: it must expand in the inner shell, not this one.
        # shellcheck disable=SC2016
        gosu "$HOST_UID:$HOST_GID" \
            env HOME="$HOST_HOME" USER="$USER_NAME" LOGNAME="$USER_NAME" PATH="$PATH" \
            bash -c '
                # pipefail matters here: every command below is piped into tail to keep the
                # session banner short, and without it the status read back would be tail is.
                set -u -o pipefail
                venv=$1
                marker=$2
                # Set here rather than trusted from the caller. Without it uv falls back to
                # whatever it inherits, and a stale value points the sync at the environment of
                # some other project and replaces the packages in it.
                export UV_PROJECT_ENVIRONMENT="$venv"
                # uv sync builds the virtualenv and installs the locked dependencies in one step,
                # so only reach for python -m venv when there is no uv project to sync.
                #
                # A single dependency that cannot build here fails the whole sync, which would
                # leave the box with no test runner and no linter over one package it was never
                # going to use. Some cannot build at any price: pygobject 3.56 wants
                # girepository-2.0, which wants a glib newer than the base image carries, so no
                # amount of -dev packages will satisfy it.
                #
                # uv names the culprit in its hint line, so read it back, exclude that one package
                # and try again. Each pass excludes one more, which converges because the set of
                # dependencies is finite; the cap is only there so that a failure reported without
                # a parseable hint cannot spin.
                if command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ]; then
                    skips=()
                    synced=0
                    for _ in 1 2 3 4 5 6; do
                        if out=$(uv sync --all-extras "${skips[@]}" 2>&1); then
                            synced=1
                            break
                        fi
                        printf "%s\n" "$out" | tail -15 >&2
                        pkg=$(printf "%s\n" "$out" |
                            sed -n "s/^hint: \`\([^\`]*\)\`.*was included because.*/\1/p" |
                            head -1)
                        if [ -z "$pkg" ]; then
                            break
                        fi
                        echo "sbclaude: $pkg cannot be built here; retrying without it." >&2
                        skips+=(--no-install-package "$pkg")
                    done
                    if [ "$synced" = 1 ]; then
                        # Two array entries per exclusion: the flag and its value.
                        excluded=$((${#skips[@]} / 2))
                        if [ "$excluded" -eq 1 ]; then
                            echo "sbclaude: virtualenv ready, minus 1 package that cannot be built here." >&2
                        elif [ "$excluded" -gt 1 ]; then
                            echo "sbclaude: virtualenv ready, minus $excluded packages that cannot be built here." >&2
                        fi
                        exit 0
                    fi
                    # Nothing above worked. Runtime dependencies alone are still worth more than
                    # an empty virtualenv.
                    echo "sbclaude: full sync failed; installing runtime dependencies only." >&2
                    if uv sync --no-default-groups 2>&1 | tail -15; then
                        exit 0
                    fi
                    echo "sbclaude: uv sync failed; falling back to a bare virtualenv." >&2
                fi
                if [ ! -x "$venv/bin/python" ] && ! python3 -m venv "$venv"; then
                    exit 1
                fi
                if [ "$marker" = requirements.txt ] && \
                    ! "$venv/bin/pip" install --quiet -r requirements.txt 2>&1 | tail -20; then
                    echo "sbclaude: pip install failed; the virtualenv may be incomplete." >&2
                fi
                exit 0
            ' _ "$SBCLAUDE_VENV" "$venv_marker" ||
            echo "sbclaude: could not prepare $SBCLAUDE_VENV; continuing without it." >&2
    fi
    # On PATH regardless, so that python and pip resolve to the box's virtualenv rather than the
    # image's /opt/venv even when provisioning was skipped or failed.
    export PATH="$SBCLAUDE_VENV/bin:$PATH"
fi

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
