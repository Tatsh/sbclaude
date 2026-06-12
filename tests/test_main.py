"""Tests for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sbclaude.config import Config
from sbclaude.main import main

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


def test_run_config_default_flags(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(default_flags=['--x11']))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_x11


def test_run_ios_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '--ios'])
    assert run.call_args[0][0].use_ios


def test_run_ios_config_default_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config(default_flags=['--ios']))
    runner.invoke(main, ['run'])
    assert run.call_args[0][0].use_ios


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


def test_run_env_flag(runner: CliRunner, mocker: MockerFixture) -> None:
    run = mocker.patch('sbclaude.main.container.run', return_value=0)
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['run', '-e', 'CLAUDE_CODE_USE_BEDROCK=1', '-e', 'AWS_REGION=us-east-1'])
    assert run.call_args[0][0].env == {'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_REGION': 'us-east-1'}


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
    assert 'line1' in runner.invoke(main, ['build', '--no-re']).output


def test_build_debian_mirror(runner: CliRunner, mocker: MockerFixture) -> None:
    build = mocker.patch('sbclaude.main.container.build_images', return_value=iter([]))
    mocker.patch('sbclaude.main.load_config', return_value=Config())
    runner.invoke(main, ['build', '--no-re', '--debian-mirror', 'http://ftp.us.debian.org/debian'])
    assert build.call_args.kwargs['debian_mirror'] == 'http://ftp.us.debian.org/debian'


def test_config_cmd(runner: CliRunner) -> None:
    assert 'config.toml' in runner.invoke(main, ['config']).output


def test_delete_image(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.delete_images', return_value=['sbclaude-re:latest'])
    assert 'Deleted' in runner.invoke(main, ['delete-image']).output


def test_delete_image_none(runner: CliRunner, mocker: MockerFixture) -> None:
    mocker.patch('sbclaude.main.container.delete_images', return_value=[])
    assert 'No sbclaude images' in runner.invoke(main, ['delete-image']).output
