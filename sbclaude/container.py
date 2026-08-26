"""
Docker orchestration for sbclaude.

Management (build, list, stop, inspect) uses the docker-py SDK. The interactive
``run`` and ``shell`` sessions hand off to the ``docker`` binary because docker-py's
interactive TTY attach is unreliable; users still only ever invoke ``sbclaude``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING
import codecs
import getpass
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import subprocess as sp
import sys
import syslog
import tempfile
import time

import docker
import docker.errors

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

log = logging.getLogger(__name__)

__all__ = ('IMAGE_BASE', 'LABEL', 'RunSpec', 'build_images', 'config_dir', 'default_name',
           'delete_images', 'ensure_image', 'gpu_ready', 'image_exists', 'image_up_to_date',
           'is_python_project', 'list_managed', 'project_containers', 'run', 'shell', 'stop',
           'transient_uv_project_environment', 'unique_name', 'uv_project_environment',
           'uv_project_environment_in')

LABEL = 'sbclaude.managed'
"""Docker label marking a container as sbclaude-managed."""
PROJECT_LABEL = 'sbclaude.project'
"""Docker label recording a container's project path."""
HASH_LABEL = 'sbclaude.context_hash'
"""Docker label storing the build context hash for staleness checks."""
IMAGE_BASE = 'sbclaude:latest'
"""Tag of the sbclaude image."""
HARDENING_ARGS = ('--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE',
                  '--cap-add', 'FOWNER', '--cap-add', 'KILL', '--cap-add', 'SETUID', '--cap-add',
                  'SETGID', '--pids-limit', '4096')
"""Hardening arguments to Docker."""
NO_NEW_PRIVILEGES_ARGS = ('--security-opt', 'no-new-privileges')
"""
Hardening argument barring privilege escalation, dropped by ``--sudo``.

``no_new_privs`` is a latching per-process kernel flag inherited by every child, and once it is
set the kernel ignores the setuid bit on ``execve``. A setuid-root ``sudo`` is therefore inert
under it (it says so and exits 1), so passwordless escalation and this flag cannot coexist.
"""
USBMUXD_SOCKET = Path('/var/run/usbmuxd')
"""Host usbmuxd socket that frida's usbmux backend uses to reach an iOS device."""
LOCKDOWN_DIR = Path('/var/lib/lockdown')
"""Host lockdownd pairing-records directory, mounted read-only so iOS pairing carries in."""
GHIDRA_DIR = Path('/usr/share/ghidra')
"""Host Ghidra installation, mounted read-only when Ghidra is enabled."""
PENTOO_TOOL_DIRS = (Path('/opt/jadx-bin'), Path('/opt/apktool'))
"""Host jadx and apktool installations (::pentoo paths), mounted read-only."""
ANDROID_SDK_DIR = Path('/opt/android-sdk')
"""Host Android SDK location used when ``ANDROID_HOME`` is unset."""
ANDROID_SDK_UPDATE_MANAGER_DIR = Path('/opt/android-sdk-update-manager')
"""Host Android SDK update manager directory (::pentoo path), mounted read-only."""
KVM_DEVICE = Path('/dev/kvm')
"""Host KVM device, passed through for Android emulator acceleration."""
USB_DEVICE_DIR = Path('/dev/bus/usb')
"""Host USB device tree, exposed for adb over USB."""
NVIDIA_DEVICES = (Path('/dev/nvidia0'), Path('/dev/nvidiactl'))
"""Host NVIDIA device nodes whose groups the box joins for GPU access."""
GPU_PROBE = ('nvidia-smi', '-L')
"""
Command that both wakes an NVIDIA GPU and reports whether it can be used.

:meta hide-value:
"""
_GPU_PROBE_ATTEMPTS = 3
"""
How many times the GPU is probed before the box is started without it.

:meta hide-value:
"""
_GPU_PROBE_DELAY_SECONDS = 1.0
"""
Pause between GPU probes, giving a card resuming from D3cold time to answer.

:meta hide-value:
"""
_GPU_PROBE_TIMEOUT_SECONDS = 5.0
"""
How long a single GPU probe may take; a wedged driver can hang one indefinitely.

:meta hide-value:
"""
DRI_DEVICE_DIR = Path('/dev/dri')
"""Host DRM device directory; its render nodes are passed through for GPU rendering."""
VENV_DIR_NAME = '.sbclaude-venv'
"""Name of the box's virtualenv directory, created beside the host's ``.venv``."""
HOST_VENV_DIR_NAME = '.venv'
"""Name of the host's virtualenv directory, which the box mounts read-only."""
GIT_EXCLUDE_ENTRY = f'/{VENV_DIR_NAME}/'
"""Entry written to ``.gitignore`` so the box's virtualenv never shows up in git status."""
PYTHON_MARKERS = ('Pipfile', 'pyproject.toml', 'requirements.txt', 'setup.cfg', 'setup.py')
"""
Files whose presence marks a project as Python, matching what the entrypoint provisions on.

:meta hide-value:
"""
_UNSEARCHED_DIRS = frozenset({'__pycache__', 'node_modules', 'target', 'vendor'})
"""
Directories skipped when looking for a ``.py`` file, along with every dotted one.

:meta hide-value:
"""
_NAME_SANITIZE = re.compile(r'[^a-zA-Z0-9_.-]')
"""Pattern matching characters not allowed in a derived container or path name."""
_START_FAILURE_REASONS = {
    125: 'docker refused to start it',
    126: 'the container command could not be executed',
    127: 'the container command was not found'
}
"""
Exit codes the docker CLI reserves for a container that never ran, and what each means.

:meta hide-value:
"""
_QUICK_EXIT_SECONDS = 5.0
"""
Below this, a session is treated as having failed on start rather than having been ended.

:meta hide-value:
"""
_SIGNAL_EXIT_BASE = 128
"""
Exit codes at or above this report death by signal, such as a session ended with Ctrl-C.

:meta hide-value:
"""
_STDERR_TAIL_CHARS = 4096
"""
How much of docker's stderr is kept for the log; its diagnoses are a line or two.

:meta hide-value:
"""
_LAUNCH_ATTEMPTS = 3
"""
How many times a box that never starts is launched before giving up.

:meta hide-value:
"""
_LAUNCH_RETRY_SECONDS = 2.0
"""
Pause between launch attempts, leaving the daemon and a waking GPU time to settle.

:meta hide-value:
"""


