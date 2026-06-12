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
import getpass
import hashlib
import json
import os
import re
import secrets
import subprocess as sp
import sys
import tempfile

import docker
import docker.errors

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

__all__ = ('IMAGE_BASE', 'IMAGE_RE', 'LABEL', 'RunSpec', 'build_images', 'config_dir',
           'default_name', 'delete_images', 'ensure_image', 'image_exists', 'image_up_to_date',
           'list_managed', 'project_containers', 'run', 'shell', 'stop', 'unique_name')

LABEL = 'sbclaude.managed'
"""Docker label marking a container as sbclaude-managed."""
PROJECT_LABEL = 'sbclaude.project'
"""Docker label recording a container's project path."""
HASH_LABEL = 'sbclaude.context_hash'
"""Docker label storing the build context hash for staleness checks."""
IMAGE_BASE = 'sbclaude:latest'
"""Tag of the everyday coding image."""
IMAGE_RE = 'sbclaude-re:latest'
"""Tag of the reverse-engineering image."""
HARDENING_ARGS = ('--security-opt', 'no-new-privileges', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
                  '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'FOWNER', '--cap-add', 'SETUID',
                  '--cap-add', 'SETGID', '--pids-limit', '4096')
"""Hardening arguments to Docker."""
USBMUXD_SOCKET = Path('/var/run/usbmuxd')
"""Host usbmuxd socket that frida's usbmux backend uses to reach an iOS device."""
LOCKDOWN_DIR = Path('/var/lib/lockdown')
"""Host lockdownd pairing-records directory, mounted read-only so iOS pairing carries in."""


@dataclass
class RunSpec:
    """Fully-resolved description of a ``run`` invocation."""

    project: Path
    """Project path (read-write, becomes the working directory)."""
    name: str
    """Container name."""
    network: str = 'host'
    """Docker network mode."""
    image: str | None = None
    """Image override, or ``None`` to auto-select."""
    use_re: bool = False
    """Whether to use the reverse-engineering image and tools."""
    use_ghidra: bool = False
    """Whether to mount the host Ghidra installation read-only."""
    use_android: bool = False
    """Whether to mount the Android SDK and related devices."""
    use_usb: bool = False
    """Whether to expose ``/dev/bus/usb`` for adb over USB."""
    use_ios: bool = False
    """Whether to mount the host usbmuxd socket so frida can reach an iOS device."""
    use_x11: bool = False
    """Whether to forward X11 ``DISPLAY`` and ``XAUTHORITY``."""
    use_ssh: bool = False
    """Whether to mount the host ``~/.ssh`` directory read-only for SSH git remotes."""
    use_gpg: bool = False
    """Whether to mount the host GnuPG home and agent socket for commit signing."""
    ro: list[Path] = field(default_factory=list)
    """Read-only mount paths."""
    rw: list[Path] = field(default_factory=list)
    """Read-write mount paths."""
    env: dict[str, str] = field(default_factory=dict)
    """Environment variables injected into the box."""
    harden: bool = True
    """Whether to apply the container hardening flags."""
    debian_mirror: str | None = None
    """Debian archive mirror for an auto-triggered image build, or ``None`` for the default."""
    extra_args: list[str] = field(default_factory=list)
    """Extra arguments passed to ``docker run``."""
    claude_args: tuple[str, ...] = ()
    """Arguments forwarded to ``claude``."""


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
    return f'sbclaude-{re.sub(r"[^a-zA-Z0-9_.-]", "-", project.name)}'


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


def claude_binary() -> Path:
    """
    Locate the real host ``claude`` binary, resolving any version symlink.

    Returns
    -------
    Path
        The resolved binary path.

    Raises
    ------
    FileNotFoundError
        If ``claude`` is not on ``PATH``.
    """
    if not (found := which('claude')):
        msg = "'claude' not found on PATH"
        raise FileNotFoundError(msg)
    return Path(found).resolve()


