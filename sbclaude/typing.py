"""Typing helpers for sbclaude."""

from __future__ import annotations

from typing import Literal, TypeAlias

__all__ = ('Agent', 'Distro')

Agent: TypeAlias = Literal['claude', 'opencode']
"""Coding agent the box runs."""
Distro: TypeAlias = Literal['debian', 'gentoo']
"""Distribution the box's image is built on."""
