# sbclaude

<!-- WISWA-GENERATED-README:START -->

[![Python versions](https://img.shields.io/pypi/pyversions/sbclaude.svg?color=blue&logo=python&logoColor=white)](https://www.python.org/)
[![PyPI - Version](https://img.shields.io/pypi/v/sbclaude)](https://pypi.org/project/sbclaude/)
[![GitHub tag (with filter)](https://img.shields.io/github/v/tag/Tatsh/sbclaude)](https://github.com/Tatsh/sbclaude/tags)
[![License](https://img.shields.io/github/license/Tatsh/sbclaude)](https://github.com/Tatsh/sbclaude/blob/master/LICENSE.txt)
[![GitHub commits since latest release (by SemVer including pre-releases)](https://img.shields.io/github/commits-since/Tatsh/sbclaude/v0.1.0/master)](https://github.com/Tatsh/sbclaude/compare/v0.1.0...master)
[![CodeQL](https://github.com/Tatsh/sbclaude/actions/workflows/codeql.yml/badge.svg)](https://github.com/Tatsh/sbclaude/actions/workflows/codeql.yml)
[![QA](https://github.com/Tatsh/sbclaude/actions/workflows/qa.yml/badge.svg)](https://github.com/Tatsh/sbclaude/actions/workflows/qa.yml)
[![Tests](https://github.com/Tatsh/sbclaude/actions/workflows/tests.yml/badge.svg)](https://github.com/Tatsh/sbclaude/actions/workflows/tests.yml)
[![Coverage Status](https://coveralls.io/repos/github/Tatsh/sbclaude/badge.svg?branch=master)](https://coveralls.io/github/Tatsh/sbclaude?branch=master)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue?logo=dependabot)](https://github.com/dependabot)
[![Documentation Status](https://readthedocs.org/projects/sbclaude/badge/?version=latest)](https://sbclaude.readthedocs.org/?badge=latest)
[![mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![uv](https://img.shields.io/badge/uv-261230?logo=astral)](https://docs.astral.sh/uv/)
[![pytest](https://img.shields.io/badge/pytest-zz?logo=Pytest&labelColor=black&color=black)](https://docs.pytest.org/en/stable/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Downloads](https://static.pepy.tech/badge/sbclaude/month)](https://pepy.tech/project/sbclaude)
[![Stargazers](https://img.shields.io/github/stars/Tatsh/sbclaude?logo=github&style=flat)](https://github.com/Tatsh/sbclaude/stargazers)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/Tatsh/sbclaude/master.svg)](https://results.pre-commit.ci/latest/github/Tatsh/sbclaude/master)
[![Prettier](https://img.shields.io/badge/Prettier-black?logo=prettier)](https://prettier.io/)

[![@Tatsh](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpublic.api.bsky.app%2Fxrpc%2Fapp.bsky.actor.getProfile%2F%3Factor=did%3Aplc%3Auq42idtvuccnmtl57nsucz72&query=%24.followersCount&label=Follow+%40Tatsh&logo=bluesky&style=social)](https://bsky.app/profile/Tatsh.bsky.social)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Tatsh-black?logo=buymeacoffee)](https://buymeacoffee.com/Tatsh)
[![Libera.Chat](https://img.shields.io/badge/Libera.Chat-Tatsh-black?logo=liberadotchat)](irc://irc.libera.chat/Tatsh)
[![Mastodon Follow](https://img.shields.io/mastodon/follow/109370961877277568?domain=hostux.social&style=social)](https://hostux.social/@Tatsh)
[![Patreon](https://img.shields.io/badge/Patreon-Tatsh2-F96854?logo=patreon)](https://www.patreon.com/Tatsh2)

<!-- WISWA-GENERATED-README:STOP -->

`sbclaude` runs **Claude Code** inside a throwaway Docker container with the bash sandbox
and permission prompts **disabled**, against a configurable set of host bind mounts. The
container stores nothing of its own (`--rm`); everything lives on the host. You only ever
invoke `sbclaude` — it manages its own image and containers via the Docker SDK.

**The host must be Linux.** The host copy of `claude` is bind-mounted into a Linux container and
executed there, so the host must supply an ELF build of it (a Mac's own `claude` is Mach-O and
cannot run in the container); the identity mirroring reads `os.getuid()`, for which Windows has no
equivalent; and every device and socket the run flags pass through (`/dev/nvidia*`, `/dev/dri`,
`/dev/kvm`, `/dev/bus/usb`, `/tmp/.X11-unix`, `/run/user/<uid>`, `/var/run/usbmuxd`) is a Linux
one. `sbclaude` refuses to start anywhere else rather than failing later with a Docker error. That
refusal can be lifted for a host you are prepared to set up by hand — see
[Running from a non-Linux host](#running-from-a-non-linux-host) — but nothing beyond Linux is
tested or supported.

There is a single image, `sbclaude`, built on demand. It bundles everyday coding tools
(Debian slim + git, ripgrep, Node 24/Yarn, a C toolchain, gh, glab, uv, jq), formatters and
linters kept at their latest upstream release (clang-format, jsonnet, jsonnetfmt, shellcheck), Qt 6
development (`qt6-base-dev` plus ninja), `webshot` (a GPU Chrome driver for screenshots and
visual diffs, with Chromium baked in), and a mobile reverse-engineering toolchain (a JDK,
frida, mitmproxy, dex2jar, baksmali/smali, and launchers for the host-mounted Ghidra,
Android SDK, jadx, and apktool).

## How it works

- The `claude` executable is **not installed** in the image. It is a self-contained
  native binary (Node is bundled in), so the host copy is bind-mounted read-only at
  run time — the image always tracks whatever version the host has. (Only glibc ≥ 2.17
  is needed, which is why the base is Debian, not Alpine/musl.)
- Your identity is **mirrored**: `UID/GID/USER/HOME` are passed in and an entrypoint
  recreates that user inside the container. This means:
  - mounted `~/.claude` files (incl. the `0600` `.credentials.json`) are owned correctly;
  - **paths resolve identically** — a host path you paste into a prompt
    (`/home/you/dev/foo`) is bind-mounted at that _same_ absolute path inside the box.
- `--dangerously-skip-permissions` is passed (hence the non-root user — that flag
  refuses root), and a **patched copy** of your `settings.json` is mounted over the
  in-container one with `sandbox.enabled=false`, `skipDangerousModePermissionPrompt=true`
  (no bypass dialog) and `tui="fullscreen"`. **Your real settings file is never modified.**
- The image **auto-builds on first use** and **rebuilds automatically** when the packaged
  Dockerfile/entrypoint change (tracked via a content-hash label).

## Running from a non-Linux host

This is unsupported, but it can be done with caveats. Most flags such as `--gpu`, `--x11`,
`--wayland`, etc will not work.

Note that `docker` will not show an error if bind mount is missing to the daemon. You will need to
make sure all paths are correct.

### Windows

Use WSL2. Install `sbclaude` and `claude` inside the distribution, turn on Docker Desktop's WSL
integration (or run `dockerd` in the distribution itself), and keep projects on the WSL2 filesystem
rather than under `/mnt/c`.

### macOS

1. Set environment variable `SBCLAUDE_ALLOW_UNSUPPORTED_PLATFORM=1`.

1. Pass `--claude-binary` with the path to a Linux build of `claude`. This must match the container
   architecture.

1. Keep `~/.claude`, the project, and the `claude` binary under a path Docker Desktop shares into
   its VM (`/Users` is shared by default).

1. Set `TMPDIR` somewhere to somewhere shared such as `~/Library/Caches/sbclaude`.

You will probably want to use `--net bridge` instead of `--net host`.

## Install

```sh
uv tool install .         # or: pipx install .
# the image builds itself on first `sbclaude run`; or pre-build:
sbclaude build
```

## Usage

```sh
sbclaude                          # run claude; cwd is the project (writable)
sbclaude run -p ~/dev/foo         # explicit project dir (writable, becomes workdir)
sbclaude run -r /data -w ~/scratch   # extra read-only / read-write mounts
sbclaude run --re --x11           # Ghidra/Android mounts + GUI passthrough
sbclaude run -- --version         # everything after -- goes to claude
sbclaude ls                       # list running sbclaude containers
sbclaude stop [--all]             # stop this project's boxes (or all with --all)
sbclaude shell                    # debug shell in this project's box, as your user
sbclaude shell --root             # ... as root instead
sbclaude build [--no-cache]       # (re)build the image
sbclaude delete-image             # remove the sbclaude image
sbclaude config                   # show the config file path
sbclaude scaffold-noclip [DIR]    # write a noclip visual-regression harness into a project
```

### `webshot`

Inside the box, `webshot` drives a real GPU-accelerated Chromium (baked into the image, so no
project needs its own browser download), screenshots it, and compares the result against a
baseline. It is deliberately generic: it knows about browsers, waiting, settling, screenshots, and
image comparison, and nothing about any particular application — per-project knowledge is supplied
with `--wait-fn`/`--eval`.

```sh
webshot doctor                                          # report the renderer actually in use
webshot shot http://localhost:3000 --out out.png --wait '#app'
webshot check http://localhost:3000 --baseline base.png
webshot compare a.png b.png --diff d.png
```

Exit codes: `0` ok, `1` regression, `2` software renderer (i.e. the GPU is not really being used),
`3` missing baseline, `64` usage. Pair it with `--gpu`, and with `--wayland` when a headed window
is wanted.

### `scaffold-noclip`

Writes the project-side glue for a visual-regression workflow built on the `webshot` tool that
ships in the image, into `DIR` (default: the current directory):

```text
viewer-tests/nc.mjs        runner -- talks only to the app's own API, copied verbatim per project
viewer-tests/views.json    the only file you edit: which scene, and where the camera goes
viewer-tests/baselines/    approved output; effectively source, nothing can regenerate it
viewer-tests/out/          captures and diffs, rewritten every run
NEW_GAME.md                getting-started guide, including the non-obvious failure modes
```

```sh
sbclaude scaffold-noclip --scene MyGame/Level1   # substitute the scene id while writing
sbclaude scaffold-noclip ~/dev/foo --force       # overwrite existing files
```

Existing files are never overwritten without `--force`: an edited `views.json` or a blessed
baseline is judgement that cannot be regenerated.

You can run **several boxes against the same project directory at once**. Each `run` gets a
unique container name — the project name plus a short random suffix — so there is no name
collision; override it with `-n`. `sbclaude ls` lists them all, `sbclaude stop` stops every box
for the current project, and `sbclaude shell` attaches to it (or asks you to pass `-n NAME` when
more than one is running).

### `run` flags

| Flag                | Effect                                                                           |
| ------------------- | -------------------------------------------------------------------------------- |
| `--re`              | enable `--ghidra` + `--android` together                                         |
| `--ghidra`          | mount host Ghidra (`/usr/share/ghidra`) read-only                                |
| `--android`         | mount Android SDK + `~/.android` + `/dev/kvm` (adb/emulator)                     |
| `--usb`             | expose `/dev/bus/usb` for adb over USB                                           |
| `--ios`             | mount the host `usbmuxd` socket so frida reaches an iOS device over USB          |
| `--gpu`             | expose the host GPUs (NVIDIA runtime plus the DRM render nodes)                  |
| `--wayland`         | forward the Wayland socket for GUI apps (preferred over `--x11`)                 |
| `--x11`             | forward `DISPLAY` + `XAUTHORITY` for GUI apps (Ghidra GUI, jadx-gui, emulator)   |
| `--ssh`             | mount the host `~/.ssh` read-only and forward the ssh-agent, for SSH git remotes |
| `--gpg`             | mount the host GnuPG home + agent socket for signing commits                     |
| `--sudo`            | passwordless `sudo` in the box (drops `no-new-privileges`)                       |
| `--venv-dir DIR`    | put the box's virtualenv in `DIR` (e.g. a volume) instead of beside the project  |
| `--claude-binary`   | mount this `claude` build instead of the first one on `PATH`                     |
| `--no-modify`       | stop sbclaude writing anything into the project directory                        |
| `--no-fullscreen`   | do not force the fullscreen TUI, so a failing session's output survives          |
| `--session-recover` | install `cc-session-recover` (auto-resume) into the project on start             |
| `--net bridge`      | isolate the box's network (default is `host` — `localhost` = your host)          |
| `-r/-w/-p/-n/-i`    | extra ro/rw mount, project, container name, image override                       |

### When a box does not start

A box that never gets going — docker exits 125–127, or the container dies within seconds — is
launched again, up to three attempts, then sbclaude exits with the last code. A session that ran
and then ended is never retried, whatever its exit code. Every attempt is logged to syslog with
docker's own message and the `docker run` command behind it; read them with
`journalctl -t sbclaude`. The terminal cannot be trusted here, because the fullscreen TUI erases
its own screen on exit; `--no-fullscreen` keeps a failing session's output on the normal screen.

`--gpu` probes for a GPU with `nvidia-smi -L` before asking Docker for one, retrying a few times
because that probe is also what wakes a card the kernel has parked. If none answers, the box starts
without `--gpus all` (the DRM render nodes are still passed): an unresolvable `nvidia.com/gpu=all`
is a warning to the daemon but death to the container.

## Configuration

All options live under a `[tool.sbclaude]` table. They are read from the global file at
`~/.config/sbclaude/config.toml` (path from `platformdirs`) and, for `run`, overlaid with the
target project's `pyproject.toml` `[tool.sbclaude]` table — project values win, so a repo can
pin its own defaults. All keys optional:

```toml
[tool.sbclaude]
gpg = true       # mount the GnuPG home + agent for signing
network = "host" # default; "bridge" to isolate the box's network
re = true        # enable the Ghidra + Android mounts together
ssh = true       # mount ~/.ssh read-only + forward the ssh-agent for SSH git remotes
sudo = true      # passwordless sudo in the box (drops no-new-privileges)
wayland = true   # forward the Wayland socket for GUI apps
x11 = true       # forward X11 for GUI apps
# image = "custom:latest"         # force a different image
# debian_mirror = "http://ftp.us.debian.org/debian" # apt mirror for image builds
# memory = "8g"                   # override the auto host-RAM cap ("0" disables)
# cpus = "4"                      # cap CPUs (uncapped by default)
# recover = true                  # install cc-session-recover into every project (off by default)
# modify = false                  # never write into the project directory (same as --no-modify)
# venv_dir = "/venv-cache"        # hold the box's virtualenv here (pair with a docker_args volume)
# claude_binary = "~/bin/claude"  # mount this claude build rather than the first one on PATH
# fullscreen = false              # do not force the fullscreen TUI (keeps start-up errors visible)
pass_env = ["AWS_REGION"]                          # forward host vars (AWS_PROFILE is default)
ro = ["~/dev*", "~/ghidra_scripts", "~/Downloads"] # read-only mounts (globs + ~ ok)
rw = []                                            # the project dir is always rw automatically

[tool.sbclaude.env] # inject fixed vars (e.g. Amazon Bedrock)
CLAUDE_CODE_USE_BEDROCK = "1"
```

The toggle keys `re`, `ghidra`, `android`, `gpu`, `usb`, `ios`, `wayland`, `x11`, `ssh`, `gpg`,
and `sudo` mirror the matching `run` flags and default to `false`; setting one is the same as
always passing that flag.

**Debian mirror** — when `deb.debian.org` is slow, point image builds at a faster archive
mirror with `debian_mirror` (or `--debian-mirror` on `build`/`run`). Only the image's main
archive URI is rewritten; `debian-security` keeps pointing at `deb.debian.org`.

`ro`/`rw` accept **globs** (`~/dev*` → every matching dir) and `~`; non-matching or
missing paths are dropped. Every path is mounted at its real absolute path, so a host
path you paste into a prompt resolves inside the box. The project (cwd or `-p`) is always
read-write and overlays any read-only parent (e.g. `~/dev` ro + `~/dev/proj` rw → `proj`
is writable).

**Environment variables** — inject with the `[tool.sbclaude.env]` table, forward host values
by name with `pass_env`, or per-run with `-e KEY=VALUE` (precedence: managed defaults <
`[tool.sbclaude.env]` < `pass_env` < `-e`). `AWS_PROFILE` is forwarded by default. This is how
you point the box at a different backend such as **Amazon Bedrock**
(`CLAUDE_CODE_USE_BEDROCK=1` + your `AWS_*` vars; mount `~/.aws` via `ro` if you use
profiles).

**uv project environment** — by default the box sets `UV_PROJECT_ENVIRONMENT` to
`.sbclaude-venv`, so `uv sync`/`uv run` build the virtualenv there instead of writing `.venv`
into your bind-mounted project. A host-built `.venv` hard-codes the host interpreter path and
would not resolve in the box anyway, so the two are kept apart; the host's `.venv` is
additionally re-mounted read-only so nothing in the box can corrupt it. Beside the project
rather than inside the container because the container is `--rm`, so a virtualenv in it would
be rebuilt from nothing every run. `/.sbclaude-venv/` is appended to the project's `.gitignore`
(once, and only when it is not already there) so it stays out of `git status`. Override the
path with `venv_dir` (below) or by setting your own `UV_PROJECT_ENVIRONMENT` (via `[env]` or
`-e`), or disable the behaviour entirely with `manage_uv_env = false`.

None of it happens unless the project **is** a Python one: a `pyproject.toml`, `setup.py`,
`setup.cfg`, `requirements.txt`, or `Pipfile` at the top, or a `.py` file anywhere below it
(ignoring dotted directories, `node_modules`, `vendor`, and `target`). A project with no Python
in it gets no `.sbclaude-venv`, no `.gitignore` line, and no virtualenv variables at all.

There is no clean equivalent for Node — `node_modules` is fixed to the package root by Node's
resolver (only Yarn Berry's `YARN_NODE_LINKER=pnp` removes it, at the cost of changing module
resolution), and `node_modules` built on the box's Linux is usually reusable on a Linux host
anyway, so it is left untouched.

**A virtualenv that outlives the box** — `--venv-dir DIR` (config key `venv_dir`) puts the box's
virtualenv in `DIR` instead of beside the project. Combined with a volume in `docker_args`, the
environment survives the box without anything being written into the project:

```toml
[tool.sbclaude]
docker_args = ["-v", "sbclaude-venv-cache:/venv-cache"]
venv_dir = "/venv-cache"
```

`DIR` gets one subdirectory per project, named after it with a digest of its absolute path
appended (`/venv-cache/sbclaude-venv-foo-1a2b3c`), so a single volume serves every project and
`~/dev/foo` and `~/work/foo` do not end up sharing an environment. The directory is created and
handed to your user by the entrypoint, which is what makes a fresh (root-owned) named volume
usable. `venv_dir` applies even with `manage_uv_env = false` — naming a directory is asking for
the environment to be managed — but not to a project with no Python in it.

**Leaving the project untouched** — `--no-modify` (or `modify = false`) stops sbclaude itself
writing anything into the directory it was launched from. Three things are suppressed:

- the `/.sbclaude-venv/` line is not appended to `.gitignore`;
- `UV_PROJECT_ENVIRONMENT` points inside the container instead of beside the project, so neither
  sbclaude nor a later `uv sync` creates a virtualenv there. That path is under `/tmp` and dies
  with the box, unless `venv_dir` names somewhere that persists;
- the start-up `uv sync` is skipped (it refreshes `uv.lock`), as is the `cc-session-recover`
  install, which writes into `.claude/` and `HANDOFF.md`. Asking for both `--session-recover` and
  `--no-modify` prints a note and leaves recovery off.

This constrains **sbclaude**, not Claude: the project is still bind-mounted read-write and the
agent can edit it exactly as before.

**Which `claude` gets mounted** — by default, whichever one `PATH` reaches first.
`--claude-binary PATH` (config key `claude_binary`) names one instead, which is how you pin a
session to a particular build rather than to whatever `PATH` reaches first. It must be an
executable file — that is checked up front, because `docker` would otherwise bind-mount a missing
path as an empty directory and fail much later with nothing pointing back at the setting. Like
every other mount, it has to exist on the machine the daemon runs on.

Unlike every other key, `claude_binary` is read from the **global** config only: a project's
`pyproject.toml` cannot set it. It names what the box executes as claude, with your `~/.claude`
credentials mounted, so letting a cloned repository choose it would be arbitrary code execution on
behalf of whoever cloned it.

**Alternate config dir** — if `CLAUDE_CONFIG_DIR` is set on the host, sbclaude mounts that
directory (its `.claude.json`, `settings.json`, history) and points claude at it inside the
box instead of `~/.claude`.

## Session recovery

[cc-session-recover](https://github.com/softcane/cc-session-recover) lets Claude Code pick a
long-running task back up after a quota or rate-limit pause. It is **off by default** — enable
it per run with `--session-recover`, or for every run with `recover = true` in the config. When
enabled, the container entrypoint runs the tool's `install-into-project.sh` against the project
before launching claude, which sets up its `SessionStart` and `Stop` hooks and a `HANDOFF.md`.

The tool is **vendored into the image as a git clone** (not the npm package) at
`/opt/cc-session-recover`, with [PR #2](https://github.com/softcane/cc-session-recover/pull/2)
(safer watcher argument handling and the opt-in `CC_REMIND_MODE` prompt-injection limit) applied
on top. Two further patches keep the installer tidy: it no longer copies `settings.example.json`
into the project, and its own `.gitignore` handling is disabled. The installer writes into the
project's `.claude/` (hooks and `settings.local.json`, merged with `jq`) and a `HANDOFF.md`;
because the project is bind-mounted read-write, **those files land in your real repository and
persist**, which is why this is opt-in. Afterwards the entrypoint appends the recovery artifacts
(`HANDOFF.md`, `auto-continue.md`, `session-recover.js`, `session-recover.yaml`,
`standing-instructions.md`, `statusline-quota-cache.sh`, and the three hook scripts) to the
project's `.gitignore`, each only when absent. If the install
fails, the box aborts rather than starting a session that silently lacks recovery.

## MCP servers

MCP server configs live in your mounted `~/.claude.json`, so they carry into the box — but
the server **command must be runnable inside the container**. A host Python _venv_ won't
work: its `bin/python` symlinks to a host-only interpreter (e.g. `/usr/bin/python3.13`),
which doesn't exist in the box → `ENOENT`. The image ships **Python 3 in a virtualenv at
`/opt/venv` (with the `mcp` SDK and `pre-commit` pre-installed) and `uv`**, so point the
command at one of:

- `uv run /abs/path/to/server.py` — best: reads the script's PEP 723 deps, works on the
  host too. Example: `claude mcp add ghidra -- uv run ~/dev/ghidra-mcp/bridge_mcp_ghidra.py`.
- `/opt/venv/bin/python3 /abs/path/to/server.py` — the container's venv Python (has `mcp`
  pre-installed); container-only. `/opt/venv/bin` is first on `PATH`, so a bare `python3`
  resolves here too.

A server that talks to a process on the host (e.g. a Ghidra GUI on `localhost`) works out
of the box because the box defaults to host networking; pass `--net bridge` only if you
want to isolate it.

## Git over SSH and commit signing

Pushing over SSH and signing commits need the host's private keys, which are **not** mounted
by default (the box is a fully-autonomous, no-prompt agent — see [Hardening](#hardening)).
Opt in per run, or globally with `ssh = true` and `gpg = true` under `[tool.sbclaude]`:

- `--ssh` bind-mounts `~/.ssh` **read-only**, so SSH remotes authenticate with the host's
  keys and `known_hosts`. Read-only means newly-learnt host keys are not written back. It also
  forwards the host's **ssh-agent**: `$SSH_AUTH_SOCK` is bind-mounted at its own path and set
  in the box, so keys that are passphrase-protected or held in a hardware token still work —
  the host agent does the signing and the secret never enters the container. Without this a
  box holding only encrypted key files would stall on a passphrase prompt nothing can answer.
  Symlinks inside `~/.ssh` are followed too: a `config` (or key) linked into a dotfiles
  repository has its target mounted read-only at the same path, so the link does not dangle
  and every `Host` alias keeps working.
- `--gpg` bind-mounts the host **GnuPG home** (read-write — `gpg` needs to write lock files
  and the trustdb) and overlays the host's live **gpg-agent socket** at both of the places the
  box's `gpg` may look for it: `~/.gnupg/S.gpg-agent` and `/run/user/<uid>/gnupg/S.gpg-agent`.
  Which one applies depends on whether `/run/user/<uid>` exists in the box, and `--wayland` and
  `--ssh` both make it exist, so both are mounted rather than guessed. The host agent performs
  the signing and owns the secret keys, so a cached passphrase carries over and any pinentry
  prompt appears on the host. The image ships `gnupg` and `openssh-client`; your mounted
  `~/.gitconfig` (with `user.signingkey`/`commit.gpgsign`) does the rest.

```sh
sbclaude run --ssh --gpg -p ~/dev/foo   # inside: git push, git commit -S both work
```

Your `~/.gitconfig` is always mounted read-only, along with every file it pulls in via an
`[include] path = ...` directive (resolved with `git config --includes`), so a split config
carries into the box intact.

## Root inside the box (`--sudo`)

The box runs as **your** mirrored user, and so does `sbclaude shell` — it opens a login shell as
that user, in the project directory. `sbclaude shell --root` gives a root shell instead; that one
asks the Docker daemon to start the process as root, so it works on any box, escalating nothing
inside it.

`sudo` is a different matter, because it is what lets the **agent** become root — to
`apt-get install` a missing library, say. It is off by default and enabled with `--sudo` (or
`sudo = true`). The box then gets a `NOPASSWD: ALL` sudoers drop-in for your user, so `sudo su`
and `sudo <anything>` work without a password:

```sh
sbclaude run --sudo -p ~/dev/foo   # inside: sudo su, sudo apt-get install ... both work
```

The catch, and why this is a flag rather than the default: `sudo` is setuid-root, and the
`no_new_privs` kernel flag that `--security-opt no-new-privileges` sets makes the kernel **ignore**
the setuid bit on every `execve` — `sudo` detects this and refuses to run. The two cannot coexist,
so **`--sudo` drops `no-new-privileges`** from the hardening set. Everything else stays: the
capability set is still `ALL` dropped plus the same six, so container root here has no
`CAP_SYS_ADMIN`, no `CAP_MKNOD`, and no `CAP_NET_ADMIN`. What it does gain is `CAP_DAC_OVERRIDE`
over every mounted path, i.e. the file permissions on your read-write mounts stop being a
boundary. Read-only mounts stay read-only — that is enforced by the kernel, not by permissions.

The image ships `sudo` but still strips **every** setuid bit at build time; the entrypoint
restores it on `sudo` alone, and only for a box started with `--sudo`. A box started without it
therefore contains no setuid binary at all.

## RE toolchain (`--re`)

Mounted from the host (your exact versions): **Ghidra** (`analyzeHeadless`, `ghidraRun`),
**Android SDK** (`adb`, `emulator`, `sdkmanager`, `avdmanager`), **jadx**, **apktool**,
plus `~/.android` (adb keys + AVDs) and `/dev/kvm` for emulator acceleration.

Installed in the image: **Temurin JDK 21** (Ghidra 12 needs it), **build-essential** +
binutils, **frida** + frida-tools (pinned to the host version), **mitmproxy**,
**dex2jar**, **baksmali/smali**, CLI **audio tools** (ffmpeg, sox, flac, vorbis-tools,
opus-tools, lame, mpg123, wavpack, shntool, plus **vgmstream-cli** for game audio), CLI
**image tools** (ImageMagick, zbar, **deark**, **pngdefry** for `-iphone` PNGs),
**czkawka-cli** (duplicate/similar finder), and the X11/GL/audio libs the mounted GUI
binaries need.

```sh
sbclaude run --re --x11 -p ~/dev/some-apk-re
#   inside: jadx -d work/jadx-out base/classes*.dex
#           apktool d base -o work/axml-decoded
#           analyzeHeadless ~/dev/x-re proj -import lib/arm64-v8a/foo.so
#           adb devices ; emulator -avd ford-x86_64-api35 -writable-system &
#           frida -U -f com.x.y -l hook.js
```

`--x11` mounts `/tmp/.X11-unix` + your session xauth cookie and sets `DISPLAY`/
`XAUTHORITY`. Java/Swing (Ghidra) renders through XWayland (`:0`). If a GUI fails with a
cookie error, run on the host: `xhost +SI:localuser:$USER`.

`--wayland` is the better option when the app speaks Wayland: it forwards the compositor
socket alone, and a Wayland client cannot read other windows or inject input into them, whereas
an X11 cookie grants exactly that over the whole session. The socket is re-homed under the
box's own `/run/user/<uid>` and `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` are set to match.

`--gpu` uses the NVIDIA container runtime when it is present and also passes through the DRM
render nodes (`/dev/dri/renderD*`), so Mesa on AMD/Intel and Vulkan/VA-API work too. The
entrypoint re-adds the device groups Docker granted, and synthesises the glvnd/Vulkan/GBM
manifests the NVIDIA toolkit does not install, so GL, EGL, and Vulkan clients get the real
driver instead of a software fallback.

`--ios` targets an **iOS** device attached to the host. The host runs `usbmuxd` (it owns
the USB device), and frida's usbmux backend reaches the device through that daemon's
socket — so instead of claiming raw USB (which would clash with the host `usbmuxd`),
`--ios` bind-mounts `/var/run/usbmuxd` plus the host's `/var/lib/lockdown` pairing
records. Combine with `--re` so frida is present:

```sh
sbclaude run --re --ios -p ~/dev/some-ios-re
#   inside: frida-ls-devices            # the host's device shows up over usbmux
#           frida -U -f com.x.y -l hook.js
```

The host needs `usbmuxd` running and the device already paired (trusted). If frida sees
no device, confirm `idevice_id -l` works on the host first.

## Hardening

This deliberately removes Claude's _own_ sandbox and permission prompts, so the box leans
on Docker for confinement instead. Every run is hardened by default (defense-in-depth —
it limits blast radius, it is not a guarantee):

- **Build time:** minimal Debian slim, `--no-install-recommends` + cleaned apt lists, no
  secrets baked in (the `claude` binary and all auth are bind-mounted), OCI provenance
  labels, and **all setuid/setgid bits stripped** from the image.
- **Run time:** `--security-opt no-new-privileges` (unless `--sudo` asked for the opposite —
  see [Root inside the box](#root-inside-the-box---sudo)), `--cap-drop ALL` plus only the six
  caps the root entrypoint needs to create the mapped user, drop to it via gosu, and let
  `tini` (PID 1) forward signals such as `SIGWINCH` to the non-root child
  (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `KILL`, `SETUID`, `SETGID`), a `--pids-limit`, a non-root
  mapped user, and the default seccomp/AppArmor profiles (never disabled). The claude
  process itself ends up with an **empty effective capability set**.
- **Can't lock the host:** `--pids-limit` stops fork bombs, and `--memory` with an equal
  `--memory-swap` bounds RAM with no extra swap, so a runaway box is OOM-killed instead of
  thrashing the host into a freeze. The default cap is derived from host RAM (reserving the
  larger of 2 GiB or an eighth for the host); set `memory` to override or `"0"` to disable,
  and `cpus` to cap CPU (uncapped by default — saturation slows but does not lock). These
  are enforced by **cgroups**, i.e. the systemd cgroup driver on a systemd host — a separate
  `systemd-run` wrapper is unnecessary (and would not bound the container, which runs under
  the Docker daemon's cgroup, not the CLI's).

Disable per run with `--no-harden`, globally with `harden = false` in config (this also
drops the resource caps), and add your own Docker flags (e.g. `--read-only`) via
`docker_args = [...]`.

Still: the containerized Claude can run any command and read/write every mounted path
without asking, with unrestricted network. Keep writable mounts minimal (the config
defaults to read-only for everything but the project) and don't mount secrets you don't
want a fully-autonomous agent to touch. This is why `--ssh`, `--gpg`, and `--sudo` are opt-in:
the first two expose your private SSH and GPG key material (the GnuPG home read-write) to that
agent, and the third hands it container root. Forwarding an agent socket does not hand over the
secret itself, but it does let the box ask the host agent to sign with any key it holds, for as
long as the box runs.
