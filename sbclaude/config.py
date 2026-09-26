"""
Configuration loading for sbclaude.

All options live under a ``[tool.sbclaude]`` table. They are read from the global config
file at :func:`config_path` (``~/.config/sbclaude/config.toml`` on Linux), overlaid with the
selected profile from :func:`profiles_dir` (``~/.config/sbclaude/profiles/<name>.toml``),
and, when a project directory is given, overlaid with the project's ``pyproject.toml``
``[tool.sbclaude]`` table (project values win). Every key is optional.

A profile file holds the same keys as the global config, under a
``[tool.sbclaude.profile]`` table:

.. code-block:: toml

    [tool.sbclaude.profile]
    ssh = true
    network = "bridge"

    [tool.sbclaude.profile.env]
    ENV_VAR = "some value"

.. code-block:: toml

    [tool.sbclaude]
    re = true
    x11 = true
    network = "host"
    ro = ["~/dev*", "~/ghidra_scripts", "~/Downloads"]
    rw = []

    [tool.sbclaude.env]
    CLAUDE_CODE_USE_BEDROCK = "1"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
import re

from platformdirs import user_config_path
import tomlkit

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = ('Config', 'config_path', 'expand_paths', 'load_config', 'profiles_dir')

DEFAULT_NETWORK = 'host'
"""Default network for Docker."""
_GLOBAL_ONLY_KEYS = frozenset({'claude_binary'})
"""
Keys honoured only from the global config, and ignored in a project's ``pyproject.toml``.