@dataclass
class RunSpec:
    """Fully-resolved description of a ``run`` invocation."""
    name: str
    """Container name."""
    project: Path
    """Project path (read-write, becomes the working directory)."""
    extra_args: list[str] = field(default_factory=list)
    """Extra arguments passed to ``docker run``."""
    claude_args: tuple[str, ...] = ()
    """Arguments forwarded to ``claude``."""
    claude_binary: str | None = None
    """Host ``claude`` executable to mount, or ``None`` to take the first one on ``PATH``."""
    cpus: str | None = None
    """Docker CPU limit (e.g. ``4``); ``None`` leaves the CPU uncapped."""
    debian_mirror: str | None = None
    """Debian archive mirror for an auto-triggered image build, or ``None`` for the default."""
    env: dict[str, str] = field(default_factory=dict)
    """Environment variables injected into the box."""
    fullscreen: bool = True
    """Whether to force claude's fullscreen TUI in the patched settings."""
    harden: bool = True
    """Whether to apply the container hardening flags."""
    image: str | None = None
    """Image override, or ``None`` to auto-select."""
    memory: str | None = None
    """Docker memory limit (e.g. ``8g``); ``None`` auto-caps from host RAM, ``0`` disables."""
    network: str = 'host'
    """Docker network mode."""
    recover: bool = False
    """Whether to install cc-session-recover (auto-resume) into the project on start."""
    ro: list[Path] = field(default_factory=list)
    """Read-only mount paths."""
    rw: list[Path] = field(default_factory=list)
    """Read-write mount paths."""
    use_android: bool = False
    """Whether to mount the Android SDK and related devices."""
    use_gpg: bool = False
    """Whether to mount the host GnuPG home and agent socket for commit signing."""
    use_ghidra: bool = False
    """Whether to mount the host Ghidra installation read-only."""
    use_gpu: bool = False
    """Whether to expose the host NVIDIA GPU(s) via the NVIDIA Container Toolkit."""
    use_ios: bool = False
    """Whether to mount the host usbmuxd socket so frida can reach an iOS device."""
    use_re: bool = False
    """Whether to enable the reverse-engineering host mounts (Ghidra and Android together)."""
    use_ssh: bool = False
    """Whether to mount the host ``~/.ssh`` read-only and forward the ssh-agent socket."""
    use_sudo: bool = False
    """Whether to allow passwordless ``sudo`` in the box (drops ``no-new-privileges``)."""
    use_usb: bool = False
    """Whether to expose ``/dev/bus/usb`` for adb over USB."""
    use_wayland: bool = False
    """Whether to forward the Wayland compositor socket."""
    use_x11: bool = False
    """Whether to forward X11 ``DISPLAY`` and ``XAUTHORITY``."""


def default_name(project: Path) -> str:
    """
    Derive a container name from a project directory.

    Parameters
    ----------
    project : Path
        The project directory.

    Returns
    -------
    str
        A docker-safe container name.
    """
    return f'sbclaude-{_NAME_SANITIZE.sub("-", project.name)}'


def uv_project_environment(project: Path) -> str:
    """
    Derive the box's virtualenv path for a project.

    The box gets its own virtualenv, kept beside the project's rather than inside a container
    path. A virtualenv records absolute interpreter paths, so one built on the host cannot run
    in the box and one built in the box cannot run on the host; sharing ``.venv`` between them
    leaves whichever ran last with a broken interpreter. Two directories mean neither disturbs
    the other.

    Beside the project rather than under ``/tmp`` so that it survives the box: the container is
    always run with ``--rm``, so a virtualenv anywhere in the container's own filesystem would be
    rebuilt from nothing on every start.

    Parameters
    ----------
    project : Path
        The project directory.

    Returns
    -------
    str
        The absolute path of the box's virtualenv.
    """
    return str(project / VENV_DIR_NAME)


def uv_project_environment_in(directory: str, project: Path) -> str:
    """
    Derive the box's virtualenv path inside a directory that is not the project.

    The directory holds one subdirectory per project, named after it and suffixed with a digest
    of its absolute path. The digest is what keeps two projects of the same name (``~/dev/foo``
    and ``~/work/foo``) from sharing one environment when a single volume is mounted for all of
    them, which would leave each ``uv sync`` replacing the other's packages.

    Parameters
    ----------
    directory : str
        An absolute directory inside the container, typically a mounted volume.
    project : Path
        The project directory.

    Returns
    -------
    str
        The absolute path of the box's virtualenv.
    """
    digest = hashlib.blake2b(str(project).encode(), digest_size=3).hexdigest()
    return f'{directory.rstrip("/")}/sbclaude-venv-{_NAME_SANITIZE.sub("-", project.name)}-{digest}'


def transient_uv_project_environment(project: Path) -> str:
    """
    Derive a container-local virtualenv path for a box that must not write to the project.

    Inside the container rather than beside the project, so ``--no-modify`` holds: nothing is
    created in the project directory. The container is run with ``--rm``, so this virtualenv does
    not survive the box and is rebuilt on the next start; ``venv_dir`` pointed at a mounted volume
    is what buys persistence back.

    Parameters
    ----------
    project : Path
        The project directory.

    Returns
    -------
    str
        The absolute path of the box's virtualenv, inside the container.
    """
    return uv_project_environment_in('/tmp', project)  # noqa: S108


def _host_venv_args(project: Path) -> list[str]:
    # Re-mount the host's virtualenv read-only over itself, so that nothing in the box can write
    # to it. The bind mount of the project is read-write (the agent has to be able to edit the
    # code), which would otherwise leave the host's interpreter, its installed packages, and its
    # pyvenv.cfg writable by an agent running with permissions skipped. Read-only rather than
    # hidden so that reading it still works, for example to see which packages the host resolved.
    venv = project / HOST_VENV_DIR_NAME
    if not venv.is_dir():
        return []
    return [*_v(venv, ro=True)]


