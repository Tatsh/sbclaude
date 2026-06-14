"""CLI entry point for sbclaude."""

from __future__ import annotations

from pathlib import Path
import os

from bascom import setup_logging
import click

from . import container
from .config import config_path, expand_paths, load_config

__all__ = ('main',)


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
@click.option('-R', '--re', 'use_re', is_flag=True, help='RE image + Ghidra + Android tools.')
@click.option('--ghidra', 'use_ghidra', is_flag=True, help='Mount host Ghidra (read-only).')
@click.option('--android',
              'use_android',
              is_flag=True,
              help='Mount Android SDK + ~/.android + kvm.')
@click.option('--usb', 'use_usb', is_flag=True, help='Expose /dev/bus/usb for adb.')
@click.option('--ios',
              'use_ios',
              is_flag=True,
              help='Mount the host usbmuxd socket so frida can reach an iOS device.')
@click.option('--x11', 'use_x11', is_flag=True, help='Forward DISPLAY + XAUTHORITY for GUIs.')
@click.option('--ssh',
              'use_ssh',
              is_flag=True,
              help='Mount the host ~/.ssh read-only for SSH git remotes.')
@click.option('--gpg',
              'use_gpg',
              is_flag=True,
              help='Mount the host GnuPG home and agent socket for signing commits.')
@click.option('--net',
              'network',
              help='Docker network mode (default: host; use "bridge" to isolate).')
@click.option('-e',
              '--env',
              'env_extra',
              multiple=True,
              help='Extra env var KEY=VALUE for the box, e.g. -e CLAUDE_CODE_USE_BEDROCK=1.')
@click.option('--no-harden',
              'no_harden',
              is_flag=True,
              help='Disable the container hardening flags (cap-drop, no-new-privileges, ...).')
@click.option('-d', '--debug', is_flag=True, help='Enable debug level logging.')
@click.argument('claude_args', nargs=-1, type=click.UNPROCESSED)
def run(project: Path | None, rw_extra: tuple[str, ...], ro_extra: tuple[str, ...],
        env_extra: tuple[str, ...], name: str | None, image: str | None, network: str | None,
        debian_mirror: str | None, claude_args: tuple[str, ...], *, use_re: bool, use_ghidra: bool,
        use_android: bool, use_usb: bool, use_ios: bool, use_x11: bool, use_ssh: bool,
        use_gpg: bool, no_harden: bool, debug: bool) -> None:
    """Launch claude in a fresh container. Args after ``--`` pass through to claude."""  # noqa: DOC501
    setup_logging(debug=debug, loggers={'sbclaude': {}})
    cfg = load_config()
    flags = set(cfg.default_flags)
    use_re = use_re or '--re' in flags
    use_ghidra = use_ghidra or use_re or '--ghidra' in flags
    use_android = use_android or use_re or '--android' in flags
    use_usb = use_usb or '--usb' in flags
    use_ios = use_ios or '--ios' in flags
    use_x11 = use_x11 or '--x11' in flags
    use_ssh = use_ssh or '--ssh' in flags
    use_gpg = use_gpg or '--gpg' in flags
    debian_mirror = debian_mirror or cfg.debian_mirror
    proj = (project or Path.cwd()).resolve()
    # Env precedence (lowest to highest): the managed UV_PROJECT_ENVIRONMENT default, the
    # config [env] table, host values for pass_env names, then -e flags. Seeding the uv
    # default first lets the user override or unset it through any of the later layers.
    env: dict[str, str] = {}
    if cfg.manage_uv_env:
        env['UV_PROJECT_ENVIRONMENT'] = container.uv_project_environment(proj)
    env.update(cfg.env)
    env.update({k: os.environ[k] for k in cfg.pass_env if k in os.environ})
    for item in env_extra:
        key, sep, value = item.partition('=')
        if not sep:
            msg = f'expected KEY=VALUE, got {item!r}'
            raise click.BadParameter(msg)
        env[key] = value
    spec = container.RunSpec(project=proj,
                             name=name or container.unique_name(proj),
                             network=network or cfg.network,
                             image=image or cfg.image,
                             use_re=use_re,
                             use_ghidra=use_ghidra,
                             use_android=use_android,
                             use_usb=use_usb,
                             use_ios=use_ios,
                             use_x11=use_x11,
                             use_ssh=use_ssh,
                             use_gpg=use_gpg,
                             ro=[*expand_paths(cfg.ro), *expand_paths(ro_extra)],
                             rw=[*expand_paths(cfg.rw), *expand_paths(rw_extra)],
                             env=env,
                             harden=cfg.harden and not no_harden,
                             debian_mirror=debian_mirror,
                             extra_args=cfg.docker_args,
                             claude_args=claude_args)
    try:
        raise SystemExit(container.run(spec))
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e


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
def shell(name: str | None) -> None:
    """Open a root debug shell inside a running box."""  # noqa: DOC501
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
    raise SystemExit(container.shell(target))


@main.command()
@click.option('--no-re',
              'with_re',
              flag_value=False,
              default=True,
              type=bool,
              help='Skip building the RE image.')
@click.option('--no-cache', is_flag=True, help='Build without the cache.')
@click.option('--debian-mirror',
              help='Debian archive mirror to bake into the base image, e.g. '
              'http://ftp.us.debian.org/debian.')
def build(debian_mirror: str | None, *, with_re: bool, no_cache: bool) -> None:
    """Build the sbclaude Docker images from the packaged Dockerfiles."""
    mirror = debian_mirror or load_config().debian_mirror
    for line in container.build_images(with_re=with_re, no_cache=no_cache, debian_mirror=mirror):
        click.echo(line)


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
