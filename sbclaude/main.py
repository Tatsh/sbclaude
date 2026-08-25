"""CLI entry point for sbclaude."""

from __future__ import annotations

from pathlib import Path
import os

from bascom import setup_logging
import click
import docker.errors

from . import container
from .config import Config, config_path, expand_paths, load_config
from .scaffold import scaffold

__all__ = ('main',)

DEFAULT_PASS_ENV = ('AWS_PROFILE',)
"""Host environment variables forwarded into the box by default when set."""


@click.group(invoke_without_command=True, context_settings={'help_option_names': ('-h', '--help')})
@click.version_option()
@click.pass_context
def main(ctx: click.Context) -> None:
    """
    Run Claude Code in a throwaway Docker container (no sandbox, no prompts).

    With no subcommand, behaves like `sbclaude run` using config defaults.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@main.command(context_settings={'ignore_unknown_options': True})
@click.option('-p',
              '--project',
              type=click.Path(exists=True, file_okay=False, path_type=Path),
              help='Project dir (read-write, becomes the workdir). Default: cwd.')
@click.option('-w', '--rw', 'rw_extra', multiple=True, help='Extra read-write mount.')
@click.option('-r', '--ro', 'ro_extra', multiple=True, help='Extra read-only mount.')
@click.option('-n', '--name', help='Container name (default: sbclaude-<project>).')
@click.option('-i', '--image', help='Image override.')
@click.option('--debian-mirror',
              help='Debian archive mirror to bake in if the image is (re)built, e.g. '
              'http://ftp.us.debian.org/debian.')
@click.option('--memory',
              help="Docker memory limit, e.g. 8g. Default auto-caps from host RAM; '0' "
              'disables. Only applied when hardening is on.')
@click.option('--cpus', help='Docker CPU limit, e.g. 4. Only applied when hardening is on.')
@click.option('-R',
              '--re',
              'use_re',
              is_flag=True,
              help='Enable the Ghidra and Android host mounts together.')
@click.option('--ghidra', 'use_ghidra', is_flag=True, help='Mount host Ghidra (read-only).')
@click.option('--gpu', 'use_gpu', is_flag=True, help='Expose the host GPUs.')
@click.option('--android',
              'use_android',
              is_flag=True,
              help='Mount Android SDK + ~/.android + kvm.')
@click.option('--usb', 'use_usb', is_flag=True, help='Expose /dev/bus/usb for adb.')
@click.option('--ios',
              'use_ios',
              is_flag=True,
              help='Mount the host usbmuxd socket so frida can reach an iOS device.')
@click.option('--wayland',
              'use_wayland',
              is_flag=True,
              help='Forward the Wayland socket for GUIs (preferred over --x11).')
@click.option('--x11', 'use_x11', is_flag=True, help='Forward DISPLAY + XAUTHORITY for GUIs.')
@click.option('--ssh',
              'use_ssh',
              is_flag=True,
              help='Mount the host ~/.ssh read-only and forward the ssh-agent for SSH git '
              'remotes.')
@click.option('--gpg',
              'use_gpg',
              is_flag=True,
              help='Mount the host GnuPG home and agent socket for signing commits.')
@click.option('--sudo',
              'use_sudo',
              is_flag=True,
              help='Allow passwordless sudo in the box. Drops no-new-privileges, without which '
              'no setuid binary can escalate.')
@click.option('--net',
              'network',
              help='Docker network mode (default: host; use "bridge" to isolate).')
@click.option('-e',
              '--env',
              'env_extra',
              multiple=True,
              help='Extra env var KEY=VALUE for the box, e.g. -e CLAUDE_CODE_USE_BEDROCK=1.')
@click.option('--venv-dir',
              help="Directory in the box holding its virtualenv, e.g. a volume's mount point. "
              'Default: .sbclaude-venv beside a Python project.')
@click.option('--no-harden',
              'no_harden',
              is_flag=True,
              help='Disable the container hardening flags (cap-drop, no-new-privileges, ...).')
@click.option('--no-fullscreen',
              'no_fullscreen',
              is_flag=True,
              help="Do not force claude's fullscreen TUI. Use this when a session dies on start "
              'with no visible reason: fullscreen output is erased when it exits.')
@click.option('--no-modify',
              'no_modify',
              is_flag=True,
              help='Stop sbclaude writing anything into the project directory: no .gitignore '
              'entry, no virtualenv beside it, and no session-recover install. Claude itself can '
              'still edit the project.')
@click.option('--session-recover',
              'session_recover',
              is_flag=True,
              help='Install cc-session-recover (auto-resume) into the project on start.')
@click.option('-d', '--debug', is_flag=True, help='Enable debug level logging.')
@click.argument('claude_args', nargs=-1, type=click.UNPROCESSED)
def run(
    project: Path | None,
    rw_extra: tuple[str, ...],
    ro_extra: tuple[str, ...],
    env_extra: tuple[str, ...],
    name: str | None,
    image: str | None,
    network: str | None,
    debian_mirror: str | None,
    memory: str | None,
    cpus: str | None,
    venv_dir: str | None,
    claude_args: tuple[str, ...],
    *,
    use_re: bool,
    use_ghidra: bool,
    use_gpu: bool,
    use_android: bool,
    use_usb: bool,
    use_ios: bool,
    use_wayland: bool,
    use_x11: bool,
    use_ssh: bool,
    use_gpg: bool,
    use_sudo: bool,
    no_fullscreen: bool,
    no_harden: bool,
    no_modify: bool,
    session_recover: bool,
    debug: bool,
) -> None:
    """Launch claude in a fresh container. Args after ``--`` pass through to claude."""  # noqa: DOC501
    setup_logging(debug=debug, loggers={'sbclaude': {}})
    proj = (project or Path.cwd()).resolve()
    cfg = load_config(project=proj)
    use_re = use_re or cfg.re
    use_ghidra = use_ghidra or use_re or cfg.ghidra
    use_gpu = use_gpu or cfg.gpu
    use_android = use_android or use_re or cfg.android
    use_usb = use_usb or cfg.usb
    use_ios = use_ios or cfg.ios
    use_wayland = use_wayland or cfg.wayland
    use_x11 = use_x11 or cfg.x11
    use_ssh = use_ssh or cfg.ssh
    use_gpg = use_gpg or cfg.gpg
    use_sudo = use_sudo or cfg.sudo
    debian_mirror = debian_mirror or cfg.debian_mirror
    memory = memory or cfg.memory
    cpus = cpus or cfg.cpus
    modify = cfg.modify and not no_modify
    session_recover = session_recover or cfg.recover
    if session_recover and not modify:
        click.echo(
            'sbclaude: session recovery installs itself into the project, so it is skipped '
            'while writes to the project are disabled.',
            err=True)
        session_recover = False
    spec = container.RunSpec(project=proj,
                             name=name or container.unique_name(proj),
                             network=network or cfg.network,
                             image=image or cfg.image,
                             use_re=use_re,
                             use_ghidra=use_ghidra,
                             use_gpu=use_gpu,
                             use_android=use_android,
                             use_usb=use_usb,
                             use_ios=use_ios,
                             use_wayland=use_wayland,
                             use_x11=use_x11,
                             use_ssh=use_ssh,
                             use_gpg=use_gpg,
                             use_sudo=use_sudo,
                             ro=[*expand_paths(cfg.ro), *expand_paths(ro_extra)],
                             rw=[*expand_paths(cfg.rw), *expand_paths(rw_extra)],
                             env=_resolve_env(cfg,
                                              proj,
                                              env_extra,
                                              modify=modify,
                                              venv_dir=venv_dir or cfg.venv_dir),
                             fullscreen=cfg.fullscreen and not no_fullscreen,
                             harden=cfg.harden and not no_harden,
                             memory=memory,
                             cpus=cpus,
                             recover=session_recover,
                             debian_mirror=debian_mirror,
                             extra_args=cfg.docker_args,
                             claude_args=claude_args)
    try:
        raise SystemExit(container.run(spec))
    except (FileNotFoundError, docker.errors.BuildError) as e:
        raise click.ClickException(str(e)) from e


def _resolve_env(cfg: Config, proj: Path, env_extra: tuple[str, ...], *, modify: bool,
                 venv_dir: str | None) -> dict[str, str]:
    """
    Resolve the box's environment from config layers and ``-e`` overrides.

    Precedence, lowest to highest: the managed ``UV_PROJECT_ENVIRONMENT`` default, the
    config ``[env]`` table, host values for ``pass_env`` names, then ``-e`` flags.

    Parameters
    ----------
    cfg : Config
        The loaded configuration.
    proj : Path
        The resolved project directory.
    env_extra : tuple[str, ...]
        ``KEY=VALUE`` strings from ``-e`` flags.
    modify : bool
        Whether sbclaude may write into the project directory.
    venv_dir : str | None
        Directory in the box to hold its virtualenv, or ``None`` to keep it beside the project.

    Returns
    -------
    dict[str, str]
        The merged environment to inject into the box.

    Raises
    ------
    click.BadParameter
        If a ``-e`` value is not ``KEY=VALUE``.
    """
    env: dict[str, str] = {}
    # An explicit directory counts as asking for the box's virtualenv to be managed, even where
    # manage_uv_env turned that off, since the only way to honour it is to set the variables. A
    # project with no Python in it gets none of this: no directory beside it, no .gitignore line,
    # and venv_dir has nothing to hold.
    if (cfg.manage_uv_env or venv_dir) and container.is_python_project(proj):
        if venv_dir:
            # Absolute, so it escapes the project; per project inside that directory, so one
            # mounted volume can serve every project without them sharing an environment.
            venv = container.uv_project_environment_in(venv_dir, proj)
            env['SBCLAUDE_VENV'] = venv
            env['UV_PROJECT_ENVIRONMENT'] = venv
        elif modify:
            # Relative, deliberately: uv resolves it against whichever project it is working on,
            # so every project the box touches gets its own virtualenv beside its own
            # pyproject.toml. An absolute path here would funnel a `uv sync` run in any second
            # project into the first project's environment and quietly replace its packages.
            env['UV_PROJECT_ENVIRONMENT'] = container.VENV_DIR_NAME
            # Absolute, because the entrypoint provisions this one and puts its bin directory on
            # PATH.
            env['SBCLAUDE_VENV'] = container.uv_project_environment(proj)
            container.exclude_venv_from_git(proj)
        else:
            # Absolute and inside the container, because a relative path would put uv's
            # environment back in the project. Every project the box touches then shares this one,
            # which is the price of creating nothing beside them.
            venv = container.transient_uv_project_environment(proj)
            env['SBCLAUDE_VENV'] = venv
            env['UV_PROJECT_ENVIRONMENT'] = venv
        # Provisioning runs `uv sync`, which refreshes the project's uv.lock, so it is a write to
        # the project even when the virtualenv itself lands elsewhere.
        env['SBCLAUDE_SETUP_VENV'] = '1' if cfg.setup_venv and modify else '0'
    env.update(cfg.env)
    env.update({k: os.environ[k] for k in (*DEFAULT_PASS_ENV, *cfg.pass_env) if k in os.environ})
    for item in env_extra:
        key, sep, value = item.partition('=')
        if not sep:
            msg = f'expected KEY=VALUE, got {item!r}'
            raise click.BadParameter(msg)
        env[key] = value
    return env


@main.command(name='ls')
def ls() -> None:
    """List running sbclaude containers."""
    boxes = list(container.list_managed())
    if not boxes:
        click.echo('No running sbclaude containers.')
        return
    width = max(len(b['name']) for b in boxes)
    for box in boxes:
        click.echo(
            f'{box["name"]:<{width}}  {box["status"]:<8}  {box["image"]:<20}  {box["project"]}')


@main.command()
@click.option('-n', '--name', help='Container name. Default: every box for the current project.')
@click.option('--all', 'all_', is_flag=True, help='Stop every sbclaude container.')
def stop(name: str | None, *, all_: bool) -> None:
    """Stop running boxes (every box for the current project by default)."""
    if all_:
        removed = list(container.stop(None))
    elif name:
        removed = list(container.stop(name))
    else:
        removed = list(container.stop(project=Path.cwd().resolve()))
    click.echo(f'Stopped: {", ".join(removed)}' if removed else 'No matching box.')


@main.command()
@click.option('-n', '--name', help="Container name. Default: the current project's box.")
@click.option('--root',
              'as_root',
              is_flag=True,
              help='Open the shell as root instead of the mapped user.')
def shell(name: str | None, *, as_root: bool) -> None:
    """Open a debug shell inside a running box, as the user the box mirrors."""  # noqa: DOC501
    target = name
    if target is None:
        names = container.project_containers(Path.cwd().resolve())
        if not names:
            msg = 'No running box for this project.'
            raise click.ClickException(msg)
        if len(names) > 1:
            msg = f'Multiple boxes for this project; pass -n NAME: {", ".join(names)}'
            raise click.ClickException(msg)
        target = names[0]
    raise SystemExit(container.shell(target, root=as_root))


@main.command()
@click.option('--no-cache', is_flag=True, help='Build without the cache.')
@click.option('--debian-mirror',
              help='Debian archive mirror to bake into the image, e.g. '
              'http://ftp.us.debian.org/debian.')
def build(debian_mirror: str | None, *, no_cache: bool) -> None:
    """Build the sbclaude Docker image from the packaged Dockerfile."""  # noqa: DOC501
    mirror = debian_mirror or load_config().debian_mirror
    try:
        for line in container.build_images(no_cache=no_cache, debian_mirror=mirror):
            click.echo(line)
    except docker.errors.BuildError as e:
        msg = f'Image build failed: {e}'
        raise click.ClickException(msg) from e


@main.command(name='delete-image')
def delete_image() -> None:
    """Delete the sbclaude Docker images."""
    removed = container.delete_images()
    click.echo(f'Deleted: {", ".join(removed)}' if removed else 'No sbclaude images to delete.')


@main.command(name='config')
def config_cmd() -> None:
    """Print the config file path and whether it exists."""
    path = config_path()
    click.echo(f'{path} ({"exists" if path.is_file() else "not created yet"})')


@main.command(name='scaffold-noclip')
@click.option('-f', '--force', is_flag=True, help='Overwrite files that already exist.')
@click.option('-s', '--scene', help='Scene id for the generated config, e.g. MyGame/Level1.')
@click.argument('target', required=False, type=click.Path(file_okay=False, path_type=Path))
def scaffold_noclip(target: Path | None, scene: str | None, *, force: bool) -> None:
    """
    Scaffold a noclip visual-regression harness into TARGET (default: the current directory).

    Writes viewer-tests/ (glue, config, baselines/, out/) plus a getting-started guide. The
    ``webshot`` tool it drives is already installed in the box. Other harnesses, if they are ever
    added, get their own ``scaffold-<kind>`` command rather than a flag on this one.
    """  # noqa: DOC501
    target = target or Path.cwd()
    try:
        result = scaffold(target, 'noclip', scene=scene, force=force)
    except OSError as e:
        raise click.ClickException(str(e)) from e
    for path in result.written:
        click.echo(f'created {path}')
    for path in result.skipped:
        click.echo(f'exists, left alone: {path}')
    if result.skipped:
        click.echo('Re-run with --force to overwrite.', err=True)
    if result.written:
        click.echo('\nNext: start your dev server, then '
                   f'`cd {target / "viewer-tests"} && ./nc.mjs watch overview`')
