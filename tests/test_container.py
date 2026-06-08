"""Tests for docker orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import json
import os
import tempfile

from sbclaude import container
import docker.errors
import pytest

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_default_name() -> None:
    assert container.default_name(Path('/a/b/My Proj')) == 'sbclaude-My-Proj'


def test_claude_binary_missing(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.which', return_value=None)
    with pytest.raises(FileNotFoundError):
        container.claude_binary()


def test_build_run_argv_basic(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'proj'
    project.mkdir()
    argv, cleanup = container.build_run_argv(
        container.RunSpec(project=project, name='sbclaude-proj'))
    assert argv[:3] == ['docker', 'run', '--rm']
    assert 'sbclaude-proj' in argv
    assert f'{project}:{project}' in argv
    assert argv[-1] == container.IMAGE_BASE
    assert cleanup is None


def test_build_run_argv_defaults_to_host_network(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert argv[argv.index('--network') + 1] == 'host'


def test_build_run_argv_re_image(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'proj'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_re=True))
    assert argv[-1] == container.IMAGE_RE


def test_build_run_argv_patches_settings(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    claude_dir = tmp_path / '.claude'
    claude_dir.mkdir()
    (claude_dir / 'settings.json').write_text('{"sandbox": {"enabled": true}, "keep": 1}')
    project = tmp_path / 'p'
    project.mkdir()
    _, cleanup = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert cleanup is not None
    data = json.loads(cleanup.read_text())
    assert data['sandbox'] == {'enabled': False}
    assert data['skipDangerousModePermissionPrompt']
    assert data['keep'] == 1
    cleanup.unlink()


def test_build_run_argv_extra_mounts(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    ro = tmp_path / 'ro'
    ro.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', ro=[ro], rw=[project]))
    assert f'{ro}:{ro}:ro' in argv
    # The project is skipped from rw extras (already mounted rw).
    assert argv.count(f'{project}:{project}') == 1


def test_run_invokes_subprocess(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.ensure_image')
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 7
    project = tmp_path / 'p'
    project.mkdir()
    assert container.run(container.RunSpec(project=project, name='n')) == 7
    assert completed.called


def test_shell_invokes_docker(mocker: MockerFixture) -> None:
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 0
    assert container.shell('n') == 0
    assert completed.call_args[0][0] == ['docker', 'exec', '-it', 'n', 'bash']


def test_list_managed(mocker: MockerFixture) -> None:
    ctr = mocker.MagicMock()
    ctr.name = 'sbclaude-x'
    ctr.status = 'running'
    ctr.labels = {'sbclaude.project': '/p'}
    ctr.image.tags = ['sbclaude:latest']
    client = mocker.MagicMock()
    client.containers.list.return_value = [ctr]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    out = list(container.list_managed())
    assert out == [{
        'name': 'sbclaude-x',
        'image': 'sbclaude:latest',
        'status': 'running',
        'project': '/p'
    }]


def test_stop_named(mocker: MockerFixture) -> None:
    ctr = mocker.MagicMock()
    ctr.name = 'n'
    client = mocker.MagicMock()
    client.containers.list.return_value = [ctr]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert list(container.stop('n')) == ['n']
    ctr.remove.assert_called_once_with(force=True)


def test_build_images(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'Step 1/2\n'}, {'error': 'boom'}, {'stream': '  '}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    lines = list(container.build_images(with_re=False))
    assert any('Building sbclaude:latest' in line for line in lines)
    assert 'Step 1/2' in lines
    assert 'boom' in lines


def test_build_run_argv_android(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    sdk = tmp_path / 'sdk'
    sdk.mkdir()
    (tmp_path / '.android').mkdir()
    mocker.patch.dict(os.environ, {'ANDROID_HOME': str(sdk)})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_android=True))
    assert f'{sdk}:{sdk}:ro' in argv
    assert f'{tmp_path / ".android"}:{tmp_path / ".android"}' in argv


def test_build_run_argv_x11_with_cookie(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    cookie = tmp_path / 'xauth'
    cookie.write_text('x')
    mocker.patch.dict(os.environ, {'XAUTHORITY': str(cookie), 'DISPLAY': ':9'})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_x11=True))
    assert 'DISPLAY=:9' in argv
    assert f'XAUTHORITY={cookie}' in argv


def test_build_run_argv_x11_without_cookie(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    # A uid whose /run/user dir has no cookie forces the warning branch.
    mocker.patch('sbclaude.container.os.getuid', return_value=4242424)
    mocker.patch.dict(os.environ, {'XAUTHORITY': '', 'DISPLAY': ':0'})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_x11=True))
    assert 'DISPLAY=:0' in argv
    assert not any(a.startswith('XAUTHORITY=') for a in argv)


def test_run_cleans_up_patched_settings(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.ensure_image')
    claude_dir = tmp_path / '.claude'
    claude_dir.mkdir()
    (claude_dir / 'settings.json').write_text('{"sandbox": {"enabled": true}}')
    project = tmp_path / 'p'
    project.mkdir()
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 0
    tmpdir = Path(tempfile.gettempdir())
    before = set(tmpdir.glob('sbclaude-settings.*'))
    assert container.run(container.RunSpec(project=project, name='n')) == 0
    assert set(tmpdir.glob('sbclaude-settings.*')) == before


def test_ensure_image_builds_when_missing(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=False)
    build = mocker.patch('sbclaude.container.build_images', return_value=iter(['built']))
    logged: list[str] = []
    container.ensure_image(container.IMAGE_BASE, log=logged.append)
    build.assert_called_once_with(with_re=False)
    assert 'built' in logged


def test_ensure_image_rebuilds_when_stale(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=True)
    build = mocker.patch('sbclaude.container.build_images', return_value=iter(['built']))
    logged: list[str] = []
    container.ensure_image(container.IMAGE_RE, log=logged.append)
    build.assert_called_once_with(with_re=True)
    assert any('rebuilding' in line for line in logged)


def test_ensure_image_skips_when_current(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=True)
    build = mocker.patch('sbclaude.container.build_images')
    container.ensure_image(container.IMAGE_RE)
    build.assert_not_called()


def test_image_exists(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.image_exists('sbclaude:latest') is True
    client.images.get.side_effect = docker.errors.ImageNotFound('nope')
    assert container.image_exists('sbclaude:latest') is False


def test_image_up_to_date_missing(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.images.get.side_effect = docker.errors.ImageNotFound('x')
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.image_up_to_date('sbclaude:latest') is False


def test_image_up_to_date_stale(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    # A bogus label can never equal the real context hash, so this is stale.
    client.images.get.return_value.labels = {'sbclaude.context_hash': 'not-the-real-hash'}
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.image_up_to_date('sbclaude:latest') is False


def test_delete_images(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    # RE removed successfully; base already absent.
    client.images.remove.side_effect = [None, docker.errors.ImageNotFound('x')]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.delete_images() == [container.IMAGE_RE]


def test_config_dir_default(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(os.environ, clear=False)
    os.environ.pop('CLAUDE_CONFIG_DIR', None)
    assert container.config_dir(tmp_path) == tmp_path / '.claude'


def test_config_dir_override(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(tmp_path / 'cfg')})
    assert container.config_dir(tmp_path) == tmp_path / 'cfg'


def test_build_run_argv_env(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', env={'CLAUDE_CODE_USE_BEDROCK': '1'}))
    idx = argv.index('CLAUDE_CODE_USE_BEDROCK=1')
    assert argv[idx - 1] == '-e'


def test_build_run_argv_config_dir_override(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    cfg = tmp_path / 'alternate'
    cfg.mkdir()
    mocker.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(cfg)})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert f'{cfg}:{cfg}' in argv
    assert f'CLAUDE_CONFIG_DIR={cfg}' in argv


def test_build_run_argv_hardened_by_default(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert 'no-new-privileges' in argv
    assert '--cap-drop' in argv
    assert 'ALL' in argv
    assert '--pids-limit' in argv


def test_build_run_argv_no_harden(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', harden=False))
    assert 'no-new-privileges' not in argv
    assert '--cap-drop' not in argv


def test_build_run_argv_extra_args(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', extra_args=['--memory', '2g']))
    idx = argv.index('--memory')
    assert argv[idx + 1] == '2g'
    assert argv.index(container.IMAGE_BASE) > idx
