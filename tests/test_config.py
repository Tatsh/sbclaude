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


def test_load_config_debian_mirror(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('debian_mirror = "http://ftp.us.debian.org/debian"\n')
    assert load_config(path).debian_mirror == 'http://ftp.us.debian.org/debian'


def test_load_config_manage_uv_env_default_true(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('network = "host"\n')
    assert load_config(path).manage_uv_env is True


def test_load_config_manage_uv_env_false(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('manage_uv_env = false\n')
    assert load_config(path).manage_uv_env is False


def test_load_config_memory_cpus(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('memory = "8g"\ncpus = "4"\n')
    cfg = load_config(path)
    assert cfg.memory == '8g'
    assert cfg.cpus == '4'


def test_load_config_recover_default_false(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('network = "host"\n')
    assert load_config(path).recover is False


def test_load_config_recover_true(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('recover = true\n')
    assert load_config(path).recover is True


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
