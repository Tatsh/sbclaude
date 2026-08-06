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
        '[tool.sbclaude]\nre = true\nnetwork = "host"\nro = ["~/x"]\nimage = "custom:latest"\n')
    cfg = load_config(path)
    assert cfg.re is True
    assert cfg.network == 'host'
    assert cfg.ro == ['~/x']
    assert cfg.image == 'custom:latest'


def test_load_config_ignores_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('re = true\nnetwork = "bridge"\n')
    cfg = load_config(path)
    assert cfg.re is False
    assert cfg.network == 'host'


def test_load_config_toggles(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\n'
                    'ghidra = true\n'
                    'android = true\n'
                    'usb = true\n'
                    'ios = true\n'
                    'x11 = true\n'
                    'ssh = true\n'
                    'gpg = true\n')
    cfg = load_config(path)
    assert (cfg.ghidra, cfg.android, cfg.usb, cfg.ios) == (True, True, True, True)
    assert (cfg.x11, cfg.ssh, cfg.gpg) == (True, True, True)


def test_load_config_debian_mirror(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\ndebian_mirror = "http://ftp.us.debian.org/debian"\n')
    assert load_config(path).debian_mirror == 'http://ftp.us.debian.org/debian'


def test_load_config_manage_uv_env_default_true(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\nnetwork = "host"\n')
    assert load_config(path).manage_uv_env is True


def test_load_config_manage_uv_env_false(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\nmanage_uv_env = false\n')
    assert load_config(path).manage_uv_env is False


def test_load_config_memory_cpus(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\nmemory = "8g"\ncpus = "4"\n')
    cfg = load_config(path)
    assert cfg.memory == '8g'
    assert cfg.cpus == '4'


def test_load_config_recover_default_false(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\nnetwork = "host"\n')
    assert load_config(path).recover is False


def test_load_config_recover_true(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\nrecover = true\n')
    assert load_config(path).recover is True


def test_load_config_project_overrides_global(tmp_path: Path) -> None:
    global_path = tmp_path / 'config.toml'
    global_path.write_text('[tool.sbclaude]\nre = true\nnetwork = "host"\n')
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[tool.sbclaude]\nnetwork = "bridge"\nx11 = true\n')
    cfg = load_config(global_path, project=project)
    assert cfg.network == 'bridge'
    assert cfg.x11 is True
    assert cfg.re is True


def test_load_config_project_env_merges(tmp_path: Path) -> None:
    global_path = tmp_path / 'config.toml'
    global_path.write_text('[tool.sbclaude.env]\nA = "1"\nB = "2"\n')
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[tool.sbclaude.env]\nB = "3"\nC = "4"\n')
    cfg = load_config(global_path, project=project)
    assert cfg.env == {'A': '1', 'B': '3', 'C': '4'}


def test_load_config_project_keeps_global_env_without_project_env(tmp_path: Path) -> None:
    global_path = tmp_path / 'config.toml'
    global_path.write_text('[tool.sbclaude.env]\nA = "1"\n')
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[tool.sbclaude]\nx11 = true\n')
    cfg = load_config(global_path, project=project)
    assert cfg.env == {'A': '1'}
    assert cfg.x11 is True


def test_load_config_project_without_table(tmp_path: Path) -> None:
    global_path = tmp_path / 'config.toml'
    global_path.write_text('[tool.sbclaude]\nx11 = true\n')
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[tool.black]\nline-length = 100\n')
    assert load_config(global_path, project=project).x11 is True


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


def test_expand_paths_glob_skips_dangling_symlink(tmp_path: Path) -> None:
    (tmp_path / 'dev1').mkdir()
    (tmp_path / 'dev2').symlink_to(tmp_path / 'gone')
    assert list(expand_paths([str(tmp_path / 'dev*')])) == [(tmp_path / 'dev1').resolve()]


def test_expand_paths_dedupes(tmp_path: Path) -> None:
    target = tmp_path / 'd'
    target.mkdir()
    assert list(expand_paths([str(target), str(target)])) == [target.resolve()]


def test_load_config_env(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\n'
                    'pass_env = ["AWS_PROFILE"]\n'
                    '[tool.sbclaude.env]\n'
                    'CLAUDE_CODE_USE_BEDROCK = "1"\n'
                    'AWS_REGION = "us-east-1"\n')
    cfg = load_config(path)
    assert cfg.env == {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': 'us-east-1'}
    assert cfg.pass_env == ['AWS_PROFILE']


def test_load_config_harden_and_docker_args(tmp_path: Path) -> None:
    path = tmp_path / 'config.toml'
    path.write_text('[tool.sbclaude]\nharden = false\ndocker_args = ["--memory", "2g"]\n')
    cfg = load_config(path)
    assert cfg.harden is False
    assert cfg.docker_args == ['--memory', '2g']
