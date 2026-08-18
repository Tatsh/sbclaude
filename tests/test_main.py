"""Tests for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sbclaude import container
from sbclaude.config import Config
from sbclaude.main import main
from sbclaude.scaffold import ScaffoldResult
import pytest

if TYPE_CHECKING:
    from click.testing import CliRunner
    from pytest_mock import MockerFixture


def test_run_default_invokes_container(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    assert runner.invoke(main, []).exit_code == 0
    assert run.called


def test_run_flags_and_passthrough(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--re', '--', '--version'])
    spec = run.call_args[0][0]
    assert spec.use_re
    assert spec.use_ghidra
    assert spec.use_android
    assert spec.claude_args == ('--version',)


def test_run_config_x11(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(x11=True))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_x11


def test_run_re_from_config_implies_ghidra_android(runner: CliRunner,
                                                   mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(re=True))
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.use_re
    assert spec.use_ghidra
    assert spec.use_android


def test_run_ghidra_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(ghidra=True))
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.use_ghidra
    assert not spec.use_re
    assert not spec.use_android


def test_run_android_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(android=True))
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.use_android
    assert not spec.use_re
    assert not spec.use_ghidra


def test_run_usb_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(usb=True))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_usb


def test_run_usb_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--usb'])
    assert run.call_args[0][0].use_usb


def test_run_ghidra_android_flags(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--ghidra', '--android'])
    spec = run.call_args[0][0]
    assert spec.use_ghidra
    assert spec.use_android
    assert not spec.use_re


def test_run_ios_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--ios'])
    assert run.call_args[0][0].use_ios


def test_run_ios_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(ios=True))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_ios


def test_run_wayland_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--wayland'])
    assert run.call_args[0][0].use_wayland


def test_run_wayland_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(wayland=True))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_wayland


def test_run_wayland_off_by_default(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_wayland is False


def test_run_ssh_gpg_flags(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--ssh', '--gpg'])
    spec = run.call_args[0][0]
    assert spec.use_ssh
    assert spec.use_gpg


def test_run_ssh_gpg_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(ssh=True, gpg=True))
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.use_ssh
    assert spec.use_gpg


def test_run_debian_mirror_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--debian-mirror', 'http://m/debian'])
    assert run.call_args[0][0].debian_mirror == 'http://m/debian'


def test_run_debian_mirror_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config',
                 return_value=Config(debian_mirror='http://cfg/debian'))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].debian_mirror == 'http://cfg/debian'


def test_run_manages_uv_env_by_default(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.env['UV_PROJECT_ENVIRONMENT'] == container.VENV_DIR_NAME


def test_run_uv_env_override_wins(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config',
                 return_value=Config(env={'UV_PROJECT_ENVIRONMENT': '/custom/env'}))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].env['UV_PROJECT_ENVIRONMENT'] == '/custom/env'


def test_run_manage_uv_env_disabled(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(manage_uv_env=False))
    runner.invoke(main, ['run'])
    env = run.call_args[0][0].env
    assert 'UV_PROJECT_ENVIRONMENT' not in env
    assert 'SBCLAUDE_VENV' not in env


def test_run_points_the_venv_variables_away_from_dot_venv(runner: CliRunner,
                                                          mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.env['SBCLAUDE_VENV'] == container.uv_project_environment(spec.project)
    assert not spec.env['SBCLAUDE_VENV'].endswith('/.venv')
    assert spec.env['UV_PROJECT_ENVIRONMENT'] != '.venv'


def test_run_uv_project_environment_is_relative(runner: CliRunner, mocker: MockerFixture) -> None:
    # Relative so uv resolves it per project; an absolute path would send a sync run in a second
    # project into the first project's environment.
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run'])
    value = run.call_args[0][0].env['UV_PROJECT_ENVIRONMENT']
    assert not Path(value).is_absolute()
    assert value == container.VENV_DIR_NAME


def test_run_excludes_the_box_venv_from_git(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    exclude = mocker.patch('sbclaude.main.container.exclude_venv_from_git')
    runner.invoke(main, ['run'])
    exclude.assert_called_once()


@pytest.mark.parametrize(('setup_venv', 'expected'), [(True, '1'), (False, '0')])
def test_run_forwards_setup_venv(runner: CliRunner, mocker: MockerFixture, expected: str, *,
                                 setup_venv: bool) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(setup_venv=setup_venv))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].env['SBCLAUDE_SETUP_VENV'] == expected


def test_run_uv_env_override_by_e_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '-e', 'UV_PROJECT_ENVIRONMENT=/from/flag'])
    assert run.call_args[0][0].env['UV_PROJECT_ENVIRONMENT'] == '/from/flag'


def test_run_uv_env_override_by_pass_env(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config',
                 return_value=Config(pass_env=['UV_PROJECT_ENVIRONMENT']))
    mocker.patch.dict('os.environ', {'UV_PROJECT_ENVIRONMENT': '/from/host'})
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].env['UV_PROJECT_ENVIRONMENT'] == '/from/host'


def test_run_memory_cpus_flags(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--memory', '8g', '--cpus', '4'])
    spec = run.call_args[0][0]
    assert spec.memory == '8g'
    assert spec.cpus == '4'


def test_run_memory_cpus_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(memory='4g', cpus='2'))
    runner.invoke(main, ['run'])
    spec = run.call_args[0][0]
    assert spec.memory == '4g'
    assert spec.cpus == '2'


def test_run_forwards_aws_profile_by_default(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    mocker.patch.dict('os.environ', {'AWS_PROFILE': 'work'})
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].env.get('AWS_PROFILE') == 'work'


def test_run_recover_off_by_default(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].recover is False


def test_run_session_recover_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--session-recover'])
    assert run.call_args[0][0].recover is True


def test_run_recover_from_config(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(recover=True))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].recover is True


def test_run_env_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '-e', 'CLAUDE_CODE_USE_BEDROCK=1', '-e', 'AWS_REGION=us-east-1'])
    env = run.call_args[0][0].env
    assert env['CLAUDE_CODE_USE_BEDROCK'] == '1'
    assert env['AWS_REGION'] == 'us-east-1'


def test_run_env_bad(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    assert runner.invoke(main, ['run', '-e', 'invalid']).exit_code != 0


def test_run_pass_env(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(pass_env=['SBCLAUDE_TEST_PASS']))
    mocker.patch.dict('os.environ', {'SBCLAUDE_TEST_PASS': 'xyz'})
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].env.get('SBCLAUDE_TEST_PASS') == 'xyz'


def test_run_harden_default_on(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].harden is True


def test_run_no_harden(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--no-harden'])
    assert run.call_args[0][0].harden is False


def test_run_claude_missing(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    mocker.patch('sbclaude.main.container.run', side_effect=FileNotFoundError('no claude'))
    assert runner.invoke(main, ['run']).exit_code != 0


def test_ls_empty(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.list_managed', return_value=[])
    assert 'No running' in runner.invoke(main, ['ls']).output


def test_ls(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.list_managed',
                 return_value=[{
                     'name': 'sbclaude-x',
                     'status': 'running',
                     'image': 'sbclaude:latest',
                     'project': '/p'
                 }])
    assert 'sbclaude-x' in runner.invoke(main, ['ls']).output


def test_stop_named(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.stop', return_value=['n'])
    assert 'Stopped: n' in runner.invoke(main, ['stop', '-n', 'n']).output


def test_stop_all(runner: CliRunner, mocker: MockerFixture) -> None:
    stop = mocker.patch('sbclaude.main.container.stop', return_value=[])
    assert 'No matching' in runner.invoke(main, ['stop', '--all']).output
    stop.assert_called_once_with(None)


def test_stop_default_project(runner: CliRunner, mocker: MockerFixture) -> None:
    stop = mocker.patch('sbclaude.main.container.stop', return_value=['sbclaude-p-abc123'])
    assert 'Stopped: sbclaude-p-abc123' in runner.invoke(main, ['stop']).output
    stop.assert_called_once_with(project=Path.cwd().resolve())


def test_shell(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.shell', return_value=0)
    assert runner.invoke(main, ['shell', '-n', 'n']).exit_code == 0


def test_shell_default_single(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.project_containers', return_value=['sbclaude-p-abc123'])
    shell = mocker.patch('sbclaude.main.container.shell', return_value=0)
    assert runner.invoke(main, ['shell']).exit_code == 0
    shell.assert_called_once_with('sbclaude-p-abc123')


def test_shell_default_none(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.project_containers', return_value=[])
    assert runner.invoke(main, ['shell']).exit_code != 0


def test_shell_default_multiple(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.project_containers', return_value=['a', 'b'])
    result = runner.invoke(main, ['shell'])
    assert result.exit_code != 0
    assert 'pass -n' in result.output


def test_build(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    mocker.patch('sbclaude.main.container.build_images', return_value=iter(['line1', 'line2']))
    assert 'line1' in runner.invoke(main, ['build']).output


def test_build_debian_mirror(runner: CliRunner, mocker: MockerFixture) -> None:
    build = mocker.patch('sbclaude.main.container.build_images', return_value=iter([]))
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['build', '--debian-mirror', 'http://ftp.us.debian.org/debian'])
    assert build.call_args.kwargs['debian_mirror'] == 'http://ftp.us.debian.org/debian'


def test_config_cmd(runner: CliRunner) -> None:
    assert 'config.toml' in runner.invoke(main, ['config']).output


def test_delete_image(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.delete_images', return_value=['sbclaude:latest'])
    assert 'Deleted' in runner.invoke(main, ['delete-image']).output


def test_delete_image_none(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.delete_images', return_value=[])
    assert 'No sbclaude images' in runner.invoke(main, ['delete-image']).output


def test_scaffold_noclip(runner: CliRunner, mocker: MockerFixture, tmp_path: Path) -> None:
    written = [tmp_path / 'viewer-tests' / 'nc.mjs', tmp_path / 'NEW_GAME.md']
    scaffold = mocker.patch('sbclaude.main.scaffold', return_value=ScaffoldResult(written=written))
    result = runner.invoke(main, ['scaffold-noclip', str(tmp_path)])
    assert result.exit_code == 0
    assert scaffold.call_args[0] == (tmp_path, 'noclip')
    assert scaffold.call_args[1] == {'force': False, 'scene': None}
    assert f'created {tmp_path / "viewer-tests" / "nc.mjs"}' in result.output
    assert 'Next: start your dev server' in result.output


def test_scaffold_noclip_skips_existing(runner: CliRunner, mocker: MockerFixture,
                                        tmp_path: Path) -> None:
    skipped = [tmp_path / 'viewer-tests' / 'views.json']
    mocker.patch('sbclaude.main.scaffold', return_value=ScaffoldResult(skipped=skipped))
    result = runner.invoke(main, ['scaffold-noclip', str(tmp_path)])
    assert result.exit_code == 0
    assert f'exists, left alone: {skipped[0]}' in result.output
    assert 'Re-run with --force' in result.output
    # Nothing was written, so the next-step hint would be misleading.
    assert 'Next: start your dev server' not in result.output


def test_scaffold_noclip_scene_and_force(runner: CliRunner, mocker: MockerFixture,
                                         tmp_path: Path) -> None:
    scaffold = mocker.patch('sbclaude.main.scaffold', return_value=ScaffoldResult())
    runner.invoke(main, ['scaffold-noclip', str(tmp_path), '--scene', 'MyGame/Level1', '--force'])
    assert scaffold.call_args[1] == {'force': True, 'scene': 'MyGame/Level1'}


def test_scaffold_noclip_defaults_to_cwd(runner: CliRunner, mocker: MockerFixture) -> None:
    scaffold = mocker.patch('sbclaude.main.scaffold', return_value=ScaffoldResult())
    runner.invoke(main, ['scaffold-noclip'])
    assert scaffold.call_args[0][0] == Path.cwd()


def test_scaffold_noclip_os_error(runner: CliRunner, mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch('sbclaude.main.scaffold', side_effect=OSError('read-only file system'))
    result = runner.invoke(main, ['scaffold-noclip', str(tmp_path)])
    assert result.exit_code != 0
    assert 'read-only file system' in result.output
    assert not isinstance(result.exception, OSError)
