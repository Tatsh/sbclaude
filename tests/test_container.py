"""Tests for docker orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import json
import os
import subprocess as sp
import tempfile

import docker.errors
import pytest

from sbclaude import container

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_default_name() -> None:
    assert container.default_name(Path('/a/b/My Proj')) == 'sbclaude-My-Proj'


def test_unique_name() -> None:
    name = container.unique_name(Path('/a/b/My Proj'))
    assert name.startswith('sbclaude-My-Proj-')
    assert name != container.unique_name(Path('/a/b/My Proj'))


def test_uv_project_environment() -> None:
    assert container.uv_project_environment(Path('/a/b/My Proj')) == '/a/b/My Proj/.sbclaude-venv'


def test_uv_project_environment_is_never_the_host_venv() -> None:
    project = Path('/a/b/proj')
    assert container.uv_project_environment(project) != str(project / '.venv')


def test_transient_uv_project_environment_is_outside_the_project() -> None:
    project = Path('/a/b/My Proj')
    value = container.transient_uv_project_environment(project)
    assert Path(value).is_absolute()
    assert not value.startswith(str(project))
    assert value.startswith('/tmp/sbclaude-venv-My-Proj-')  # noqa: S108


def test_uv_project_environment_in_is_under_the_given_directory() -> None:
    value = container.uv_project_environment_in('/venv-cache/', Path('/a/b/My Proj'))
    assert value.startswith('/venv-cache/sbclaude-venv-My-Proj-')
    assert Path(value).parent == Path('/venv-cache')


def test_uv_project_environment_in_separates_same_named_projects() -> None:
    directory = '/venv-cache'
    assert (container.uv_project_environment_in(directory, Path('/a/foo'))
            != container.uv_project_environment_in(directory, Path('/b/foo')))


@pytest.mark.parametrize('marker', ['pyproject.toml', 'setup.py', 'requirements.txt'])
def test_is_python_project_by_marker(marker: str, tmp_path: Path) -> None:
    (tmp_path / marker).write_text('')
    assert container.is_python_project(tmp_path) is True


def test_is_python_project_by_nested_source_file(tmp_path: Path) -> None:
    (tmp_path / 'src' / 'pkg').mkdir(parents=True)
    (tmp_path / 'src' / 'pkg' / 'thing.py').write_text('')
    assert container.is_python_project(tmp_path) is True


def test_is_python_project_without_python(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'index.ts').write_text('')
    assert container.is_python_project(tmp_path) is False


def test_is_python_project_ignores_dependency_trees(tmp_path: Path) -> None:
    for directory in ('node_modules', '.git'):
        (tmp_path / directory / 'dep').mkdir(parents=True)
        (tmp_path / directory / 'dep' / 'thing.py').write_text('')
    assert container.is_python_project(tmp_path) is False


def test_host_venv_mounted_read_only(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.claude_binary', return_value=Path('/usr/bin/claude'))
    (tmp_path / '.venv' / 'bin').mkdir(parents=True)
    argv, _ = container.build_run_argv(container.RunSpec(name='n', project=tmp_path))
    assert f'{tmp_path}/.venv:{tmp_path}/.venv:ro' in argv


def test_host_venv_not_mounted_when_absent(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.claude_binary', return_value=Path('/usr/bin/claude'))
    argv, _ = container.build_run_argv(container.RunSpec(name='n', project=tmp_path))
    assert not any('.venv' in arg for arg in argv)


def test_exclude_venv_from_git_adds_entry(tmp_path: Path) -> None:
    (tmp_path / '.git').mkdir()
    assert container.exclude_venv_from_git(tmp_path) is True
    assert '/.sbclaude-venv/' in (tmp_path / '.gitignore').read_text().splitlines()


def test_exclude_venv_from_git_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / '.git').mkdir()
    assert container.exclude_venv_from_git(tmp_path) is True
    assert container.exclude_venv_from_git(tmp_path) is False
    text = (tmp_path / '.gitignore').read_text()
    assert text.count('/.sbclaude-venv/') == 1


def test_exclude_venv_from_git_keeps_existing_entries(tmp_path: Path) -> None:
    (tmp_path / '.git').mkdir()
    (tmp_path / '.gitignore').write_text('# a comment\n/scratch/\n')
    assert container.exclude_venv_from_git(tmp_path) is True
    lines = (tmp_path / '.gitignore').read_text().splitlines()
    assert lines[:2] == ['# a comment', '/scratch/']
    assert '/.sbclaude-venv/' in lines


def test_exclude_venv_from_git_without_a_repository(tmp_path: Path) -> None:
    assert container.exclude_venv_from_git(tmp_path) is False


def test_exclude_venv_from_git_unreadable(tmp_path: Path, mocker: MockerFixture) -> None:
    (tmp_path / '.git').mkdir()
    (tmp_path / '.gitignore').write_text('node_modules/\n')
    mocker.patch('sbclaude.container.Path.read_text', side_effect=OSError('denied'))
    assert container.exclude_venv_from_git(tmp_path) is False


def test_exclude_venv_from_git_unwritable(tmp_path: Path, mocker: MockerFixture) -> None:
    (tmp_path / '.git').mkdir()
    mocker.patch('sbclaude.container.Path.open', side_effect=OSError('read-only file system'))
    assert container.exclude_venv_from_git(tmp_path) is False


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


def test_build_run_argv_re_uses_base_image(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'proj'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_re=True))
    assert argv[-1] == container.IMAGE_BASE


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
    assert data['tui'] == 'fullscreen'
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


def test_build_run_argv_gitconfig_includes(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    gitconfig = tmp_path / '.gitconfig'
    gitconfig.write_text('[include]\n')
    included = tmp_path / 'work.gitconfig'
    included.write_text('[user]\n')
    out = (f'file:{gitconfig}\tinclude.path={included}\n'
           f'file:{included}\tuser.email=work@example.com\n')
    mocker.patch('sbclaude.container.sp.run', return_value=mocker.MagicMock(stdout=out))
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert f'{gitconfig}:{gitconfig}:ro' in argv
    assert f'{included}:{included}:ro' in argv


@pytest.mark.parametrize('exc', [FileNotFoundError(), sp.CalledProcessError(128, 'git')])
def test_build_run_argv_gitconfig_fallback(exc: Exception, mocker: MockerFixture,
                                           tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    gitconfig = tmp_path / '.gitconfig'
    gitconfig.write_text('[user]\n')
    mocker.patch('sbclaude.container.sp.run', side_effect=exc)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert f'{gitconfig}:{gitconfig}:ro' in argv


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


def test_shell_invokes_docker_as_mapped_user(mocker: MockerFixture) -> None:
    ctr = mocker.MagicMock()
    ctr.attrs = {
        'Config': {
            'Env': ['HOST_UID=1234', 'HOST_USER=someone', 'HOST_HOME=/home/someone', 'NOT_A_PAIR']
        }
    }
    client = mocker.MagicMock()
    client.containers.get.return_value = ctr
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 0
    assert container.shell('n') == 0
    # No -w: the shell starts in the container's own working directory, the project.
    assert completed.call_args[0][0] == [
        'docker', 'exec', '-it', '-u', '1234', '-e', 'HOME=/home/someone', '-e', 'USER=someone',
        '-e', 'LOGNAME=someone', 'n', 'bash', '-l'
    ]


def test_shell_falls_back_to_host_identity(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.docker.from_env',
                 side_effect=docker.errors.DockerException('no daemon'))
    mocker.patch('sbclaude.container.os.getuid', return_value=4321)
    mocker.patch('sbclaude.container.getpass.getuser', return_value='hostuser')
    mocker.patch('sbclaude.container.Path.home', return_value=Path('/home/hostuser'))
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 0
    assert container.shell('n') == 0
    argv = completed.call_args[0][0]
    assert argv[argv.index('-u') + 1] == '4321'
    assert 'HOME=/home/hostuser' in argv
    assert 'USER=hostuser' in argv


def test_shell_root(mocker: MockerFixture) -> None:
    from_env = mocker.patch('sbclaude.container.docker.from_env')
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 0
    assert container.shell('n', root=True) == 0
    assert completed.call_args[0][0] == ['docker', 'exec', '-it', 'n', 'bash', '-l']
    assert not from_env.called


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
    client.api.build.return_value = [{
        'stream': 'Step 1/2\n'
    }, {
        'stream': '  '
    }, {
        'stream': 'Successfully built\n'
    }]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    lines = list(container.build_images())
    assert any('Building sbclaude:latest' in line for line in lines)
    assert 'Step 1/2' in lines


def test_build_images_raises_on_error(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'Step 1/2\n'}, {'error': 'boom'}, {'stream': 'x\n'}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    lines: list[str] = []
    # extend appends as it iterates, so what was yielded before the raise is kept.
    with pytest.raises(docker.errors.BuildError):
        lines.extend(container.build_images())
    # The failing step is reported before the generator gives up, and nothing after it is run.
    assert 'boom' in lines
    assert 'x' not in lines


def test_build_images_debian_mirror(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'ok\n'}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    list(container.build_images(debian_mirror='http://ftp.us.debian.org/debian/'))
    # Trailing slash stripped, and the single build receives the mirror argument.
    assert client.api.build.call_args.kwargs['dockerfile'] == 'Dockerfile'
    assert client.api.build.call_args.kwargs['buildargs'] == {
        'DEBIAN_MIRROR': 'http://ftp.us.debian.org/debian'
    }


def test_build_images_no_mirror_passes_none(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.api.build.return_value = [{'stream': 'ok\n'}]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    list(container.build_images())
    assert client.api.build.call_args.kwargs['buildargs'] is None


def test_ensure_image_passes_debian_mirror(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=False)
    build = mocker.patch('sbclaude.container.build_images', return_value=iter(['built']))
    container.ensure_image(container.IMAGE_BASE, debian_mirror='http://m/debian')
    build.assert_called_once_with(debian_mirror='http://m/debian')


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


def test_build_run_argv_android_sdk_absent(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.ANDROID_SDK_UPDATE_MANAGER_DIR', tmp_path / 'absent-manager')
    mocker.patch('sbclaude.container.KVM_DEVICE', tmp_path / 'absent-kvm')
    mocker.patch.dict(os.environ, {'ANDROID_HOME': str(tmp_path / 'absent-sdk')})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_android=True))
    assert not any('absent' in arg for arg in argv)


def test_build_run_argv_android_update_manager_and_kvm(mocker: MockerFixture,
                                                       tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    manager = tmp_path / 'sdk-update-manager'
    manager.mkdir()
    kvm = tmp_path / 'kvm'
    kvm.write_text('')
    mocker.patch('sbclaude.container.ANDROID_SDK_UPDATE_MANAGER_DIR', manager)
    mocker.patch('sbclaude.container.KVM_DEVICE', kvm)
    mocker.patch.dict(os.environ, {'ANDROID_HOME': str(tmp_path / 'absent-sdk')})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_android=True))
    assert f'{manager}:{manager}:ro' in argv
    assert argv[argv.index('--device') + 1] == str(kvm)
    assert argv[argv.index('--group-add') + 1] == str(kvm.stat().st_gid)


def test_build_run_argv_pentoo_tools(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    jadx = tmp_path / 'jadx-bin'
    jadx.mkdir()
    mocker.patch('sbclaude.container.PENTOO_TOOL_DIRS', (jadx, tmp_path / 'absent-apktool'))
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_re=True))
    assert f'{jadx}:{jadx}:ro' in argv
    assert not any('absent' in arg for arg in argv)


def test_build_run_argv_ghidra(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    ghidra = tmp_path / 'ghidra'
    ghidra.mkdir()
    mocker.patch('sbclaude.container.GHIDRA_DIR', ghidra)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n',
                                                         use_ghidra=True))
    assert f'{ghidra}:{ghidra}:ro' in argv


def test_build_run_argv_ghidra_absent(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.GHIDRA_DIR', tmp_path / 'absent-ghidra')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n',
                                                         use_ghidra=True))
    assert not any('absent-ghidra' in arg for arg in argv)


def test_build_run_argv_usb(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    usb = tmp_path / 'usb'
    usb.mkdir()
    mocker.patch('sbclaude.container.USB_DEVICE_DIR', usb)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_usb=True))
    assert f'{usb}:{usb}' in argv


def test_build_run_argv_usb_absent(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.USB_DEVICE_DIR', tmp_path / 'absent-usb')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_usb=True))
    assert not any('absent-usb' in arg for arg in argv)


def test_build_run_argv_gpu(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    dev = tmp_path / 'nvidia0'
    dev.write_text('')
    mocker.patch('sbclaude.container.NVIDIA_DEVICES', (dev, tmp_path / 'absent-nvidiactl'))
    mocker.patch('sbclaude.container.DRI_DEVICE_DIR', tmp_path / 'absent-dri')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpu=True))
    assert argv[argv.index('--gpus') + 1] == 'all'
    # `graphics` is required for the GL/EGL/Vulkan userspace, not just CUDA.
    assert 'NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics' in argv
    assert argv[argv.index('--group-add') + 1] == str(dev.stat().st_gid)


def test_build_run_argv_gpu_x11_adds_display_cap(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.NVIDIA_DEVICES', ())
    mocker.patch('sbclaude.container.DRI_DEVICE_DIR', tmp_path / 'absent-dri')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_gpu=True, use_x11=True))
    assert 'NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display' in argv


def test_build_run_argv_gpu_passes_dri_render_nodes(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.NVIDIA_DEVICES', ())
    dri = tmp_path / 'dri'
    dri.mkdir()
    (node := dri / 'renderD128').write_text('')
    (dri / 'card0').write_text('')  # Not a render node; must not be passed through.
    mocker.patch('sbclaude.container.DRI_DEVICE_DIR', dri)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpu=True))
    assert argv[argv.index('--device') + 1] == str(node)
    assert str(dri / 'card0') not in argv


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


def test_build_run_argv_ssh_symlink_target(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': ''})
    ssh = tmp_path / '.ssh'
    (ssh / 'nested').mkdir(parents=True)
    dotfiles = tmp_path / 'dotfiles'
    dotfiles.mkdir()
    (config := dotfiles / 'config').write_text('Host x\n')
    (key := dotfiles / 'id_ed25519').write_text('key\n')
    (ssh / 'config').symlink_to(config)
    (ssh / 'nested' / 'id_ed25519').symlink_to(key)
    (ssh / 'known_hosts').write_text('host\n')
    (ssh / 'dangling').symlink_to(tmp_path / 'absent')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    assert f'{config}:{config}:ro' in argv
    assert f'{key}:{key}:ro' in argv
    # A link with no target on the host cannot be mounted.
    assert not any('absent' in arg for arg in argv)


def test_build_run_argv_ssh_internal_symlink(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': ''})
    ssh = tmp_path / '.ssh'
    ssh.mkdir()
    (real := ssh / 'id_rsa').write_text('key\n')
    (ssh / 'id_default').symlink_to(real)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    # The target is already inside the mounted ~/.ssh, so no separate mount is added.
    assert argv.count(f'{real}:{real}:ro') == 0


def test_build_run_argv_ssh_symlink_dir(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': ''})
    ssh = tmp_path / '.ssh'
    ssh.mkdir()
    keys = tmp_path / 'dotfiles' / 'keys'
    keys.mkdir(parents=True)
    vault = tmp_path / 'vault'
    vault.mkdir()
    (nested := vault / 'id_ed25519').write_text('key\n')
    (keys / 'id_ed25519').symlink_to(nested)
    (ssh / 'keys').symlink_to(keys)
    # A link back to the directory being walked resolves inside ~/.ssh, so it needs no mount.
    (ssh / 'loop').symlink_to(ssh)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    # A symlinked directory is mounted whole and not descended into, so a link inside it
    # gets no mount of its own.
    assert f'{keys}:{keys}:ro' in argv
    assert not any(str(nested) in arg for arg in argv)
    assert not any('loop' in arg for arg in argv)
    assert argv.count(f'{ssh}:{ssh}:ro') == 1


def test_build_run_argv_ssh_symlinks_share_target(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': ''})
    ssh = tmp_path / '.ssh'
    (ssh / 'nested').mkdir(parents=True)
    (dotfiles := tmp_path / 'dotfiles').mkdir()
    (config := dotfiles / 'config').write_text('Host x\n')
    (ssh / 'config').symlink_to(config)
    (ssh / 'nested' / 'config').symlink_to(config)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    # Docker rejects a repeated destination, so a shared target is mounted only once.
    assert argv.count(f'{config}:{config}:ro') == 1


def test_build_run_argv_ssh_agent(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    sock = tmp_path / 'run' / 'agent.sock'
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': str(sock)})
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    ssh = tmp_path / '.ssh'
    ssh.mkdir()
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    # Mounted read-write: connecting to a socket needs write permission on it.
    assert f'{sock}:{sock}' in argv
    assert f'SSH_AUTH_SOCK={sock}' in argv


def test_build_run_argv_ssh_agent_no_ssh_dir(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    sock = tmp_path / 'run' / 'agent.sock'
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': str(sock)})
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    assert f'SSH_AUTH_SOCK={sock}' in argv


@pytest.mark.parametrize('value', ['', 'absent'])
def test_build_run_argv_ssh_agent_unusable(mocker: MockerFixture, tmp_path: Path,
                                           value: str) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'SSH_AUTH_SOCK': str(tmp_path / value) if value else ''})
    ssh = tmp_path / '.ssh'
    ssh.mkdir()
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_ssh=True))
    assert not any('SSH_AUTH_SOCK' in arg for arg in argv)


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


def test_build_run_argv_gpg_bridges_the_runtime_socket_too(mocker: MockerFixture,
                                                           tmp_path: Path) -> None:
    # Where the box's gpg looks depends on whether /run/user/<uid> is usable there, which
    # --wayland and --ssh both arrange, so the socket has to be bridged at both paths.
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'GNUPGHOME': ''})
    (tmp_path / '.gnupg').mkdir()
    sock = tmp_path / 'run' / 'S.gpg-agent'
    mocker.patch('sbclaude.container.sp.run', return_value=mocker.MagicMock(stdout=f'{sock}\n'))
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpg=True))
    assert f'{sock}:/run/user/{os.getuid()}/gnupg/S.gpg-agent' in argv


def test_build_run_argv_gpg_socket_already_at_the_runtime_path(mocker: MockerFixture,
                                                               tmp_path: Path) -> None:
    # The usual host layout: the socket already sits where the box will look. That still has
    # to be mounted, because the matching paths name different files -- /run/user/<uid> in the
    # box is its own tmpfs -- and leaving it out is what makes the box start its own agent.
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'GNUPGHOME': ''})
    gnupg = tmp_path / '.gnupg'
    gnupg.mkdir()
    sock = Path(f'/run/user/{os.getuid()}/gnupg/S.gpg-agent')
    mocker.patch('sbclaude.container.sp.run', return_value=mocker.MagicMock(stdout=f'{sock}\n'))
    mocker.patch('sbclaude.container.Path.is_socket', return_value=True)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_gpg=True))
    assert f'{sock}:{gnupg / "S.gpg-agent"}' in argv
    assert f'{sock}:{sock}' in argv


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


def test_build_run_argv_wayland(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.os.getuid', return_value=1234)
    runtime = tmp_path / 'run'
    runtime.mkdir()
    (runtime / 'wayland-1').write_text('')
    mocker.patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(runtime), 'WAYLAND_DISPLAY': 'wayland-1'})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_wayland=True))
    assert 'WAYLAND_DISPLAY=wayland-1' in argv
    assert 'XDG_RUNTIME_DIR=/run/user/1234' in argv
    # Read-write: a Wayland client must be able to write to the socket.
    assert f'{runtime / "wayland-1"}:/run/user/1234/wayland-1' in argv


def test_build_run_argv_wayland_absolute_display(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.os.getuid', return_value=1234)
    sock = tmp_path / 'elsewhere' / 'wayland-9'
    sock.parent.mkdir()
    sock.write_text('')
    mocker.patch.dict(os.environ, {'WAYLAND_DISPLAY': str(sock)})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_wayland=True))
    assert 'WAYLAND_DISPLAY=wayland-9' in argv
    assert f'{sock}:/run/user/1234/wayland-9' in argv


def test_build_run_argv_wayland_missing_socket(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(tmp_path), 'WAYLAND_DISPLAY': 'absent-0'})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', use_wayland=True))
    assert not any(a.startswith('WAYLAND_DISPLAY=') for a in argv)


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
    build.assert_called_once_with(debian_mirror=None)
    assert 'built' in logged


def test_ensure_image_rebuilds_when_stale(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=True)
    build = mocker.patch('sbclaude.container.build_images', return_value=iter(['built']))
    logged: list[str] = []
    container.ensure_image(container.IMAGE_BASE, log=logged.append)
    build.assert_called_once_with(debian_mirror=None)
    assert any('rebuilding' in line for line in logged)


def test_ensure_image_failed_first_build_is_fatal(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=False)
    mocker.patch('sbclaude.container.build_images',
                 side_effect=docker.errors.BuildError('step 11 failed', iter(())))
    with pytest.raises(docker.errors.BuildError):
        container.ensure_image(container.IMAGE_BASE)


def test_ensure_image_failed_rebuild_warns_and_continues(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=False)
    mocker.patch('sbclaude.container.image_exists', return_value=True)
    mocker.patch('sbclaude.container.build_images',
                 side_effect=docker.errors.BuildError('step 11 failed', iter(())))
    logged: list[str] = []
    container.ensure_image(container.IMAGE_BASE, log=logged.append)
    assert any('FAILED' in line for line in logged)
    assert any('stale' in line for line in logged)


def test_ensure_image_skips_when_current(mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.container.image_up_to_date', return_value=True)
    build = mocker.patch('sbclaude.container.build_images')
    container.ensure_image(container.IMAGE_BASE)
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
    client.images.remove.return_value = None
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.delete_images() == [container.IMAGE_BASE]


def test_delete_images_absent(mocker: MockerFixture) -> None:
    client = mocker.MagicMock()
    client.images.remove.side_effect = docker.errors.ImageNotFound('x')
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert container.delete_images() == []


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
    cap_adds = {argv[i + 1] for i, arg in enumerate(argv) if arg == '--cap-add'}
    assert 'KILL' in cap_adds


def test_build_run_argv_no_harden(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', harden=False))
    assert 'no-new-privileges' not in argv
    assert '--cap-drop' not in argv


def test_build_run_argv_sudo(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_sudo=True))
    # sudo's setuid bit is inert under no_new_privs, so the two are mutually exclusive.
    assert 'no-new-privileges' not in argv
    assert 'SBCLAUDE_SUDO=1' in argv
    # The rest of the hardening stays on.
    assert '--cap-drop' in argv
    assert '--pids-limit' in argv


def test_build_run_argv_no_sudo_by_default(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert 'SBCLAUDE_SUDO=1' not in argv


def test_build_run_argv_memory_explicit(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', memory='8g'))
    assert argv[argv.index('--memory') + 1] == '8g'
    assert argv[argv.index('--memory-swap') + 1] == '8g'


def test_build_run_argv_memory_disabled(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', memory='0'))
    assert '--memory' not in argv


def test_build_run_argv_memory_auto_from_host(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    # 8 GiB host: reserve max(2 GiB, 1 GiB) = 2 GiB, so the cap is 6 GiB.
    mocker.patch('sbclaude.container.os.sysconf',
                 side_effect=lambda name: {
                     'SC_PAGE_SIZE': 4096,
                     'SC_PHYS_PAGES': 2097152
                 }[name])
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert argv[argv.index('--memory') + 1] == f'{6 * 1024**3}b'


def test_build_run_argv_cpus(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', cpus='4'))
    assert argv[argv.index('--cpus') + 1] == '4'


def test_build_run_argv_resource_limits_need_harden(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', memory='8g', cpus='4', harden=False))
    assert '--memory' not in argv
    assert '--cpus' not in argv


def test_build_run_argv_recover(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', recover=True))
    assert 'SBCLAUDE_RECOVER=1' in argv


def test_build_run_argv_no_recover_by_default(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert 'SBCLAUDE_RECOVER=1' not in argv


def test_build_run_argv_extra_args(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project,
                          name='n',
                          extra_args=['--read-only', '--shm-size', '256m']))
    idx = argv.index('--shm-size')
    assert argv[idx + 1] == '256m'
    assert '--read-only' in argv
    assert argv.index(container.IMAGE_BASE) > idx


@pytest.mark.parametrize('exc', [AttributeError(), OSError(), ValueError()])
def test_build_run_argv_memory_auto_unavailable(exc: Exception, mocker: MockerFixture,
                                                tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.os.sysconf', side_effect=exc)
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert '--memory' not in argv


def test_build_run_argv_memory_auto_tiny_host(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    # A 2 GiB host: the whole reserve is taken, leaving nothing to cap with.
    mocker.patch('sbclaude.container.os.sysconf',
                 side_effect=lambda name: {
                     'SC_PAGE_SIZE': 4096,
                     'SC_PHYS_PAGES': 524288
                 }[name])
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert '--memory' not in argv


def test_build_run_argv_legacy_claude_json(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    legacy = tmp_path / '.claude.json'
    legacy.write_text('{}')
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert f'{legacy}:{legacy}' in argv


def test_build_run_argv_mount_extras_skip_project(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    project = tmp_path / 'p'
    project.mkdir()
    rw = tmp_path / 'rw'
    rw.mkdir()
    argv, _ = container.build_run_argv(
        container.RunSpec(project=project, name='n', ro=[project], rw=[rw]))
    # The project stays read-write and is not re-mounted read-only.
    assert f'{project}:{project}:ro' not in argv
    assert argv.count(f'{project}:{project}') == 1
    assert f'{rw}:{rw}' in argv


def test_build_run_argv_gitconfig_dedupes(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    gitconfig = tmp_path / '.gitconfig'
    gitconfig.write_text('[user]\n')
    absent = tmp_path / 'gone.gitconfig'
    out = (f'file:{gitconfig}\tuser.name=A\n'
           f'file:{gitconfig}\tuser.email=a@example.com\n'
           f'file:{absent}\tuser.signingkey=DEADBEEF\n')
    mocker.patch('sbclaude.container.sp.run', return_value=mocker.MagicMock(stdout=out))
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n'))
    assert argv.count(f'{gitconfig}:{gitconfig}:ro') == 1
    assert not any('gone.gitconfig' in arg for arg in argv)


def test_build_run_argv_x11_home_cookie(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    mocker.patch('sbclaude.container.os.getuid', return_value=4242424)
    cookie = tmp_path / '.Xauthority'
    cookie.write_text('x')
    mocker.patch.dict(os.environ, {'XAUTHORITY': '', 'DISPLAY': ':0'})
    project = tmp_path / 'p'
    project.mkdir()
    argv, _ = container.build_run_argv(container.RunSpec(project=project, name='n', use_x11=True))
    assert f'XAUTHORITY={cookie}' in argv


def test_run_custom_image_skips_ensure(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.container.which', return_value='/usr/bin/claude')
    mocker.patch('sbclaude.container.Path.home', return_value=tmp_path)
    ensure = mocker.patch('sbclaude.container.ensure_image')
    completed = mocker.patch('sbclaude.container.sp.run')
    completed.return_value.returncode = 0
    project = tmp_path / 'p'
    project.mkdir()
    assert container.run(container.RunSpec(project=project, name='n', image='custom:latest')) == 0
    assert not ensure.called
    assert completed.call_args[0][0][-1] == 'custom:latest'


def test_stop_all(mocker: MockerFixture) -> None:
    ctr = mocker.MagicMock()
    ctr.name = 'sbclaude-other-abc123'
    client = mocker.MagicMock()
    client.containers.list.return_value = [ctr]
    mocker.patch('sbclaude.container.docker.from_env', return_value=client)
    assert list(container.stop()) == ['sbclaude-other-abc123']
    ctr.remove.assert_called_once_with(force=True)
    assert client.containers.list.call_args.kwargs['filters'] == {'label': 'sbclaude.managed=1'}
