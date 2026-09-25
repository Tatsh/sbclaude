"""Typing helpers for sbclaude."""

from __future__ import annotations

from typing import Literal, TypeAlias

__all__ = ('Agent',)

Agent: TypeAlias = Literal['claude', 'opencode']
"""Coding agent the box runs."""
