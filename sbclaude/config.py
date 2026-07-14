"""
Configuration loading for sbclaude.

All options live under a ``[tool.sbclaude]`` table. They are read from the global config
file at :func:`config_path` (``~/.config/sbclaude/config.toml`` on Linux) and, when a project
directory is given, overlaid with the project's ``pyproject.toml`` ``[tool.sbclaude]`` table
(project values win). Every key is optional.

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

from platformdirs import user_config_path
import tomlkit

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = ('Config', 'config_path', 'expand_paths', 'load_config')

DEFAULT_NETWORK = 'host'
"""Default network for Docker."""


def config_path() -> Path:
    """
    Return the path to the sbclaude TOML config file.

    Returns
    -------
    Path
        ``~/.config/sbclaude/config.toml`` on Linux.
    """
    return user_config_path('sbclaude') / 'config.toml'


@dataclass
class Config:
    """Resolved sbclaude configuration."""

    android: bool = False
    """Whether to mount the Android SDK and related devices."""
    cpus: str | None = None
    """Docker CPU limit (e.g. ``4``); ``None`` leaves the CPU uncapped."""
    debian_mirror: str | None = None
    """Debian archive mirror used when building the image, or ``None`` for the default."""
    docker_args: list[str] = field(default_factory=list)
    """Extra arguments passed to ``docker run``."""
    env: dict[str, str] = field(default_factory=dict)
    """Fixed environment variables injected into the box."""
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
    manage_uv_env: bool = True
    """Whether to point ``UV_PROJECT_ENVIRONMENT`` at a container-local path by default."""
    memory: str | None = None
    """Docker memory limit (e.g. ``8g``); ``None`` auto-caps from host RAM, ``0`` disables."""
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
    """Whether to mount the host ``~/.ssh`` directory read-only for SSH git remotes."""
    usb: bool = False
    """Whether to expose ``/dev/bus/usb`` for adb over USB."""
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


def load_config(path: Path | None = None, *, project: Path | None = None) -> Config:
    """
    Load the ``[tool.sbclaude]`` config, returning defaults when it does not exist.

    The global config file is read first; when ``project`` is given, the project's
    ``pyproject.toml`` ``[tool.sbclaude]`` table is overlaid on top, so project values
    override the global ones (the ``env`` tables are merged key by key).

    Parameters
    ----------
    path : Path | None
        Override the global config path (mainly for tests).
    project : Path | None
        Project directory whose ``pyproject.toml`` overrides the global config.

    Returns
    -------
    Config
        The parsed configuration.
    """
    data = _tool_table(path or config_path())
    if project is not None and (proj := _tool_table(project / 'pyproject.toml')):
        env = {**_str_dict(data.get('env')), **_str_dict(proj.get('env'))}
        data = {**data, **proj, **({'env': env} if env else {})}
    return Config(android=bool(data.get('android', False)),
                  cpus=(str(data['cpus']) if data.get('cpus') is not None else None),
                  debian_mirror=(str(data['debian_mirror']) if data.get('debian_mirror') else None),
                  docker_args=_str_list(data.get('docker_args')),
                  env=_str_dict(data.get('env')),
                  ghidra=bool(data.get('ghidra', False)),
                  gpg=bool(data.get('gpg', False)),
                  gpu=bool(data.get('gpu', False)),
                  harden=bool(data.get('harden', True)),
                  image=(str(data['image']) if data.get('image') else None),
                  ios=bool(data.get('ios', False)),
                  manage_uv_env=bool(data.get('manage_uv_env', True)),
                  memory=(str(data['memory']) if data.get('memory') is not None else None),
                  network=str(data.get('network', DEFAULT_NETWORK)),
                  pass_env=_str_list(data.get('pass_env')),
                  re=bool(data.get('re', False)),
                  recover=bool(data.get('recover', False)),
                  ro=_str_list(data.get('ro')),
                  rw=_str_list(data.get('rw')),
                  ssh=bool(data.get('ssh', False)),
                  usb=bool(data.get('usb', False)),
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
