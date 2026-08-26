<!-- markdownlint-configure-file {"MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.1/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [unreleased]

### Added

- `--claude-binary PATH` (config key `claude_binary`) mounts that `claude` build instead of the
  first one on `PATH`, which pins a session to a particular version rather than to whatever `PATH`
  reaches first. It must be an executable file, checked before the box starts, because `docker`
  would otherwise bind-mount a missing path as an empty directory and fail much later with nothing
  pointing back at the setting. Alone among the config keys it is read from the global file only,
  and a project's `pyproject.toml` cannot set it: it names what the box executes as claude, with
  `~/.claude` mounted, so a cloned repository choosing it would be arbitrary code execution on
  behalf of whoever cloned it.
- `SBCLAUDE_ALLOW_UNSUPPORTED_PLATFORM=1` turns the non-Linux refusal into a warning, for a host
  you are willing to arrange by hand. Nothing in a plain `sbclaude run` is Linux-only except the
  binary, so a macOS session may work given an ELF `claude` for the container's architecture, every
  mounted path on a filesystem Docker Desktop shares into its VM, and a shared `TMPDIR`. The device
  flags still cannot work anywhere but Linux, and the packaging metadata still declares Linux only:
  this is an escape hatch, not support. Only `1`, `on`, `true`, or `yes` lift the guard, so a stray
  `=0` does not silently disable it.

## [0.1.0] - 2026-08-26

### Added

- `--wayland` (config key `wayland`) forwards the host compositor socket into the box, re-homed
  under the container's own `XDG_RUNTIME_DIR`. Preferred over `--x11`, which hands the box a
  cookie granting access to the whole session.
- `--gpu` now also passes through the DRM render nodes (`/dev/dri/renderD*`), so Mesa on
  AMD/Intel and Vulkan/VA-API work, not only CUDA.
- `webshot`, a GPU-accelerated screenshot and visual-diff tool, is installed in the image with
  Chromium baked in, so no project needs a per-repo browser download.
- `scaffold-noclip` writes a project-side visual-regression harness driven by `webshot`
  (`viewer-tests/`, baselines, and a getting-started guide). Existing files are never overwritten
  without `--force`.
- `--sudo` (config key `sudo`) gives the mapped user passwordless `sudo` in the box, so `sudo su`
  and `sudo apt-get install` work. It drops `--security-opt no-new-privileges` for that box, which
  is unavoidable: `no_new_privs` makes the kernel ignore the setuid bit `sudo` depends on. The
  image now ships `sudo`, still with every setuid bit stripped at build time; the entrypoint
  restores it on `sudo` alone and only for a `--sudo` box.
- `sbclaude shell --root` opens the shell as root, which is what `shell` used to do
  unconditionally.
- A box that fails to start is launched again, three attempts in all, after which sbclaude exits
  with the last code and says why. A Docker exit code meaning the container never ran (125–127),
  and a session that dies within seconds, each count as a failure to start; a session that ran and
  then ended never does. The command is rebuilt for each attempt, so a GPU woken by the previous
  attempt's probe is picked up by the next. Docker's own message plus the `docker run` command
  behind it goes to syslog
  (`journalctl -t sbclaude`), which survives a terminal that does not. `-d/--debug` logs the same
  command before launching.
- `--no-fullscreen` (config key `fullscreen`) stops forcing claude's fullscreen TUI. The TUI draws
  on the terminal's alternate screen, which is discarded on exit, so a session that failed on start
  took its own error message with it.
- `--venv-dir DIR` (config key `venv_dir`) holds the box's virtualenv in `DIR` rather than beside
  the project, so pairing it with a volume in `docker_args` keeps the environment across boxes
  without writing into the project. One subdirectory per project, suffixed with a digest of its
  absolute path so a shared volume cannot mix up two projects of the same name; the entrypoint
  creates it and hands it to the mapped user, which is what makes a fresh named volume usable.
- `--no-modify` (config key `modify`) stops sbclaude writing into the project directory: no
  `/.sbclaude-venv/` line appended to `.gitignore`, the box's virtualenv redirected to
  `/tmp/sbclaude-venv-<project>-<digest>` inside the container, no start-up `uv sync` (it refreshes
  `uv.lock`), and no `cc-session-recover` install. It constrains sbclaude only — the project is
  still mounted read-write for Claude itself.

### Changed

- sbclaude now requires a Linux host, which it has needed in practice all along. The host copy of
  `claude` is bind-mounted into a Linux container and executed there, so an ELF build of it has to
  exist on the host; the identity mirroring reads `os.getuid()`; and every device and socket the
  run flags pass through is a Linux one. It now refuses to start on any other host rather than
  failing later with a Docker error, and the packaging carries an
  `Operating System :: POSIX :: Linux` classifier.
- `sbclaude shell` now opens a login shell as the user the box mirrors, rather than as root. The
  working directory is unchanged (the project).
- The box's virtualenv is only set up for a project that actually contains Python (a
  `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, or `Pipfile` at the top, or a
  `.py` file anywhere below it). Other projects no longer get a `.sbclaude-venv` directory, a
  `.gitignore` line, or any of the virtualenv environment variables.

### Fixed

- A failed image build no longer passes for a successful one. The daemon reports a failing step
  in-band as one more log chunk, which was echoed and otherwise ignored, so `sbclaude build` exited
  0 and `sbclaude run` went on to start a box on whatever stale image still carried the tag.
  `build` now exits non-zero, and `run` aborts when there is no image at all and warns loudly when
  it falls back to the previous one (a failed rebuild should not be able to stop every box over a
  transient upstream outage).
- The image build no longer fails when upstream publishes a release with no assets attached to it.
  The baksmali and smali jars were fetched from a URL built out of the latest tag, and
  `baksmali/smali` 3.0.10 carries no files, so the download 404'd and took the whole build with it.
  The asset URL is now read from the releases feed, which skips an empty or half-published release.
- `--gpg` could no longer sign once `/run/user/<uid>` existed in the box, which `--wayland` and
  `--ssh` both arrange. The agent socket was overlaid only at `~/.gnupg/S.gpg-agent`, the path
  `gpg` falls back to when it has no usable runtime directory; with one, it looked in
  `/run/user/<uid>/gnupg` instead, found nothing, and silently started its own agent inside the
  box, which can only reach `pinentry-curses` and so failed with `Inappropriate ioctl for device`
  on a TTY-less box. The socket is now overlaid at both paths.
- `--gpu` no longer starts a box that cannot run. `--gpus all` is passed only once `nvidia-smi -L`
  lists a device, retried a few times since that call is also what resumes a card parked at
  D3cold; otherwise the box starts on the DRM render nodes alone with a warning. An unresolvable
  `nvidia.com/gpu=all` is only a warning in the daemon's log, but it kills the container half a
  second later.
- `--gpu` left the GPU unusable: the supplementary device groups Docker granted were dropped when
  the entrypoint switched to the mapped user, and the driver capability set omitted `graphics`, so
  anything that rendered fell back to software silently.
- The glvnd, Vulkan, and GBM manifests the NVIDIA container toolkit does not install are now
  synthesised at start-up, so GL, EGL, and Vulkan clients find the real driver.
- `~/.config` is no longer left root-owned when something is mounted beneath it, which made Chrome
  abort at start-up.

### Removed

- The PyInstaller workflow, and with it the Windows and macOS release binaries. Its matrix held
  nothing else, and neither platform can run a box: Windows has no `os.getuid` and no `syslog`
  (which is what broke the build), and a macOS host's `claude` is a Mach-O binary that cannot
  execute inside the Linux container it would be mounted into. Linux binaries are unaffected —
  they come from the AppImage workflow.

## [0.0.1] - 2026-08-07

First release.

`sbclaude` runs Claude Code inside a throwaway Docker container with the bash sandbox and
permission prompts disabled, against a configurable set of host bind mounts.

See the README for the full feature set: RE toolchain, session recovery, MCP support, SSH and GPG
passthrough, and per-project configuration.

[unreleased]: https://github.com/Tatsh/sbclaude/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Tatsh/sbclaude/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/Tatsh/sbclaude/releases/tag/v0.0.1
