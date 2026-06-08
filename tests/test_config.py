"""Tests for config loading."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sbclaude.config import Config, config_path, expand_paths, load_config

if TYPE_CHECKING:
    from pathlib import Path


def test_config_path() -> None:
    assert config_path().name == 'config.toml'


def test_load_config_missing(tmp_path: Path) -> None:
    assert load_config(tmp_path / 'nope.toml') == Config()


def test_load_config_values(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text(
        'default_flags = ["--re"]\nnetwork = "host"\nro = ["~/x"]\nimage = "sbclaude-re:latest"\n')
    cfg = load_config(path)
    assert cfg.default_flags == ['--re']
    assert cfg.network == 'host'
    assert cfg.ro == ['~/x']
    assert cfg.image == 'sbclaude-re:latest'


def test_expand_paths_glob(tmp_path: Path) -> None:
    (tmp_path / 'dev1').mkdir()
    (tmp_path / 'dev2').mkdir()
    assert len(list(expand_paths([str(tmp_path / 'dev*')]))) == 2


def test_expand_paths_plain(tmp_path: Path) -> None:
    target = tmp_path / 'd'
    target.mkdir()
    assert list(expand_paths([str(target)])) == [target.resolve()]


def test_expand_paths_missing(tmp_path: Path) -> None:
    assert list(expand_paths([str(tmp_path / 'nope')])) == []


def test_expand_paths_dedupes(tmp_path: Path) -> None:
    target = tmp_path / 'd'
    target.mkdir()
    assert list(expand_paths([str(target), str(target)])) == [target.resolve()]


def test_load_config_env(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('pass_env = ["AWS_PROFILE"]\n'
                    '[env]\n'
                    'CLAUDE_CODE_USE_BEDROCK = "1"\n'
                    'AWS_REGION = "us-east-1"\n')
    cfg = load_config(path)
    assert cfg.env == {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': 'us-east-1'}
    assert cfg.pass_env == ['AWS_PROFILE']


def test_load_config_harden_and_docker_args(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('harden = false\ndocker_args = ["--memory", "2g"]\n')
    cfg = load_config(path)
    assert cfg.harden is False
    assert cfg.docker_args == ['--memory', '2g']