def is_python_project(project: Path) -> bool:
    """
    Report whether a project is a Python one, and so needs a virtualenv at all.

    True when one of ``PYTHON_MARKERS`` sits at the top level, or when any ``.py`` file
    exists anywhere below it. The scan stops at the first hit and skips dotted directories along
    with a few large build and dependency trees, none of which say anything about the project's
    own language.

    Parameters
    ----------
    project : Path
        The project directory.

    Returns
    -------
    bool
        ``True`` when the project looks like Python.
    """
    if any((project / marker).is_file() for marker in PYTHON_MARKERS):
        return True
    for _, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in _UNSEARCHED_DIRS]
        if any(f.endswith('.py') for f in files):
            return True
    return False


def exclude_venv_from_git(project: Path) -> bool:
    """
    Add the box's virtualenv to the project's ``.gitignore``.

    Parameters
    ----------
    project : Path
        The project directory.

    Returns
    -------
    bool
        ``True`` if the entry was added, ``False`` if it was already there or the project is not
        a git repository.
    """
    if not (project / '.git').exists():
        return False
    gitignore = project / '.gitignore'
    try:
        existing = gitignore.read_text().splitlines() if gitignore.is_file() else []
    except OSError as e:
        sys.stderr.write(f'sbclaude: could not read {gitignore}: {e}\n')
        return False
    if GIT_EXCLUDE_ENTRY in existing:
        return False
    # Separate from whatever came before, so the entry cannot be appended onto an unterminated
    # final line and silently change that pattern instead.
    prefix = '\n' if existing and existing[-1].strip() else ''
    try:
        with gitignore.open('a') as f:
            f.write(f'{prefix}{GIT_EXCLUDE_ENTRY}\n')
    except OSError as e:
        sys.stderr.write(f'sbclaude: could not update {gitignore}: {e}\n')
        return False
    return True


def unique_name(project: Path) -> str:
    """
    Derive a unique container name for a project.

    A short random suffix lets several boxes run against the same project
    directory at once without colliding on the Docker container name.

    Parameters
    ----------
    project : Path
        The project directory.

    Returns
    -------
    str
        A docker-safe container name unique to this invocation.
    """
    return f'{default_name(project)}-{secrets.token_hex(3)}'


def claude_binary(override: str | None = None) -> Path:
    """
    Locate the real host ``claude`` binary, resolving any version symlink.

    ``override`` names a build directly instead of taking whichever one ``PATH`` happens to reach
    first. That is how a session is pinned to a particular version, or given an ELF build on a host
    whose own ``claude`` is not one.

    Parameters
    ----------
    override : str | None
        Path to the executable to use, with ``~`` expanded, or ``None`` to search ``PATH``.

    Returns
    -------
    Path
        The resolved binary path.

    Raises
    ------
    FileNotFoundError
        If ``claude`` is not on ``PATH``, or ``override`` does not name an executable file.
    """
    if override:
        # Checked here rather than left to docker, which would bind-mount a missing path as an
        # empty directory and fail much later with nothing pointing back at this setting.
        candidate = Path(override).expanduser()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            msg = f'{candidate} is not an executable file'
            raise FileNotFoundError(msg)
        return candidate.resolve()
    if not (found := which('claude')):
        msg = "'claude' not found on PATH"
        raise FileNotFoundError(msg)
    return Path(found).resolve()


def _patched_settings(settings: Path, *, fullscreen: bool = True) -> Path:
    """
    Write a copy of ``settings.json`` patched for the no-prompt box.

    The bash sandbox is disabled, the dangerous-mode permission prompt is skipped, and the TUI is
    forced to fullscreen unless ``fullscreen`` is false. Leaving it as the host has it keeps a
    claude that dies on start from taking its own error message down with the alternate screen.

    Parameters
    ----------
    settings : Path
        The host ``~/.claude/settings.json``.
    fullscreen : bool
        Whether to force the fullscreen TUI.

    Returns
    -------
    Path
        A temp file to bind-mount over the in-container settings.
    """
    data = json.loads(settings.read_text(encoding='utf-8')) | {
        'sandbox': {
            'enabled': False
        },
        'skipDangerousModePermissionPrompt': True
    } | ({
        'tui': 'fullscreen'
    } if fullscreen else {})
    fd, name = tempfile.mkstemp(prefix='sbclaude-settings.', suffix='.json')
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle)
    path = Path(name)
    path.chmod(0o644)
    return path


def _v(src: Path | str, dst: Path | str | None = None, *, ro: bool = False) -> tuple[str, str]:
    return ('-v', f'{src}:{dst if dst is not None else src}{":ro" if ro else ""}')


