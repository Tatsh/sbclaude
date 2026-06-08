#!/usr/bin/env bash
# Recreate the host user's identity inside the container, then drop privileges
# and launch Claude Code with no permission prompts.
#
# Why mirror the host identity (UID/GID/USER/HOME) instead of using a baked-in
# user?  Two reasons:
#   1. Mounted files in ~/.claude (notably .credentials.json, mode 0600) must be
#      owned by the same UID we run as, or auth breaks.
#   2. Mirroring HOME means "~"-relative settings (e.g. additionalDirectories:
#      ["~/dev/wiswa"]) resolve to the SAME absolute path inside and outside the
#      container, so host paths "just work".
set -euo pipefail

: "${HOST_UID:=1000}"
: "${HOST_GID:=1000}"
: "${HOST_USER:=claude}"
: "${HOST_HOME:=/home/${HOST_USER}}"

# --dangerously-skip-permissions refuses to run as root. Mirroring root would
# also defeat the point, so fail loudly with a clear message.
if [ "$HOST_UID" -eq 0 ]; then
    echo "sbclaude: refusing to run as UID 0 (--dangerously-skip-permissions disallows root)." >&2
    echo "            Re-run as a non-root host user." >&2
    exit 1
fi

# Create the group for HOST_GID if one does not already exist.
if ! getent group "$HOST_GID" >/dev/null 2>&1; then
    groupadd -g "$HOST_GID" "$HOST_USER" >/dev/null 2>&1 || true
fi

# Create the user for HOST_UID if one does not already exist. -M: do not create
# or touch the home dir here (it is provided by mounts); we only need a passwd
# entry so getpwuid()-based tools (git, claude) resolve a name and home.
if ! getent passwd "$HOST_UID" >/dev/null 2>&1; then
    useradd -u "$HOST_UID" -g "$HOST_GID" -d "$HOST_HOME" -s /bin/bash -M "$HOST_USER" >/dev/null 2>&1 || true
fi
USER_NAME="$(getent passwd "$HOST_UID" | cut -d: -f1)"
USER_NAME="${USER_NAME:-$HOST_USER}"

# Ensure HOME exists (Docker auto-creates mount targets as root; the top-level
# home dir itself may not be a mount). Chown only the top dir, never recurse
# into mounted subtrees.
mkdir -p "$HOST_HOME"
chown "$HOST_UID:$HOST_GID" "$HOST_HOME" 2>/dev/null || true

# The host config records installMethod=native, so claude expects its own binary at
# ~/.local/bin/claude. We bind-mount it at /usr/local/bin/claude instead, so create the
# expected symlink to silence the "claude command not found at ~/.local/bin/claude" warning.
mkdir -p "$HOST_HOME/.local/bin"
ln -sf /usr/local/bin/claude "$HOST_HOME/.local/bin/claude"
chown -R "$HOST_UID:$HOST_GID" "$HOST_HOME/.local" 2>/dev/null || true

# Inject the no-prompt flag unless explicitly disabled (SBCLAUDE_SKIP_PERMISSIONS=0).
SKIP_FLAG=()
if [ "${SBCLAUDE_SKIP_PERMISSIONS:-1}" = "1" ]; then
    SKIP_FLAG=(--dangerously-skip-permissions)
fi

# Put the user's ~/.local/bin first on PATH (claude warns when it is missing) while
# keeping the image's toolchain PATH after it.
export PATH="$HOST_HOME/.local/bin:$PATH"

# Drop to the mapped user with a correct HOME/USER/PATH environment and exec claude.
exec gosu "$HOST_UID:$HOST_GID" \
    env HOME="$HOST_HOME" USER="$USER_NAME" LOGNAME="$USER_NAME" PATH="$PATH" \
    claude "${SKIP_FLAG[@]}" "$@"