def _patched_settings(settings: Path) -> Path:
    """
    Write a copy of ``settings.json`` with the bash sandbox disabled.

    Parameters
    ----------
    settings : Path
        The host ``~/.claude/settings.json``.

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
    }
    fd, name = tempfile.mkstemp(prefix='sbclaude-settings.', suffix='.json')
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle)
    path = Path(name)
    path.chmod(0o644)
    return path


def _v(src: Path | str, dst: Path | str | None = None, *, ro: bool = False) -> tuple[str, str]:
    return ('-v', f'{src}:{dst if dst is not None else src}{":ro" if ro else ""}')


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
    image = spec.image or (IMAGE_RE if spec.use_re else IMAGE_BASE)
    tty = ('-i', '-t') if (sys.stdin.isatty() and sys.stdout.isatty()) else ('-i',)
    hardening = tuple(HARDENING_ARGS) if spec.harden else ()
    argv = [
        'docker', 'run', '--rm', *tty, '--name', spec.name, *hardening, '--label', f'{LABEL}=1',
        '--label', f'{PROJECT_LABEL}={spec.project}', '--network', spec.network, '-e',
        f'HOST_UID={uid}', '-e', f'HOST_GID={gid}', '-e', f'HOST_USER={user}', '-e',
        f'HOST_HOME={home}', '-e', f'TERM={os.environ.get("TERM", "xterm-256color")}',
        *_v(claude_binary(), '/usr/local/bin/claude', ro=True), *_v(cfg_dir), *_v(spec.project),
        '-w',
        str(spec.project)
    ]
    # Mirror CLAUDE_CONFIG_DIR into the box so claude reads the relocated config.
    if os.environ.get('CLAUDE_CONFIG_DIR'):
        argv += ['-e', f'CLAUDE_CONFIG_DIR={cfg_dir}']
    # The legacy top-level config/auth file (only when it still lives in $HOME).
    if (home / '.claude.json').is_file():
        argv += _v(home / '.claude.json')
    for key, value in spec.env.items():
        argv += ['-e', f'{key}={value}']
    cleanup: Path | None = None
    settings = cfg_dir / 'settings.json'
    if settings.is_file():
        # Mounted read-write (not :ro) so claude can save settings in-session; writes land in this
        # throwaway temp file, leaving the host settings.json untouched.
        cleanup = _patched_settings(settings)
        argv += _v(cleanup, cfg_dir / 'settings.json')
    gitconfig = home / '.gitconfig'
    if gitconfig.is_file():
        argv += _v(gitconfig, ro=True)
    for path in spec.ro:
        if path != spec.project:
            argv += _v(path, ro=True)
    for path in spec.rw:
        if path != spec.project:
            argv += _v(path)
    if spec.use_re or spec.use_android:
        # These are ::pentoo paths.
        for tool in (Path('/opt/jadx-bin'), Path('/opt/apktool')):
            if tool.is_dir():
                argv += _v(tool, ro=True)
    if spec.use_ghidra and Path('/usr/share/ghidra').is_dir():
        argv += _v('/usr/share/ghidra', ro=True)
    if spec.use_android:
        argv += _android_args(home)
    if spec.use_usb and Path('/dev/bus/usb').is_dir():
        argv += _v('/dev/bus/usb')
    if spec.use_ios:
        argv += _ios_args()
    if spec.use_ssh:
        argv += _ssh_args(home)
    if spec.use_gpg:
        argv += _gpg_args(home)
    if spec.use_x11:
        argv += _x11_args(uid, user)
    argv += spec.extra_args
    argv.append(image)
    argv += list(spec.claude_args)
    return argv, cleanup


def _android_args(home: Path) -> Iterator[str]:
    sdk = Path(os.environ.get('ANDROID_HOME', '/opt/android-sdk'))
    if sdk.is_dir():
        yield from _v(sdk, ro=True)
    if Path('/opt/android-sdk-update-manager').is_dir():
        yield from _v('/opt/android-sdk-update-manager', ro=True)
    if (d := home / '.android').is_dir():
        yield from _v(d)
    if (kvm := Path('/dev/kvm')).exists():
        yield from ('--device', '/dev/kvm', '--group-add', str(kvm.stat().st_gid))


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


def _ssh_args(home: Path) -> list[str]:
    # Mount the host ~/.ssh read-only so SSH git remotes authenticate with the host's
    # keys and known_hosts. Read-only protects the keys from the autonomous agent (at
    # the cost of not persisting newly-learnt host keys).
    ssh = home / '.ssh'
    return list(_v(ssh, ro=True)) if ssh.is_dir() else []


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


def _gpg_args(home: Path) -> list[str]:
    # Mount the host GnuPG home (read-write: gpg needs to write lock files and the
    # trustdb) so the container's gpg sees the same keyrings, then bridge to the host's
    # running gpg-agent for the actual signing. The agent socket often lives outside the
    # home (e.g. /run/user/<uid>/gnupg); the container's gpg, lacking a usable
    # /run/user/<uid>, looks for it at <GNUPGHOME>/S.gpg-agent, so overlay the live host
    # socket there.
    homedir = Path(os.environ.get('GNUPGHOME') or home / '.gnupg')
    if not homedir.is_dir():
        return []
    args = list(_v(homedir))
    if os.environ.get('GNUPGHOME'):
        args += ['-e', f'GNUPGHOME={homedir}']
    if (socket := _gpg_agent_socket()) and socket.is_socket() \
            and not socket.is_relative_to(homedir):
        args += _v(socket, homedir / 'S.gpg-agent')
    return args


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


def run(spec: RunSpec) -> int:
    """
    Launch an interactive claude session.

    Parameters
    ----------
    spec : RunSpec
        The resolved run description.

    Returns
    -------
    int
        The container's exit code.
    """
    image = spec.image or (IMAGE_RE if spec.use_re else IMAGE_BASE)
    if image in {IMAGE_BASE, IMAGE_RE}:
        ensure_image(
            image,
            log=lambda line: print(line, file=sys.stderr),  # noqa: T201
            debian_mirror=spec.debian_mirror)
    argv, cleanup = build_run_argv(spec)
    try:
        return sp.run(argv, check=False).returncode
    finally:
        if cleanup is not None:
            cleanup.unlink(missing_ok=True)


def shell(name: str) -> int:
    """
    Open a root debug shell inside a running box.

    Parameters
    ----------
    name : str
        The container name.

    Returns
    -------
    int
        The shell's exit code.
    """
    return sp.run(['docker', 'exec', '-it', name, 'bash'], check=False).returncode  # noqa: S607


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
    Delete the sbclaude images (RE first, then base).

    Returns
    -------
    list[str]
        Tags that were removed (missing images are ignored).
    """
    client = _client()
    removed: list[str] = []
    for tag in (IMAGE_RE, IMAGE_BASE):
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
        The image tag to ensure. Only the sbclaude images are auto-built.
    log : Callable[[str], None]
        Sink for build log lines.
    debian_mirror : str | None
        Debian archive mirror to bake into the base image when a build is triggered.
    """
    if image not in {IMAGE_BASE, IMAGE_RE} or image_up_to_date(image):
        return
    verb = 'rebuilding (Dockerfile changed)' if image_exists(image) else 'building'
    log(f'Image {image} {verb}...')
    for line in build_images(with_re=image == IMAGE_RE, debian_mirror=debian_mirror):
        log(line)


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


def build_images(*,
                 with_re: bool = True,
                 no_cache: bool = False,
                 debian_mirror: str | None = None) -> Iterator[str]:
    """
    Build the sbclaude images, yielding log lines.

    Parameters
    ----------
    with_re : bool
        Also build the reverse-engineering image.
    no_cache : bool
        Disable the build cache.
    debian_mirror : str | None
        Debian archive mirror to bake into the base image's apt sources. The RE image
        inherits the rewritten sources, so the build argument is passed to the base only.

    Yields
    ------
    str
        Build log lines.
    """
    client = _client()
    targets: Sequence[tuple[str, str]] = [(IMAGE_BASE, 'Dockerfile')]
    if with_re:
        targets = [*targets, (IMAGE_RE, 'Dockerfile.re')]
    mirror = debian_mirror.rstrip('/') if debian_mirror else None
    with resources.as_file(resources.files('sbclaude') / 'docker') as ctx:
        labels = {HASH_LABEL: _dir_hash(Path(ctx))}
        for tag, dockerfile in targets:
            yield f'==> Building {tag} ({dockerfile})'
            buildargs = {'DEBIAN_MIRROR': mirror} if mirror and dockerfile == 'Dockerfile' else None
            for chunk in client.api.build(path=str(ctx),
                                          dockerfile=dockerfile,
                                          tag=tag,
                                          nocache=no_cache,
                                          labels=labels,
                                          buildargs=buildargs,
                                          decode=True,
                                          rm=True):
                line = chunk.get('stream') or chunk.get('error') or ''
                if line.strip():
                    yield line.rstrip('\n')