``claude_binary`` names the executable the box runs as claude, with the mounted ``~/.claude``
credentials within its reach. A checked-out repository choosing that would be arbitrary code
execution on behalf of whoever cloned it, so this one setting does not follow the usual rule that
project values win.
"""


def config_path() -> Path:
    """
    Return the path to the sbclaude TOML config file.

    Returns
    -------
    Path
        ``~/.config/sbclaude/config.toml`` on Linux.
    """
    return user_config_path('sbclaude') / 'config.toml'


_PROFILE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
"""Valid profile names, matched against the whole name."""


def profiles_dir(config_file: Path | None = None) -> Path:
    """
    Return the directory holding the sbclaude TOML profile files.

    Parameters
    ----------
    config_file : Path | None
        The global config file the profiles sit beside, or ``None`` for the default location.

    Returns
    -------
    Path
        ``~/.config/sbclaude/profiles`` on Linux.
    """
    return (config_file or config_path()).parent / 'profiles'


@dataclass
class Config:
    """Resolved sbclaude configuration."""

    agent: str = 'claude'
    """Coding agent the box runs, ``claude`` or ``opencode``."""
    android: bool = False
    """Whether to mount the Android SDK and related devices."""
    claude_binary: str | None = None
    """
    Host ``claude`` executable to mount, or ``None`` to take the first one on ``PATH``.

    Naming one pins the session to a particular build rather than to whatever ``PATH`` reaches
    first. ``~`` is expanded. It must be an executable file where sbclaude runs, which is checked
    before the box starts, and it must also exist on the machine the Docker daemon runs on, since
    that is where the bind-mount source is resolved.

    Read from the global config only. A project's ``pyproject.toml`` cannot set it, because that
    would let a cloned repository choose what runs as claude.
    """
    cpus: str | None = None
    """Docker CPU limit (e.g. ``4``); ``None`` leaves the CPU uncapped."""
    debian_mirror: str | None = None
    """Debian archive mirror used when building the image, or ``None`` for the default."""
    default_profile: str | None = None
    """
    Profile applied when ``--profile`` is absent, or ``None`` for no profile.

    Read from the global config only. Profiles and projects cannot select a further profile.
    """
    desktop: bool = False
    """Whether to forward the host desktop session for portal screen capture and input."""
    docker: bool = False
    """
    Whether to forward the host Docker daemon socket into the box.

    Control of the daemon is root on the host unless the daemon is rootless. Point ``DOCKER_HOST``
    at a rootless daemon reserved for sbclaude to limit an escape to the rootless daemon's user.
    """
    docker_args: list[str] = field(default_factory=list)
    """Extra arguments passed to ``docker run``."""
    env: dict[str, str] = field(default_factory=dict)
    """Fixed environment variables injected into the box."""
    fullscreen: bool = True
    """
    Whether to force claude's fullscreen TUI in the box.

    Turning it off keeps a claude that fails on start from erasing its own error message with the
    terminal's alternate screen.
    """
    gentoo: bool = False
    """
    Whether to run the Gentoo image, for ebuild work, instead of the Debian one.

    A Gentoo box is saved between sessions and always has passwordless ``sudo``.
    """
    ghidra: bool = False
    """Whether to mount the host Ghidra installation read-only."""
    gpg: bool = False
    """Whether to mount the host GnuPG home and agent socket for commit signing."""
    gpu: bool = False
    """Whether to expose the host NVIDIA GPU(s) via the NVIDIA Container Toolkit."""
    harden: bool = True
    """Whether to apply the container hardening flags."""
    image: str | None = None
    """Image override, or ``None`` to use the default."""
    ios: bool = False
    """Whether to mount the host usbmuxd socket so frida can reach an iOS device."""
    keyring: bool = False
    """
    Whether to forward the D-Bus session bus so the box reaches the host Secret Service.

    Use this where an application in the box cannot take its secret from an environment variable
    and insists on the Secret Service itself. It grants the whole session bus rather than one
    secret: every entry in the login keyring becomes readable, along with the other services on
    that bus. Prefer ``keyring_keys`` wherever an environment variable will do.
    """
    keyring_keys: list[str] = field(default_factory=list)
    """
    Named host secrets to copy into the box, each written ``NAME=SERVICE``.

    NAME becomes an environment variable and SERVICE is the keyring ``service`` attribute the
    secret is stored under, so ``GH_TOKEN=gh:github.com`` reads the ``gh`` token and presents it as
    ``GH_TOKEN``, and ``GITLAB_TOKEN=glab:gitlab.com`` does the same for ``glab``. Reading uses
    ``secret-tool`` on the host.

    Prefer this over ``keyring`` whenever the applications in the box accept their secrets through
    the environment. It grants the named secrets rather than the whole bus, and it needs no bus
    forwarding.
    """
    manage_uv_env: bool = True
    """
    Whether to give the box its own virtualenv instead of the project's ``.venv``.

    Only a project that looks like Python gets one. Setting ``venv_dir`` manages the environment
    even where this is false, since naming a directory is asking for one.
    """
    setup_venv: bool = True
    """
    Whether the box provisions that virtualenv on start when the project looks like Python.

    Provisioning runs ``uv sync``, which refreshes the project's ``uv.lock``, so ``modify = false``
    suppresses it regardless of this setting.
    """
    memory: str | None = None
    """Docker memory limit (e.g. ``8g``); ``None`` auto-caps from host RAM, ``0`` disables."""
    modify: bool = True
    """Whether sbclaude itself may write into the project directory."""
    network: str = DEFAULT_NETWORK
    """Docker network mode."""
    pass_env: list[str] = field(default_factory=list)
    """Host environment variable names to forward into the box."""
    re: bool = False
    """Whether to enable the reverse-engineering host mounts (Ghidra and Android together)."""
    recover: bool = False
    """Whether to install cc-session-recover (auto-resume) into the project by default."""
    ro: list[str] = field(default_factory=list)
    """Read-only mount patterns (globs and ``~`` accepted)."""
    rw: list[str] = field(default_factory=list)
    """Read-write mount patterns (globs and ``~`` accepted)."""
    ssh: bool = False
    """Whether to mount the host ``~/.ssh`` read-only and forward the ssh-agent socket."""
    sudo: bool = False
    """Whether to allow passwordless ``sudo`` in the box (drops ``no-new-privileges``)."""
    usb: bool = False
    """Whether to expose ``/dev/bus/usb`` for adb over USB."""
    venv_dir: str | None = None
    """
    Directory inside the box holding its virtualenv, or ``None`` to keep it beside the project.

    Point this at a mounted volume (via ``docker_args``) to keep the environment across boxes
    without writing anything into the project. With ``None`` and ``modify = false`` the virtualenv
    lands inside the container instead, and does not survive it.
    """
    wayland: bool = False
    """Whether to forward the Wayland compositor socket."""
    x11: bool = False
    """Whether to forward X11 ``DISPLAY`` and ``XAUTHORITY``."""


def _tool_table(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    tool = tomlkit.parse(path.read_text(encoding='utf-8')).get('tool')
    sbclaude = tool.get('sbclaude') if isinstance(tool, dict) else None
    return dict(sbclaude) if isinstance(sbclaude, dict) else {}


def _str_list(value: Any) -> list[str]:
    return [str(x) for x in value] if value else []


def _str_dict(value: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(value).items()} if value else {}


def _merge_data(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Overlay one raw config table on another, merging the ``env`` tables key by key.

    Parameters
    ----------
    base : dict[str, Any]
        The lower-precedence table.
    overlay : dict[str, Any]
        The higher-precedence table whose values win.

    Returns
    -------
    dict[str, Any]
        The merged table.
    """
    env = {**_str_dict(base.get('env')), **_str_dict(overlay.get('env'))}
    return {**base, **overlay, **({'env': env} if env else {})}


def _profile_table(path: Path) -> dict[str, Any]:
    """
    Read the profile table from a profile file.

    The canonical form is ``[tool.sbclaude.profile]``. A bare ``[tool.sbclaude]`` table in
    the same file is also honoured, with the ``profile`` table winning over it.

    Parameters
    ----------
    path : Path
        The profile file to read.

    Returns
    -------
    dict[str, Any]
        The profile's raw settings.
    """
    tool = tomlkit.parse(path.read_text(encoding='utf-8')).get('tool')
    sbclaude = tool.get('sbclaude') if isinstance(tool, dict) else None
    if not isinstance(sbclaude, dict):
        return {}
    base = {key: value for key, value in sbclaude.items() if key != 'profile'}
    overlay = sbclaude.get('profile')
    overlay = dict(overlay) if isinstance(overlay, dict) else {}
    return _merge_data(dict(base), overlay)


