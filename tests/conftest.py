"""Configuration for Pytest."""

from __future__ import annotations

from itertools import cycle
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
import os
import subprocess as sp

from click.testing import CliRunner
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture

if os.getenv('_PYTEST_RAISE', '0') != '0':  # pragma no cover

    @pytest.hookimpl(tryfirst=True)
    def pytest_exception_interact(call: pytest.CallInfo[None]) -> NoReturn:
        assert call.excinfo is not None
        raise call.excinfo.value

    @pytest.hookimpl(tryfirst=True)
    def pytest_internalerror(excinfo: pytest.ExceptionInfo[BaseException]) -> NoReturn:
        raise excinfo.value


@pytest.fixture(autouse=True)
def isolate_claude_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)


@pytest.fixture(autouse=True)
def recover_stale_process_cwd(request: pytest.FixtureRequest) -> None:
    """
    Recover when the process cwd was removed mid-session.

    Gentoo Portage test phases often run pytest with aggressive temporary-directory retention.
    The process working directory can then point at a path that no longer exists, so
    ``Path.cwd()`` raises ``FileNotFoundError`` before ``monkeypatch.chdir`` can save the
    prior cwd.
    """
    try:
        Path.cwd()
    except FileNotFoundError:
        os.chdir(Path(request.config.rootpath))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def docker_run(mocker: MockerFixture) -> Callable[..., MagicMock]:
    """
    Patch the ``docker run`` child process with one that yields a given exit code and stderr.

    Parameters
    ----------
    mocker : MockerFixture
        The pytest-mock fixture.

    Returns
    -------
    Callable[..., MagicMock]
        Factory taking ``code`` (one exit code, or one per attempt) and ``stderr``, returning the
        mock installed over :py:class:`subprocess.Popen`.
    """
    real_popen = sp.Popen

    def patch(code: int | Sequence[int] = 0, stderr: bytes = b'') -> MagicMock:
        codes = cycle([code]) if isinstance(code, int) else iter(code)
        proc = mocker.MagicMock()
        # Cycled, not a fixed list: a launch that fails is attempted more than once, and each
        # attempt reads the stream to the end again.
        proc.stderr.read.side_effect = cycle([stderr, b''] if stderr else [b''])
        proc.__enter__.return_value = proc

        # Only the box's own launch is faked; everything else that shells out (git, docker exec)
        # keeps the real Popen, which subprocess.run needs underneath it.
        def launch(args: Sequence[str], *rest: Any, **kwargs: Any) -> Any:
            if list(args[:2]) == ['docker', 'run']:
                proc.returncode = next(codes)
                return proc
            return real_popen(args, *rest, **kwargs)

        return mocker.patch('sbclaude.container.sp.Popen', side_effect=launch)

    return patch
