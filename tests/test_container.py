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


def test_unique_name() -> None:
    name = container.unique_name(Path('/a/b/My Proj'))
    assert name.startswith('sbclaude-My-Proj-')
    assert name != container.unique_name(Path('/a/b/My Proj'))


def test_uv_project_environment() -> None:
    assert container.uv_project_environment(
        Path('/a/b/My Proj')) == f'{container.UV_ENV_PREFIX}My-Proj'


@pytest.mark.parametrize(('name', 'expected'), [
    ('a@b:c d', 'a-b-c-d'),
    ('keep.this_one-here', 'keep.this_one-here'),
])
def test_uv_project_environment_sanitises(name: str, expected: str) -> None:
    assert container.uv_project_environment(
        Path('/a/b') / name) == f'{container.UV_ENV_PREFIX}{expected}'


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


def test_stop_by_project(mocker: MockerFixture) -> None:
    ctr = mocker.MagicMock()
    ctr.name = 'sbclaude-p-abc123'
    client = mocker.MagicMock()
    client.containers.list.return_value = [ctr]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert list(container.stop(project=Path('/p'))) == ['sbclaude-p-abc123']
    ctr.remove.assert_called_once_with(force=True)
    assert client.containers.list.call_args.kwargs['filters'] == {
        'label': ['sbclaude.managed=1', 'sbclaude.project=/p']
    }


def test_project_containers(mocker: MockerFixture) -> None:
    ctr = mocker.MagicMock()
    ctr.name = 'sbclaude-p-abc123'
    client = mocker.MagicMock()
    client.containers.list.return_value = [ctr]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.project_containers(Path('/p')) == ['sbclaude-p-abc123']


def test_build_images(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'Step 1/2\n'}, {'error': 'boom'}, {'stream': '  '}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    lines = list(container.build_images(with_re=False))
    assert any('Building sbclaude:latest' in line for line in lines)
    assert 'Step 1/2' in lines
    assert 'boom' in lines


def test_build_images_debian_mirror_base_only(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'ok\n'}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    list(container.build_images(debian_mirror='http://ftp.us.debian.org/debian/'))
    by_dockerfile = {
        c.kwargs['dockerfile']: c.kwargs['buildargs']
        for c in client.api.build.call_args_list
    }
    # Trailing slash stripped, and only the base build receives the mirror argument.
    assert by_dockerfile['Dockerfile'] == {'DEBIAN_MIRROR': 'http://ftp.us.debian.org/debian'}
    assert by_dockerfile['Dockerfile.re'] is None


def test_build_images_no_mirror_passes_none(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'ok\n'}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    list(container.build_images(with_re=False))
    assert client.api.build.call_args.kwargs['buildargs'] is None


def test_ensure_image_passes_debian_mirror(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=False)
    build = mocker.patch('sbclaude.container.build_images', return_value=iter(['built']))
    container.ensure_image(container.IMAGE_BASE, debian_mirror='http://m/debian')
    build.assert_called_once_with(with_re=False, debian_mirror='http://m/debian')


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


def test_build_run_argv_ios(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    sock_path = tmp_path / 'usbmuxd'
    lockdown = tmp_path / 'lockdown'
    lockdown.mkdir()
    mocker.patch('sbclaude.container.USBMUXD_SOCKET', sock_path)
    mocker.patch('sbclaude.container.LOCKDOWN_DIR', lockdown)
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ios=True))
    assert f'{sock_path}:{sock_path}' in argv
    assert f'{lockdown}:{lockdown}:ro' in argv


def test_build_run_argv_ios_no_socket(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.USBMUXD_SOCKET', tmp_path / 'absent')
    mocker.patch('sbclaude.container.LOCKDOWN_DIR', tmp_path / 'absent-lockdown')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ios=True))
    assert not any('usbmuxd' in arg or 'absent' in arg for arg in argv)


def test_build_run_argv_ssh(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    ssh = tmp_path / '.ssh'
    ssh.mkdir()
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    assert f'{ssh}:{ssh}:ro' in argv


def test_build_run_argv_gpg(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'GNUPGHOME': ''})
    gnupg = tmp_path / '.gnupg'
    gnupg.mkdir()
    sock = tmp_path / 'run' / 'S.gpg-agent'
    mocker.patch('sbclaude.container.sp.run', return_value=mocker.MagicMock(stdout=f'{sock}\n'))
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpg=True))
    assert f'{gnupg}:{gnupg}' in argv
    assert f'{sock}:{gnupg / "S.gpg-agent"}' in argv


def test_build_run_argv_gpg_socket_in_home(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'GNUPGHOME': ''})
    gnupg = tmp_path / '.gnupg'
    gnupg.mkdir()
    sock = gnupg / 'S.gpg-agent'
    mocker.patch('sbclaude.container.sp.run', return_value=mocker.MagicMock(stdout=f'{sock}\n'))
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpg=True))
    # The socket already lives in the mounted home, so no separate overlay is added.
    assert argv.count(f'{sock}:{sock}') == 0


def test_build_run_argv_gpg_custom_home(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    custom = tmp_path / 'gnupg-home'
    custom.mkdir()
    mocker.patch.dict(os.environ, {'GNUPGHOME': str(custom)})
    mocker.patch('sbclaude.container.sp.run', side_effect=FileNotFoundError)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpg=True))
    assert f'{custom}:{custom}' in argv
    assert f'GNUPGHOME={custom}' in argv
    # gpgconf is absent, so no agent socket overlay is mounted.
    assert not any('S.gpg-agent' in arg for arg in argv)


def test_build_run_argv_gpg_no_home(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'GNUPGHOME': ''})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpg=True))
    assert not any('.gnupg' in arg or 'S.gpg-agent' in arg for arg in argv)


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
    build.assert_called_once_with(with_re=False, debian_mirror=None)
    assert 'built' in logged


def test_ensure_image_rebuilds_when_stale(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=True)
    build = mocker.patch('sbclaude.container.build_images', return_value=iter(['built']))
    logged: list[str] = []
    container.ensure_image(container.IMAGE_RE, log=logged.append)
    build.assert_called_once_with(with_re=True, debian_mirror=None)
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