def load_config(path: Path | None = None,
                *,
                project: Path | None = None,
                profile: str | None = None) -> Config:
    """
    Load the ``[tool.sbclaude]`` config, returning defaults when it does not exist.

    The global config file is read first, then the selected profile (``profile``, or the
    global ``default_profile`` when no profile is passed), so profile values override the
    global ones. When ``project`` is given, the project's ``pyproject.toml``
    ``[tool.sbclaude]`` table is overlaid last, so project values override both (the
    ``env`` tables are merged key by key at each step).

    Parameters
    ----------
    path : Path | None
        Override the global config path (mainly for tests).
    project : Path | None
        Project directory whose ``pyproject.toml`` overrides the global config.
    profile : str | None
        Profile from the profiles directory, overriding ``default_profile``.

    Returns
    -------
    Config
        The parsed configuration.

    Raises
    ------
    FileNotFoundError
        If the selected profile has no file in the profiles directory.
    ValueError
        If the profile name is not a plain file stem.
    """
    global_path = path or config_path()
    data = _tool_table(global_path)
    if profile is not None:
        selected: Any = profile
    elif data.get('default_profile'):
        selected = data['default_profile']
    else:
        selected = None
    if selected is not None:
        name = str(selected)
        if not _PROFILE_NAME.fullmatch(name):
            msg = (f'invalid profile name {name!r}; use letters, digits, dots, underscores, '
                   'and hyphens')
            raise ValueError(msg)
        profile_file = profiles_dir(global_path) / f'{name}.toml'
        if not profile_file.is_file():
            msg = f'profile {name!r} not found at `{profile_file}`'
            raise FileNotFoundError(msg)
        prof = _profile_table(profile_file)
        prof.pop('default_profile', None)
        data = _merge_data(data, prof)
    if project is not None and (proj := _tool_table(project / 'pyproject.toml')):
        proj = {key: value for key, value in proj.items() if key not in _GLOBAL_ONLY_KEYS}
        proj.pop('default_profile', None)
        data = _merge_data(data, proj)
    return Config(
        agent=str(data.get('agent', 'claude')),
        default_profile=(str(data['default_profile']) if data.get('default_profile') else None),
        android=bool(data.get('android', False)),
        claude_binary=(str(data['claude_binary']) if data.get('claude_binary') else None),
        cpus=(str(data['cpus']) if data.get('cpus') is not None else None),
        debian_mirror=(str(data['debian_mirror']) if data.get('debian_mirror') else None),
        desktop=bool(data.get('desktop', False)),
        docker=bool(data.get('docker', False)),
        docker_args=_str_list(data.get('docker_args')),
        env=_str_dict(data.get('env')),
        fullscreen=bool(data.get('fullscreen', True)),
        gentoo=bool(data.get('gentoo', False)),
        ghidra=bool(data.get('ghidra', False)),
        gpg=bool(data.get('gpg', False)),
        gpu=bool(data.get('gpu', False)),
        harden=bool(data.get('harden', True)),
        image=(str(data['image']) if data.get('image') else None),
        ios=bool(data.get('ios', False)),
        keyring=bool(data.get('keyring', False)),
        keyring_keys=_str_list(data.get('keyring_keys')),
        manage_uv_env=bool(data.get('manage_uv_env', True)),
        memory=(str(data['memory']) if data.get('memory') is not None else None),
        modify=bool(data.get('modify', True)),
        network=str(data.get('network', DEFAULT_NETWORK)),
        pass_env=_str_list(data.get('pass_env')),
        re=bool(data.get('re', False)),
        recover=bool(data.get('recover', False)),
        ro=_str_list(data.get('ro')),
        rw=_str_list(data.get('rw')),
        setup_venv=bool(data.get('setup_venv', True)),
        ssh=bool(data.get('ssh', False)),
        sudo=bool(data.get('sudo', False)),
        usb=bool(data.get('usb', False)),
        venv_dir=(str(data['venv_dir']) if data.get('venv_dir') else None),
        wayland=bool(data.get('wayland', False)),
        x11=bool(data.get('x11', False)))


def expand_paths(patterns: Sequence[str]) -> Iterator[Path]:
    """
    Expand ``~`` and globs in mount patterns, keeping only existing paths.

    Parameters
    ----------
    patterns : Sequence[str]
        Patterns such as ``~/dev*``.

    Yields
    ------
    Path
        De-duplicated resolved paths that exist on disk, order preserved.
    """
    seen: dict[Path, None] = {}
    for pattern in patterns:
        expanded = Path(pattern).expanduser()
        if any(ch in expanded.name for ch in '*?['):
            matches = sorted(expanded.parent.glob(expanded.name))
        else:
            matches = [expanded] if expanded.exists() else []
        for match in matches:
            resolved = match.resolve()
            if resolved.exists():
                seen.setdefault(resolved, None)
    yield from seen
