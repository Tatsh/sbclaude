"""
Configuration loading for sbclaude.

The config file is TOML at :func:`config_path` (``~/.config/sbclaude/config.toml`` on
Linux). All keys are optional.

.. code-block:: toml

    default_flags = ["--re", "--x11"]
    network = "bridge"
    # image = "sbclaude-re:latest"
    ro = ["~/dev*", "~/ghidra_scripts", "~/Downloads"]
    rw = []
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

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

    default_flags: list[str] = field(default_factory=list)
    """Flags applied to every run."""
    ro: list[str] = field(default_factory=list)
    """Read-only mount patterns (globs and ``~`` accepted)."""
    rw: list[str] = field(default_factory=list)
    """Read-write mount patterns (globs and ``~`` accepted)."""
    env: dict[str, str] = field(default_factory=dict)
    """Fixed environment variables injected into the box."""
    pass_env: list[str] = field(default_factory=list)
    """Host environment variable names to forward into the box."""
    docker_args: list[str] = field(default_factory=list)
    """Extra arguments passed to ``docker run``."""
    image: str | None = None
    """Image override, or ``None`` to auto-select."""
    network: str = DEFAULT_NETWORK
    """Docker network mode."""
    harden: bool = True
    """Whether to apply the container hardening flags."""


def load_config(path: Path | None = None) -> Config:
    """
    Load the TOML config, returning defaults when it does not exist.

    Parameters
    ----------
    path : Path | None
        Override the config path (mainly for tests).

    Returns
    -------
    Config
        The parsed configuration.
    """
    path = path or config_path()
    if not path.is_file():
        return Config()
    data = tomlkit.parse(path.read_text())
    return Config(default_flags=[str(x) for x in data.get('default_flags', [])],
                  ro=[str(x) for x in data.get('ro', [])],
                  rw=[str(x) for x in data.get('rw', [])],
                  env={
                      str(k): str(v)
                      for k, v in dict(data.get('env', {})).items()
                  },
                  pass_env=[str(x) for x in data.get('pass_env', [])],
                  docker_args=[str(x) for x in data.get('docker_args', [])],
                  image=(str(data['image']) if data.get('image') else None),
                  network=str(data.get('network', DEFAULT_NETWORK)),
                  harden=bool(data.get('harden', True)))


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
