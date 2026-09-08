"""`-P` is what closes `python -m hmc_mcp`'s `sys.path[0]` shadowing exposure.

ADR 0128:120-133 records `-m` as putting the caller's working directory first on
`sys.path` where the console script puts its own `bin` directory, so a `hmc_mcp/` in
the launch directory is imported before the installed package -- in a process that
holds profile passwords and a granted access policy. The same record states that **no
in-package guard closes this**, and names `-P` / `PYTHONSAFEPATH=1` as the one remedy;
`CHANGELOG.md:90-93` is where operators are told to use it.

Until this module, that remedy was documented and unverified. `tests/app/
test_entry_point_equivalence.py` launches both forms with `cwd` at an empty `tmp_path`
*so that* shadowing cannot affect the comparison, and says so; L5's fixture in
`tests/app/test_authorization_audit_live.py` passes `-P` but asserts nothing about
what it prevents. So if `-P` stopped covering the exposure, nothing was red.

Two arms, and the first is load-bearing rather than incidental:

* a bare launch from a shadowed directory must reach the stub. This constructs the
  exposure. Without it the second arm would pass just as well against a directory that
  shadows nothing, which is the unverified-remedy shape this module exists to close.
* the same launch under each documented spelling of the remedy must reach the installed
  package instead.

Both spellings are exercised because both are what operators were told to use. They are
one CPython setting reached two ways, but a container or unit file reaches it by the
environment variable and a shell invocation by the flag, and a test covering one of them
leaves the other in exactly the state this issue was filed about.

Scope, from ADR 0128 and issue #725:

* the module form only. The console script is not exposed -- it puts its own `bin`
  directory at `sys.path[0]` -- so it has no case here.
* the package half only. The record's other half is a shadowed *dependency*, reachable
  because `hmc_mcp/__init__.py`'s module-scope `from importlib.metadata import version`
  resolves `csv`, `email`, `zipfile`, `textwrap` -- and `json` on 3.13+ -- against the
  launch directory. That set is version-dependent by the record's own account and this
  suite runs on CPython 3.11 through 3.14, so no assertion over it holds across the
  eight `ci` legs. It is out of scope here rather than covered version-conditionally.
* no `sys.path[0]` guard in `src/hmc_mcp/__main__.py` is proposed or implied. ADR 0128
  rejects that logic in that file: it would arrive after the module-scope imports above
  had already resolved against the launch directory.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]

#: The stub package's two exit statuses, one per module, distinct so the launch reports
#: *which* of them ran. Neither is a status the real CLI produces -- rendering help is
#: 0, a startup refusal is 1, a usage error is 2 -- so a match is unambiguous.
_SHADOW_INIT_STATUS = 97
_SHADOW_MAIN_STATUS = 98

#: Every child wait is bounded. `--help` renders and exits, so a child that has not
#: finished by now is not slow; the bound is what turns that into a named
#: `TimeoutExpired` rather than a `just verify` and eight `ci` legs hanging silently.
_DEADLINE = 60.0

# Removed from the child environment. The first five are
# `tests/app/test_entry_point_equivalence.py`'s list, for its reasons -- `HMC_*`
# selects a connection, the config-dir keys move where a profile is read from, the
# colour keys make rich style what it renders, and Typer prefers `TERMINAL_WIDTH` to
# the `COLUMNS` pinned below. The last two are this module's own and matter more here
# than anywhere else in the suite: an exported `PYTHONSAFEPATH` is the remedy under
# test, so inheriting it would make the bare arm fail while claiming the exposure had
# closed, and `PYTHONPATH` moves where `hmc_mcp` resolves from under both arms.
_DROPPED = (
    "XDG_CONFIG_HOME",
    "APPDATA",
    "FORCE_COLOR",
    "CLICOLOR_FORCE",
    "TERMINAL_WIDTH",
    "PYTHONSAFEPATH",
    "PYTHONPATH",
)

# SGR escapes, which survive `NO_COLOR`. rich decides it is writing to a terminal from
# any of `TTY_COMPATIBLE`, `FORCE_COLOR`, `PY_COLORS` or -- via Typer -- `GITHUB_ACTIONS`,
# which is set on every `ci` leg, so the `Usage:` line arrives styled there and plain
# here. The sibling idiom at `tests/app/test_entry_point_equivalence.py:96-97`.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(scope="module")
def installed_package() -> Path:
    """Where a shadow-free launch finds `hmc_mcp` -- required to be this checkout's.

    `tests/app/test_authorization_audit_live.py::server_module_command`'s guard, for
    its reason: "on PATH" and "the code on this branch" are different claims, and a
    `pytest` run from outside this checkout would otherwise prove `-P` protects some
    other build of `hmc_mcp`. There is no PATH lookup here to go wrong, so the
    interpreter is asked directly where the package it would import lives, under the
    same `-P` the remedy arm uses.

    Against `src/` rather than the checkout root, so a copied non-editable install into
    the in-checkout `.venv` fails this instead of passing as "this checkout".
    """
    probe = subprocess.run(
        [sys.executable, "-P", "-c", "import hmc_mcp; print(hmc_mcp.__file__)"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_DEADLINE,
    )
    # Not `check=True`: `CalledProcessError` stringifies to the exit status alone and
    # leaves the child's traceback in an attribute nobody prints.
    assert probe.returncode == 0, (
        f"{sys.executable} cannot import hmc_mcp, so there is no installed package for "
        f"the remedy to reach:\n{probe.stderr}"
    )
    origin = Path(probe.stdout.strip()).resolve()
    source = (REPO_ROOT / "src").resolve()
    assert origin.is_relative_to(source), (
        f"{origin} is not under {source}, so `-P` would be proved against a different "
        "build of hmc_mcp than this checkout's"
    )
    return origin


@pytest.fixture
def shadowed_cwd(tmp_path: Path) -> Path:
    """A launch directory holding a `hmc_mcp/` that is observably not the installed one.

    Both of the package's modules, carrying different statuses, because ADR 0128's
    claim is about which of them decides: `runpy` imports the package first, so a bare
    launch reports the `__init__.py` status and the `__main__.py` status is unreachable.
    A stub carrying only one of them could not tell those two apart, and it is the
    `__init__.py` half that the record says no guard inside `__main__.py` can reach.
    """
    package = tmp_path / "hmc_mcp"
    package.mkdir()
    (package / "__init__.py").write_text(f"raise SystemExit({_SHADOW_INIT_STATUS})\n")
    (package / "__main__.py").write_text(f"raise SystemExit({_SHADOW_MAIN_STATUS})\n")
    return tmp_path


def _launch(
    cwd: Path,
    *,
    flags: tuple[str, ...] = (),
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`python [flags] -m hmc_mcp --help`, from `cwd`, on a pinned environment."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("HMC_")}
    for name in _DROPPED:
        env.pop(name, None)
    # A copy rather than a mapping built from scratch, so the child keeps the `PATH`
    # and loader state it needs; `HOME` is redirected at the scratch directory so no
    # developer's real profile or access policy is in reach of the child.
    env["HOME"] = str(cwd)
    env["NO_COLOR"] = "1"
    env["COLUMNS"] = "100"
    env["LINES"] = "50"
    env.update(overrides or {})

    return subprocess.run(
        [sys.executable, *flags, "-m", "hmc_mcp", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        timeout=_DEADLINE,
        check=False,
    )


def test_a_bare_module_launch_imports_the_shadowing_package(shadowed_cwd, installed_package):
    """The exposure, constructed: without the remedy the launch directory wins.

    This arm is what makes the next one mean something. If a bare launch stopped
    reaching the stub -- because the stub stopped shadowing, or because `-m` stopped
    putting the working directory first -- the remedy arm would keep passing while
    proving nothing about a directory an operator does not control.
    """
    result = _launch(shadowed_cwd)

    assert result.returncode == _SHADOW_INIT_STATUS, (
        f"expected the shadowing package's __init__ to decide ({_SHADOW_INIT_STATUS}), "
        f"got {result.returncode}; a bare `python -m hmc_mcp` no longer imports a "
        f"`hmc_mcp/` in the launch directory ahead of the installed package, so nothing "
        f"here is exercising ADR 0128:120-133. stderr={result.stderr!r}"
    )
    # `stdout` is deliberately not in that message: the failure this arm reports is a
    # launch that reached the *real* CLI, whose `--help` is forty lines of rendered
    # panel. The status is the whole finding, and `stderr` is where a stub that failed
    # some other way would leave its traceback.


@pytest.mark.parametrize(
    ("flags", "overrides"),
    [(("-P",), {}), ((), {"PYTHONSAFEPATH": "1"})],
    ids=["-P", "PYTHONSAFEPATH=1"],
)
def test_the_documented_remedy_reaches_the_installed_package(
    shadowed_cwd, installed_package, flags, overrides
):
    """Both spellings `CHANGELOG.md:90-93` gives operators, from that same directory."""
    result = _launch(shadowed_cwd, flags=flags, overrides=overrides)

    assert result.returncode == 0, (
        f"expected the installed CLI to render help (0), got {result.returncode}; "
        f"{_SHADOW_INIT_STATUS} or {_SHADOW_MAIN_STATUS} means the shadowing package "
        f"was reached anyway, so the remedy ADR 0128:120-133 and CHANGELOG.md:90-93 "
        f"give operators no longer keeps the launch directory off sys.path. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    rendered = [_ANSI.sub("", line).lstrip() for line in result.stdout.splitlines()]
    assert any(line.startswith("Usage:") for line in rendered), (
        f"exit 0 with no Usage: line, so the child rendered no help and 0 is not "
        f"evidence it reached the installed package at {installed_package}. "
        f"stdout={result.stdout!r}"
    )
