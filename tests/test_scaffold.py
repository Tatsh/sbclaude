"""Tests for project scaffolding."""

from __future__ import annotations

from pathlib import Path
import json
import os
import stat

from sbclaude.scaffold import DEFAULT_SCENE, SCENE_PLACEHOLDER, scaffold
import pytest


def test_scaffold_writes_the_harness(tmp_path: Path) -> None:
    result = scaffold(tmp_path)
    names = {p.name for p in result.written}
    assert {'nc.mjs', 'views.json', 'NEW_GAME.md'} <= names
    assert (tmp_path / 'viewer-tests' / 'nc.mjs').is_file()
    assert (tmp_path / 'viewer-tests' / 'views.json').is_file()
    # The guide belongs to the project, not to the harness directory.
    assert (tmp_path / 'NEW_GAME.md').is_file()
    # Both directories must exist before the first capture.
    assert (tmp_path / 'viewer-tests' / 'baselines').is_dir()
    assert (tmp_path / 'viewer-tests' / 'out').is_dir()


def test_scaffold_creates_a_missing_target(tmp_path: Path) -> None:
    target = tmp_path / 'does' / 'not' / 'exist'
    scaffold(target)
    assert (target / 'viewer-tests' / 'views.json').is_file()


def test_scaffold_makes_the_runner_executable(tmp_path: Path) -> None:
    # It is invoked as ./nc.mjs; without the bit the documented usage fails.
    scaffold(tmp_path)
    mode = (tmp_path / 'viewer-tests' / 'nc.mjs').stat().st_mode
    assert mode & stat.S_IXUSR


def test_scaffold_substitutes_the_scene_id(tmp_path: Path) -> None:
    scaffold(tmp_path, scene='SonicR/ResortIsland')
    config = json.loads((tmp_path / 'viewer-tests' / 'views.json').read_text(encoding='utf-8'))
    assert config['scene'] == 'SonicR/ResortIsland'


def test_scaffold_uses_a_default_scene_and_leaves_no_placeholder(tmp_path: Path) -> None:
    scaffold(tmp_path)
    text = (tmp_path / 'viewer-tests' / 'views.json').read_text(encoding='utf-8')
    assert SCENE_PLACEHOLDER not in text
    assert json.loads(text)['scene'] == DEFAULT_SCENE


def test_scaffold_emits_valid_json(tmp_path: Path) -> None:
    # The template carries comment-ish keys; it must still parse, or nc.mjs cannot start.
    scaffold(tmp_path)
    config = json.loads((tmp_path / 'viewer-tests' / 'views.json').read_text(encoding='utf-8'))
    assert config['views'], 'template must ship at least one view'


def test_scaffold_never_clobbers_existing_work(tmp_path: Path) -> None:
    # views.json and baselines represent judgement that cannot be regenerated.
    scaffold(tmp_path)
    edited = tmp_path / 'viewer-tests' / 'views.json'
    edited.write_text('{"mine": true}', encoding='utf-8')
    result = scaffold(tmp_path)
    assert edited in result.skipped
    assert edited not in result.written
    assert edited.read_text(encoding='utf-8') == '{"mine": true}'


def test_scaffold_force_overwrites(tmp_path: Path) -> None:
    scaffold(tmp_path)
    edited = tmp_path / 'viewer-tests' / 'views.json'
    edited.write_text('{"mine": true}', encoding='utf-8')
    result = scaffold(tmp_path, force=True)
    assert edited in result.written
    assert 'mine' not in edited.read_text(encoding='utf-8')


def test_scaffold_rejects_an_unknown_kind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='unknown scaffold kind'):
        scaffold(tmp_path, 'not-a-kind')


def test_scaffold_runner_is_not_game_specific(tmp_path: Path) -> None:
    # The whole point of the split: nc.mjs is copied verbatim per project, so it must not carry
    # the game it happened to be written for.
    scaffold(tmp_path)
    code = (tmp_path / 'viewer-tests' / 'nc.mjs').read_text(encoding='utf-8')
    body = '\n'.join(line for line in code.splitlines() if not line.lstrip().startswith('//'))
    for token in ('thps', 'hangar', 'skhan'):
        assert token not in body.lower(), f'nc.mjs template mentions {token!r}'


def test_scaffold_defaults_to_the_current_directory(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    scaffold(Path(os.getcwd()))  # noqa: PTH109
    assert (tmp_path / 'viewer-tests' / 'nc.mjs').is_file()
