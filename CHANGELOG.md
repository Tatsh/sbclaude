<!-- markdownlint-configure-file {"MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [unreleased]

### Added

- Run several boxes against the same project directory at once; each `run` gets a uniquely
  suffixed container name (override with `-n`).
- `--ios` flag on `run`, mounting the host `usbmuxd` socket and `/var/lib/lockdown` so frida
  reaches an iOS device over USB.
- `--debian-mirror` option on `build` and `run`, plus a `debian_mirror` config key, to build the
  base image against an alternate Debian archive mirror.
- `--ssh` and `--gpg` flags on `run`, opt-in mounts of the host `~/.ssh` (read-only) and the host
  GnuPG home and agent socket for SSH git remotes and signed commits.
- The box now points `UV_PROJECT_ENVIRONMENT` at a container-local path by default so `uv` does
  not write a `.venv` into the bind-mounted project; opt out with the `manage_uv_env` config key.
- `--session-recover` flag on `run`, plus a `recover` config key (off by default), to install
  cc-session-recover into the project on session start so a crashed session auto-resumes; the
  recovery artifacts are added to the project's `.gitignore`.
- Per-project configuration: the global `~/.config/sbclaude/config.toml` is now overlaid by the
  target project's `pyproject.toml` `[tool.sbclaude]` table, with project values winning (the
  `env` tables are merged key by key).
- The image now ships the Qt 6 development packages (`qt6-base-dev`, `qt6-base-dev-tools`) and
  `ninja-build`.

### Changed

- **Breaking:** configuration now lives under a `[tool.sbclaude]` table instead of top-level keys,
  and `default_flags` is removed in favour of individual boolean keys (`re`, `ghidra`, `android`,
  `usb`, `ios`, `x11`, `ssh`, and `gpg`).
- **Breaking:** `--re` no longer selects a separate image; it now enables the `--ghidra` and
  `--android` host mounts together. The reverse-engineering toolchain is part of the single
  `sbclaude` image.
- `stop` with no arguments now stops every box for the current project, and `shell` selects the
  project's box automatically, asking for `-n NAME` when more than one is running.
- The base image now ships Node.js 24 with Yarn (via Corepack), the MCP SDK in `/opt/venv`,
  `gnupg`, `openssh-client`, and `jq`.

### Removed

- **Breaking:** the separate `sbclaude-re` image, now merged into the single `sbclaude` image.
- **Breaking:** the `--no-re` flag on `build`, which is no longer meaningful with a single image.

### Fixed

- Claude Code failing to start in the container because the managed settings file was not readable
  by the mapped non-root user (`EACCES`).
- `tini` (PID 1) crashing with `Unexpected error when forwarding signal: 'Operation not permitted'`
  when a terminal resize (`SIGWINCH`, e.g. switching tabs over SSH/tmux) had to be forwarded to the
  non-root child, by adding the `KILL` capability to the hardened capability set.

## [0.0.1] - 2026-00-00

First version.

[unreleased]: https://github.com/Tatsh/sbclaude/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/Tatsh/sbclaude/releases/tag/v0.0.1