def _auto_memory_bytes() -> int | None:
    # Reserve the larger of 2 GiB or an eighth of RAM for the host and dockerd. Returns None on
    # tiny hosts (let Docker default apply) or if the host total cannot be determined.
    try:
        total = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (AttributeError, OSError, ValueError):
        return None
    limit = total - max(2 * 1024 ** 3, total // 8)
    return limit if limit > 512 * 1024 ** 2 else None


def _resource_args(spec: RunSpec) -> list[str]:
    args: list[str] = []
    memory = spec.memory
    if memory is None and (auto := _auto_memory_bytes()) is not None:
        memory = f'{auto}b'
    if memory and memory != '0':
        args += ['--memory', memory, '--memory-swap', memory]
    if spec.cpus:
        args += ['--cpus', spec.cpus]
    return args


def config_dir(home: Path) -> Path:
    """
    Return claude's config directory, honouring ``CLAUDE_CONFIG_DIR``.

    Parameters
    ----------
    home : Path
        The user's home directory.

    Returns
    -------
    Path
        ``$CLAUDE_CONFIG_DIR`` if set, else ``<home>/.claude``.
    """
    override = os.environ.get('CLAUDE_CONFIG_DIR')
    return Path(override).expanduser() if override else home / '.claude'


def build_run_argv(spec: RunSpec) -> tuple[list[str], Path | None]:
    """
    Build the ``docker run`` argv for an interactive claude session.

    Parameters
    ----------
    spec : RunSpec
        The resolved run description.

    Returns
    -------
    tuple[list[str], Path | None]
        The argv and an optional temp settings file the caller must delete.
    """
    home = Path.home()
    cfg_dir = config_dir(home)
    uid, gid, user = os.getuid(), os.getgid(), getpass.getuser()
    image = spec.image or IMAGE_BASE
    tty = ('-i', '-t') if (sys.stdin.isatty() and sys.stdout.isatty()) else ('-i',)
    # no-new-privileges is dropped for a --sudo box (and only then): with it set, the kernel
    # ignores sudo's setuid bit, so escalation is impossible however the box is configured.
    no_new_privileges = () if spec.use_sudo else NO_NEW_PRIVILEGES_ARGS
    hardening = ((*no_new_privileges, *HARDENING_ARGS, *_resource_args(spec)) if spec.harden else
                 ())
    argv = [
        'docker', 'run', '--rm', *tty, '--name', spec.name, *hardening, '--label', f'{LABEL}=1',
        '--label', f'{PROJECT_LABEL}={spec.project}', '--network', spec.network, '-e',
        f'HOST_UID={uid}', '-e', f'HOST_GID={gid}', '-e', f'HOST_USER={user}', '-e',
        f'HOST_HOME={home}', '-e', f'TERM={os.environ.get("TERM", "xterm-256color")}',
        *_v(claude_binary(spec.claude_binary), '/usr/local/bin/claude', ro=True), *_v(cfg_dir),
        *_v(spec.project), '-w',
        str(spec.project)
    ]
    if os.environ.get('CLAUDE_CONFIG_DIR'):
        argv += ['-e', f'CLAUDE_CONFIG_DIR={cfg_dir}']
    # The legacy top-level config/auth file (only when it still lives in $HOME).
    if (home / '.claude.json').is_file():
        argv += _v(home / '.claude.json')
    # The entrypoint runs the cc-session-recover installer only when this is set.
    if spec.recover:
        argv += ['-e', 'SBCLAUDE_RECOVER=1']
    # The entrypoint writes the sudoers drop-in and restores sudo's setuid bit only when this is
    # set, so a box started without --sudo has no sudo even though the image ships it.
    if spec.use_sudo:
        argv += ['-e', 'SBCLAUDE_SUDO=1']
    for key, value in spec.env.items():
        argv += ['-e', f'{key}={value}']
    cleanup: Path | None = None
    settings = cfg_dir / 'settings.json'
    if settings.is_file():
        # Mounted read-write (not :ro) so claude can save settings in-session; writes land in this
        # throwaway temp file, leaving the host settings.json untouched.
        cleanup = _patched_settings(settings, fullscreen=spec.fullscreen)
        argv += _v(cleanup, cfg_dir / 'settings.json')
    argv += _gitconfig_args(home)
    argv += _host_venv_args(spec.project)
    for path in spec.ro:
        if path != spec.project:
            argv += _v(path, ro=True)
    for path in spec.rw:
        if path != spec.project:
            argv += _v(path)
    if spec.use_re or spec.use_android:
        for tool in PENTOO_TOOL_DIRS:
            if tool.is_dir():
                argv += _v(tool, ro=True)
    if spec.use_ghidra and GHIDRA_DIR.is_dir():
        argv += _v(GHIDRA_DIR, ro=True)
    # Optional feature mounts, as (enabled, build-args) pairs. A table rather than a run of `if`
    # statements: each entry is independent, so adding one should not grow the function's
    # branching. Order is preserved and matters only for readability of the final argv.
    optional_args = (
        (spec.use_gpu, lambda: _gpu_args(use_x11=spec.use_x11)),
        (spec.use_android, lambda: _android_args(home)),
        (spec.use_usb and USB_DEVICE_DIR.is_dir(), lambda: _v(USB_DEVICE_DIR)),
        (spec.use_ios, _ios_args),
        (spec.use_ssh, lambda: _ssh_args(home)),
        (spec.use_gpg, lambda: _gpg_args(home, uid)),
        (spec.use_wayland, lambda: _wayland_args(uid)),
        (spec.use_x11, lambda: _x11_args(uid, user)),
    )
    for enabled, build_args in optional_args:
        if enabled:
            argv += build_args()
    argv += spec.extra_args
    argv.append(image)
    argv += list(spec.claude_args)
    return argv, cleanup


def gpu_ready() -> bool:
    """
    Wake an NVIDIA GPU and report whether it can be handed to a container.

    ``nvidia-smi`` is the probe and the wake-up in one: opening the control device resumes a card
    the kernel has parked at runtime, and loads the driver on a host where it was never inserted.
    A card mid-resume can fail the first call and answer the next, so it is asked more than once.

    Where ``nvidia-smi`` is not installed, there is nothing to ask, and the presence of the driver's
    device nodes stands in for it, which is as much as can be known without the tool.

    Returns
    -------
    bool
        ``True`` when a GPU is listed, or when its device nodes exist and there is no probe.
    """
    if not which(GPU_PROBE[0]):
        return any(dev.exists() for dev in NVIDIA_DEVICES)
    for attempt in range(1, _GPU_PROBE_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(_GPU_PROBE_DELAY_SECONDS)
        try:
            listed = sp.run(GPU_PROBE,
                            capture_output=True,
                            check=True,
                            text=True,
                            timeout=_GPU_PROBE_TIMEOUT_SECONDS).stdout
        except (OSError, sp.SubprocessError) as e:
            log.debug('GPU probe %d of %d failed: %s', attempt, _GPU_PROBE_ATTEMPTS, e)
            continue
        # Every listed device carries a UUID; an empty list is the "No devices were found" reply.
        if 'UUID' in listed:
            log.debug('GPU probe %d of %d found a device.', attempt, _GPU_PROBE_ATTEMPTS)
            return True
        log.debug('GPU probe %d of %d listed no device.', attempt, _GPU_PROBE_ATTEMPTS)
    return False


def _gpu_args(*, use_x11: bool = False) -> list[str]:
    # `graphics` is what makes the NVIDIA Container Toolkit inject the GL/EGL/Vulkan userspace
    # (libGL, libEGL, libnvidia-glcore, the Vulkan ICD). Without it only CUDA is available and
    # anything that renders -- headless Chrome/WebGL, Qt, ffmpeg filters -- silently falls back
    # to software. `display` adds the GLX bits, needed only when driving a real X server.
    caps = 'compute,utility,graphics'
    if use_x11:
        caps += ',display'
    args: list[str] = []
    # Only ask for the NVIDIA GPUs once one has answered. The daemon resolves the request against
    # the CDI registry, and `nvidia.com/gpu=all` with no usable card (parked at D3cold, an eGPU
    # that is not attached, a module never inserted) is unresolvable: the daemon logs a warning,
    # starts the container anyway, and it dies within the second. Dropping the request leaves a
    # working box on the DRM nodes below instead of one that cannot start.
    if gpu_ready():
        args += ['--gpus', 'all', '-e', f'NVIDIA_DRIVER_CAPABILITIES={caps}']
        args += [
            arg for dev in NVIDIA_DEVICES if dev.exists()
            for arg in ('--group-add', str(dev.stat().st_gid))
        ]
    else:
        message = (f'--gpu: no NVIDIA GPU answered after {_GPU_PROBE_ATTEMPTS} attempts, so the '
                   'box starts without --gpus all. Asking Docker for one anyway starts a '
                   'container that dies immediately.')
        sys.stderr.write(f'sbclaude: {message}\n')
        _to_syslog(message)
    # DRM render nodes. Needed by Mesa on AMD/Intel, and used by Vulkan/VA-API even on NVIDIA.
    # Passed as devices (not a bind mount) so the device cgroup actually allows access.
    for node in sorted(DRI_DEVICE_DIR.glob('renderD*')) if DRI_DEVICE_DIR.is_dir() else []:
        args += ['--device', str(node), '--group-add', str(node.stat().st_gid)]
    return args


def _android_args(home: Path) -> Iterator[str]:
    sdk = Path(os.environ.get('ANDROID_HOME', str(ANDROID_SDK_DIR)))
    if sdk.is_dir():
        yield from _v(sdk, ro=True)
    if ANDROID_SDK_UPDATE_MANAGER_DIR.is_dir():
        yield from _v(ANDROID_SDK_UPDATE_MANAGER_DIR, ro=True)
    if (d := home / '.android').is_dir():
        yield from _v(d)
    if KVM_DEVICE.exists():
        yield from ('--device', str(KVM_DEVICE), '--group-add', str(KVM_DEVICE.stat().st_gid))


def _ios_args() -> list[str]:
    # frida's usbmux ("fruity") backend reaches an iOS device through the host usbmuxd
    # socket rather than claiming the USB device directly (the host usbmuxd already owns
    # it), so bind-mount that socket read-write. The lockdown directory carries the
    # host's existing pairing records so a trusted session needs no re-pairing.
    args: list[str] = []
    if USBMUXD_SOCKET.is_socket():
        args += _v(USBMUXD_SOCKET)
    else:
        sys.stderr.write(
            f'sbclaude: --ios: no usbmuxd socket at {USBMUXD_SOCKET}; is usbmuxd running on '
            'the host?\n')
    if LOCKDOWN_DIR.is_dir():
        args += _v(LOCKDOWN_DIR, ro=True)
    return args


def _walk(root: Path) -> Iterator[Path]:
    # Recurse into real subdirectories only; a symlinked directory is yielded but not
    # descended into, so a link loop cannot hang the walk.
    for entry in sorted(root.iterdir()):
        yield entry
        if entry.is_dir() and not entry.is_symlink():
            yield from _walk(entry)


def _ssh_link_args(ssh: Path) -> Iterator[str]:
    # A dotfiles setup commonly symlinks ~/.ssh/config (or a key) to a path outside ~/.ssh.
    # A bind mount carries the link itself, not its target, so the link dangles in the box
    # and every Host alias, IdentityFile, and User line is silently lost. Mount each target
    # read-only at its real path so the link resolves the same way it does on the host.
    seen: set[Path] = set()
    for link in _walk(ssh):
        if not link.is_symlink():
            continue
        target = link.resolve()
        if target not in seen and target.exists() and not target.is_relative_to(ssh):
            seen.add(target)
            yield from _v(target, ro=True)


def _ssh_agent_args() -> list[str]:
    # Bridge the host's running ssh-agent into the box. The mounted keys are useless on
    # their own when they are passphrase-protected or held in a hardware token: only the
    # live agent can sign the authentication challenge, and nothing inside a no-prompt box
    # can answer a passphrase prompt. Mounted read-write because connecting to a socket
    # needs write permission on it.
    if not (sock := os.environ.get('SSH_AUTH_SOCK', '')):
        return []
    if not Path(sock).is_socket():
        sys.stderr.write(
            f'sbclaude: --ssh: SSH_AUTH_SOCK={sock} is not a socket; agent not forwarded.\n')
        return []
    return [*_v(sock), '-e', f'SSH_AUTH_SOCK={sock}']


def _ssh_args(home: Path) -> list[str]:
    # Mount the host ~/.ssh read-only so SSH git remotes authenticate with the host's
    # keys and known_hosts. Read-only protects the keys from the autonomous agent (at
    # the cost of not persisting newly-learnt host keys).
    ssh = home / '.ssh'
    args = [*_v(ssh, ro=True), *_ssh_link_args(ssh)] if ssh.is_dir() else []
    return args + _ssh_agent_args()


def _gpg_agent_socket() -> Path | None:
    try:
        out = sp.run(
            ['gpgconf', '--list-dirs', 'agent-socket'],  # noqa: S607
            check=True,
            capture_output=True,
            text=True).stdout.strip()
    except (FileNotFoundError, sp.CalledProcessError):
        return None
    return Path(out) if out else None


def _gpg_args(home: Path, uid: int) -> list[str]:
    # Mount the host GnuPG home (read-write: gpg needs to write lock files and the
    # trustdb) so the container's gpg sees the same keyrings, then bridge to the host's
    # running gpg-agent for the actual signing.
    #
    # The agent socket usually lives outside the home, and where the box's gpg looks for it
    # depends on whether /run/user/<uid> is there and usable: with it, the socket directory
    # is /run/user/<uid>/gnupg, and without it gpg falls back to <GNUPGHOME>/S.gpg-agent.
    # Which of the two applies is not ours to predict -- --wayland and --ssh both forward a
    # socket into /run/user/<uid>, which makes Docker create it and the entrypoint chown it
    # to the user -- so overlay the live host socket at both paths, unconditionally. The
    # unused one costs a bind mount and nothing else, whereas guessing wrong costs the
    # ability to sign: gpg does not report a missing agent, it silently starts its own inside
    # the box, and that one can only reach pinentry-curses, which needs a TTY the box does
    # not have.
    homedir = Path(os.environ.get('GNUPGHOME') or home / '.gnupg')
    if not homedir.is_dir():
        return []
    args = list(_v(homedir))
    if os.environ.get('GNUPGHOME'):
        args += ['-e', f'GNUPGHOME={homedir}']
    if (socket := _gpg_agent_socket()) and socket.is_socket():
        if not socket.is_relative_to(homedir):
            args += _v(socket, homedir / 'S.gpg-agent')
        # Mount at the runtime path even when the host socket already lives there. The paths
        # match but the files do not: /run/user/<uid> inside the box is its own tmpfs, so
        # skipping this leaves nothing at the very path the box's gpg prefers, which is the
        # usual host layout rather than an unusual one.
        args += _v(socket, Path(f'/run/user/{uid}/gnupg/S.gpg-agent'))
    return args


def _global_gitconfig_files(home: Path) -> Iterator[Path]:
    # Enumerate the global git config files: the main ~/.gitconfig plus every file it
    # pulls in via include.path / includeIf. --includes is off by default when a specific
    # scope (--global) is given, so it must be requested explicitly. HOME is overridden so
    # the include paths resolve the same way they will inside the box.
    env = {**os.environ, 'HOME': str(home), 'GIT_CONFIG_NOSYSTEM': '1'}
    try:
        out = sp.run(
            ['git', 'config', '--global', '--list', '--show-origin', '--includes'],  # noqa: S607
            capture_output=True,
            check=True,
            env=env,
            text=True).stdout
    except (FileNotFoundError, sp.CalledProcessError):
        return
    seen: set[Path] = set()
    for line in out.splitlines():
        origin = line.split('\t', 1)[0]
        if origin.startswith('file:'):
            path = Path(origin.removeprefix('file:')).expanduser()
            if path not in seen and path.is_file():
                seen.add(path)
                yield path


def _gitconfig_args(home: Path) -> list[str]:
    # Mount the user's global git config and every file it includes read-only, so git and
    # pre-commit inside the box see the full configuration. Fall back to the plain
    # ~/.gitconfig when git cannot enumerate it (e.g. git missing on the host).
    args: list[str] = []
    for path in _global_gitconfig_files(home):
        args += _v(path, ro=True)
    if not args and (gitconfig := home / '.gitconfig').is_file():
        args += _v(gitconfig, ro=True)
    return args


def _wayland_args(uid: int) -> list[str]:
    """
    Forward the Wayland socket.

    Preferred over X11 for GUIs: a Wayland client cannot read other windows or inject input into
    them, whereas handing over an X11 cookie grants exactly that over the whole session. It is
    also a single socket rather than socket + cookie, and NVIDIA drives it through EGL, so the
    ``display`` (GLX) driver capability is not needed.

    Parameters
    ----------
    uid : int
        Host user ID, used to place the socket under the container's ``/run/user/<uid>``.

    Returns
    -------
    list[str]
        ``docker run`` arguments, or an empty list when no compositor socket exists.
    """
    display = Path(os.environ.get('WAYLAND_DISPLAY', 'wayland-0'))
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{uid}')
    # WAYLAND_DISPLAY may itself be an absolute path.
    sock = display if display.is_absolute() else runtime / display
    if not sock.exists():
        sys.stderr.write(f'sbclaude: --wayland: no compositor socket at {sock}; skipping\n')
        return []
    # Re-home the socket under the container's own XDG_RUNTIME_DIR. Mounted read-write: a Wayland
    # client must write to the socket.
    target = f'/run/user/{uid}/{sock.name}'
    return [
        *_v(sock, target), '-e', f'WAYLAND_DISPLAY={sock.name}', '-e',
        f'XDG_RUNTIME_DIR=/run/user/{uid}'
    ]


def _x11_args(uid: int, user: str) -> list[str]:
    home = Path.home()
    xauth = os.environ.get('XAUTHORITY', '')
    if not xauth:
        cookies = sorted(Path(f'/run/user/{uid}').glob('xauth*'))
        xauth = str(cookies[0]) if cookies else ''
    if not xauth and (home / '.Xauthority').is_file():
        xauth = str(home / '.Xauthority')
    args = ['-e', f'DISPLAY={os.environ.get("DISPLAY", ":0")}',
            *_v('/tmp/.X11-unix', ro=True)]  # noqa: S108
    if xauth and Path(xauth).is_file():
        args += [*_v(xauth, ro=True), '-e', f'XAUTHORITY={xauth}']
    else:
        sys.stderr.write(
            f"sbclaude: --x11: no xauth cookie; if GUI fails run 'xhost +SI:localuser:{user}'\n")
    return args


def _run_relaying_stderr(argv: Sequence[str]) -> tuple[int, str]:
    # docker's own diagnosis -- the daemon's refusal, the unresolvable device, and the mount that
    # does not exist -- is on stderr, and that is the one thing worth keeping. It is passed straight
    # through as it arrives, so the terminal behaves as before, while a bounded tail is kept back
    # for the log.
    #
    # stdin and stdout stay attached to the terminal untouched: `docker run -t` and the TUI both
    # need a real terminal on them, and with a TTY allocated the container's own output comes back
    # on stdout anyway, so nothing of the session is intercepted here.
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    tail = ''
    with sp.Popen(argv, stderr=sp.PIPE, bufsize=0) as proc:
        # stderr=PIPE always gives a stream; the check is for the type checker alone.
        while proc.stderr and (chunk := proc.stderr.read(_STDERR_TAIL_CHARS)):
            text = decoder.decode(chunk)
            sys.stderr.write(text)
            sys.stderr.flush()
            tail = (tail + text)[-_STDERR_TAIL_CHARS:]
    return proc.returncode, tail.strip()


def _to_syslog(message: str) -> None:
    # The terminal cannot be relied on to carry a start failure: the fullscreen TUI erases its own
    # screen on the way out, and a box that dies before the prompt returns can leave nothing behind
    # at all. The system log survives both, so the same message goes there and can be read
    # afterwards with `journalctl -t sbclaude`.
    # One entry per line: a single long entry is what journalctl truncates at the terminal width,
    # and the reason has to survive that.
    try:
        syslog.openlog('sbclaude', syslog.LOG_PID, syslog.LOG_USER)
        for line in message.splitlines():
            syslog.syslog(syslog.LOG_ERR, line)
        syslog.closelog()
    except OSError as e:
        sys.stderr.write(f'sbclaude: could not write to syslog: {e}\n')


def _failed_to_start(code: int, seconds: float) -> bool:
    # Two shapes of "it never got going". The docker CLI reserves 125-127 for a container that
    # never ran, and a box that exits within seconds died before a session could begin (an
    # unresolvable GPU, a mount that is not there, an entrypoint that aborted). Signal deaths
    # (128+) are neither: a session ended with Ctrl-C worked exactly as asked.
    if not code:
        return False
    return code in _START_FAILURE_REASONS or (code < _SIGNAL_EXIT_BASE
                                              and seconds < _QUICK_EXIT_SECONDS)


def _report_failure(code: int, seconds: float, argv: Sequence[str], stderr_tail: str, *,
                    attempt: int, fullscreen: bool) -> None:
    # docker's own message goes to the terminal but the command behind it does not, and that is
    # usually where the answer is; anything claude printed while dying is already gone with the
    # alternate screen. Both are put in the log, where they outlive the terminal.
    reason = _START_FAILURE_REASONS.get(code, f'it exited with {code} after {seconds:.1f}s')
    detail = f'{stderr_tail}\n' if stderr_tail else ''
    last = attempt >= _LAUNCH_ATTEMPTS
    _to_syslog(f'Attempt {attempt} of {_LAUNCH_ATTEMPTS}: the box did not start ({reason}, exit '
               f'{code}).\n{detail}Command: {shlex.join(argv)}')
    tail = (' Giving up.' if last else ' Retrying.')
    hint = (' Re-run with --no-fullscreen to see what it printed, erased here by the fullscreen '
            'TUI.' if last and fullscreen and code not in _START_FAILURE_REASONS else '')
    sys.stderr.write(f'sbclaude: the box did not start ({reason}, exit {code}), attempt {attempt} '
                     f'of {_LAUNCH_ATTEMPTS}.{tail}{hint} Details in the system log: '
                     'journalctl -t sbclaude\n')


def run(spec: RunSpec) -> int:
    """
    Launch an interactive claude session, retrying a box that never starts.

    A box that dies before a session begins is launched again, three attempts in all. The command
    is rebuilt for each attempt rather than reused, so a GPU that the previous attempt's probe woke
    is picked up by the next one. A session that ran and then ended, whatever its exit code, is
    never retried.

    Parameters
    ----------
    spec : RunSpec
        The resolved run description.

    Returns
    -------
    int
        The container's exit code, from the last attempt made.
    """
    image = spec.image or IMAGE_BASE
    if image == IMAGE_BASE:
        ensure_image(
            image,
            log=lambda line: print(line, file=sys.stderr),  # noqa: T201
            debian_mirror=spec.debian_mirror)
    code = 0
    for attempt in range(1, _LAUNCH_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(_LAUNCH_RETRY_SECONDS)
        argv, cleanup = build_run_argv(spec)
        log.debug('Running: %s', shlex.join(argv))
        started = time.monotonic()
        try:
            code, stderr_tail = _run_relaying_stderr(argv)
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)
        if not _failed_to_start(code, seconds := time.monotonic() - started):
            return code
        _report_failure(code,
                        seconds,
                        argv,
                        stderr_tail,
                        attempt=attempt,
                        fullscreen=spec.fullscreen)
    return code


def _box_env(name: str) -> dict[str, str]:
    # The environment the box was started with, which is where the mirrored identity
    # (HOST_UID/HOST_GID/HOST_USER/HOST_HOME) is recorded.
    try:
        env = (_client().containers.get(name).attrs.get('Config') or {}).get('Env') or []
    except docker.errors.DockerException:
        return {}
    return {key: value for key, sep, value in (item.partition('=') for item in env) if sep}


def _shell_identity_args(name: str) -> list[str]:
    # Enter as the identity the box mirrors rather than as root.
    #
    # Read back from the container rather than assumed from this process: the box records what it
    # was started with, which is what its passwd entry was built from. The host's own values are
    # only a fallback for a box that predates this or was started some other way.
    #
    # No -w: the working directory is left as the container's own, which is the project. USER and
    # LOGNAME are passed explicitly because `docker exec -u` sets neither (it fills in HOME alone,
    # from the passwd entry), leaving tools that identify the user by environment -- git's
    # fallback author, gh, the shell prompt -- looking at an empty string.
    env = _box_env(name)
    uid = env.get('HOST_UID') or str(os.getuid())
    user = env.get('HOST_USER') or getpass.getuser()
    home = env.get('HOST_HOME') or str(Path.home())
    return ['-u', uid, '-e', f'HOME={home}', '-e', f'USER={user}', '-e', f'LOGNAME={user}']


def shell(name: str, *, root: bool = False) -> int:
    """
    Open a debug shell inside a running box.

    The shell runs as the user the box mirrors, in the container's working directory (the
    project). With ``root``, it runs as root instead; that path never involves privilege
    escalation inside the box, as the Docker daemon starts the process as root directly.

    Parameters
    ----------
    name : str
        The container name.
    root : bool
        Open the shell as root instead of the mapped user.

    Returns
    -------
    int
        The shell's exit code.
    """
    identity = [] if root else _shell_identity_args(name)
    # A login shell so that /etc/profile.d/sbclaude.sh runs and the toolchain (the JDK, the
    # Android SDK, /opt/venv) is on PATH exactly as it is for the session's own shells.
    return sp.run(
        ['docker', 'exec', '-it', *identity, name, 'bash', '-l'],  # noqa: S607
        check=False).returncode


def _client() -> docker.DockerClient:
    return docker.from_env()


def _dir_hash(ctx: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in ctx.rglob('*') if p.is_file()):
        digest.update(path.relative_to(ctx).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _context_hash() -> str:
    with resources.as_file(resources.files('sbclaude') / 'docker') as ctx:
        return _dir_hash(Path(ctx))


def image_exists(tag: str) -> bool:
    """
    Return whether a local image with ``tag`` exists.

    Parameters
    ----------
    tag : str
        The image tag.

    Returns
    -------
    bool
        ``True`` if the image is present locally.
    """
    try:
        _client().images.get(tag)
    except docker.errors.ImageNotFound:
        return False
    return True


def image_up_to_date(tag: str) -> bool:
    """
    Return whether the local image matches the current build context.

    Parameters
    ----------
    tag : str
        The image tag.

    Returns
    -------
    bool
        ``True`` if the image exists and its context-hash label matches the
        packaged Dockerfiles/entrypoint (so no rebuild is needed).
    """
    try:
        image = _client().images.get(tag)
    except docker.errors.ImageNotFound:
        return False
    return (image.labels or {}).get(HASH_LABEL) == _context_hash()


def delete_images() -> list[str]:
    """
    Delete the sbclaude image.

    Returns
    -------
    list[str]
        Tags that were removed (a missing image is ignored).
    """
    client = _client()
    removed: list[str] = []
    for tag in (IMAGE_BASE,):
        try:
            client.images.remove(tag, force=True)
        except docker.errors.ImageNotFound:
            continue
        removed.append(tag)
    return removed


def ensure_image(image: str,
                 *,
                 log: Callable[[str], None] = lambda _line: None,
                 debian_mirror: str | None = None) -> None:
    """
    Build ``image`` if it is missing or its build context changed.

    Parameters
    ----------
    image : str
        The image tag to ensure. Only the sbclaude image is auto-built.
    log : Callable[[str], None]
        Sink for build log lines.
    debian_mirror : str | None
        Debian archive mirror to bake into the image when a build is triggered.

    Raises
    ------
    docker.errors.BuildError
        If the build fails and no usable image is already tagged.
    """
    if image != IMAGE_BASE or image_up_to_date(image):
        return
    existing = image_exists(image)
    log(f'Image {image} {"rebuilding (Dockerfile changed)" if existing else "building"}...')
    try:
        for line in build_images(debian_mirror=debian_mirror):
            log(line)
    except docker.errors.BuildError as e:
        # A failed first build leaves nothing to run, so it is fatal. A failed rebuild is not:
        # the previous image is still there and still works, and refusing to start would hand a
        # transient upstream outage the power to stop every box. Say so loudly instead -- the
        # session then knowingly runs an image that predates the change that triggered the
        # rebuild.
        if not existing:
            raise
        log(f'sbclaude: image build FAILED: {e}')
        log(f'sbclaude: continuing with the existing, now stale {image}. Re-run `sbclaude build` '
            'once the cause is fixed.')


def list_managed() -> Iterator[dict[str, str]]:
    """
    Return info for every running sbclaude-managed container.

    Yields
    ------
    dict[str, str]
        One mapping per container with ``name``, ``image``, ``status``, ``project``.
    """
    for ctr in _client().containers.list(filters={'label': f'{LABEL}=1'}):
        labels = ctr.labels or {}
        tags = ctr.image.tags if ctr.image else []
        yield {
            'name': ctr.name or '',
            'image': (tags or ['<none>'])[0],
            'status': ctr.status,
            'project': labels.get(PROJECT_LABEL, '')
        }


def project_containers(project: Path) -> list[str]:
    """
    Return the names of running managed boxes for a project.

    Parameters
    ----------
    project : Path
        The project directory the boxes were started against.

    Returns
    -------
    list[str]
        Names of the running boxes whose project label matches.
    """
    return [
        ctr.name or '' for ctr in _client().containers.list(
            filters={'label': [f'{LABEL}=1', f'{PROJECT_LABEL}={project}']})
    ]


def stop(name: str | None = None, project: Path | None = None) -> Iterator[str]:
    """
    Stop boxes by name, by project, or every managed box.

    When ``name`` is given only that box is stopped. Otherwise, when
    ``project`` is given every managed box for that project is stopped (this
    is what removes several boxes sharing one project directory). When both
    are ``None`` every managed box is stopped.

    Parameters
    ----------
    name : str | None
        The single container to stop.
    project : Path | None
        Stop every managed box started against this project directory.

    Yields
    ------
    str
        Names of the containers that were removed.
    """
    client = _client()
    if name is not None:
        targets = [c for c in client.containers.list(all=True) if c.name == name]
    elif project is not None:
        targets = client.containers.list(
            all=True, filters={'label': [f'{LABEL}=1', f'{PROJECT_LABEL}={project}']})
    else:
        targets = client.containers.list(all=True, filters={'label': f'{LABEL}=1'})
    for ctr in targets:
        ctr.remove(force=True)
        yield ctr.name or ''


def build_images(*, no_cache: bool = False, debian_mirror: str | None = None) -> Iterator[str]:
    """
    Build the sbclaude image, yielding log lines.

    Parameters
    ----------
    no_cache : bool
        Disable the build cache.
    debian_mirror : str | None
        Debian archive mirror to bake into the image's apt sources.

    Yields
    ------
    str
        Build log lines.

    Raises
    ------
    docker.errors.BuildError
        If the daemon reports a failing build step, after that step's message is yielded.
    """
    client = _client()
    mirror = debian_mirror.rstrip('/') if debian_mirror else None
    with resources.as_file(resources.files('sbclaude') / 'docker') as ctx:
        labels = {HASH_LABEL: _dir_hash(Path(ctx))}
        yield f'==> Building {IMAGE_BASE} (Dockerfile)'
        buildargs = {'DEBIAN_MIRROR': mirror} if mirror else None
        for chunk in client.api.build(path=str(ctx),
                                      dockerfile='Dockerfile',
                                      tag=IMAGE_BASE,
                                      nocache=no_cache,
                                      labels=labels,
                                      buildargs=buildargs,
                                      decode=True,
                                      rm=True):
            if (error := chunk.get('error')) and error.strip():
                # The daemon reports a failed step in-band, as one more chunk of the log. Without
                # raising here the generator would end normally and the caller would carry on as
                # though the image had been built, leaving whatever stale image happened to
                # already carry the tag in place.
                yield error.rstrip('\n')
                # The log has already been streamed to the caller line by line, so the exception
                # carries an empty one rather than repeating it.
                raise docker.errors.BuildError(error.strip(), iter(()))
            if (line := chunk.get('stream', '')).strip():
                yield line.rstrip('\n')
