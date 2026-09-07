"""`hmc-mcp` and `python -m hmc_mcp` are one program.

ADR 0128 holds the two invocations equivalent "by construction, not by a standing
test", and moved L5 -- the live suite's only fd-2-closed launch -- onto the module
form on that premise. This module is that standing test: if the two ever stop being
one program, L5 keeps passing while proving its property about a program no operator
runs.

Two arms, because they fail on different regressions:

* the command tree, which catches a divergence in what the two forms dispatch to;
* the exit status, which catches a divergence in how that program's status is
  signalled -- ADR 0128's named risk, `hmc_mcp.main` returning a status instead of
  raising while `__main__.py` no longer wraps it.

Measured on this branch, that named regression needs *both* halves. A bare `main()`
in `__main__.py` alone does not diverge, because `app()` runs in Click's standalone
mode and raises `SystemExit` itself; with `main` also returning a status, the console
script's `sys.exit(main())` reports it (3) while a bare `main()` reports none (0).

ADR 0128:117-125 records two ways the invocations are intended *not* to be identical,
and both are accommodated rather than asserted:

* the Typer/Click program name, which appears in every `Usage:` line. It is taken as
  an opaque string -- the differing middle between the two `Usage:` lines' shared
  prefix and shared tail -- and replaced with one placeholder in that line only.
  Deriving it instead (from `sys.executable` plus the module name, say) would
  reproduce Click's own `_detect_program_name` and break on a Click upgrade for a
  reason that has nothing to do with `hmc_mcp`. Confining the substitution to the
  `Usage:` line matters: `hmc-mcp` recurs inside `--profile`'s help text, where
  `python -m hmc_mcp` does not, so a whole-output substitution corrupts the console
  form alone and is red at HEAD.
* `sys.path[0]`, which `-m` sets to the working directory. Both forms are launched
  with `cwd` at an empty `tmp_path` rather than under `-P`, so the module form keeps
  the `sys.path[0]` entry an operator's launch has while pointing it at a directory
  that shadows nothing. Neither compared observation reads `sys.path[0]`.

The child environment is pinned rather than inherited, because Click sizes help text
from the terminal. `COLUMNS` is wide enough that the longer program name's `Usage:`
line does not wrap; a narrower *equal* width is not sufficient, because the two names
differ in length and no single-line substitution reconciles a differing line count.

Styling is stripped rather than suppressed. rich decides it is writing to a terminal
from any of `TTY_COMPATIBLE`, `FORCE_COLOR`, `PY_COLORS` or -- via Typer -- `GITHUB_ACTIONS`,
which is set on every CI leg; and `NO_COLOR` then removes the colour but not the other
SGR attributes. A styled `Usage:` line matches no prefix and splits the module form's
program name into escape-delimited runs, so both normalisation steps fail at once.
Chasing that key list would mean tracking rich's own precedence, so the escapes come
out of the captured text instead and any future key is covered by construction.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]

_PLACEHOLDER = "<prog>"

# Removed from the child environment: `HMC_*` selects a connection, the config-dir keys
# move where a profile is read from, the colour keys make rich style what it renders,
# and Typer reads `TERMINAL_WIDTH` in preference to the `COLUMNS` pinned below -- a
# narrow inherited value would wrap the longer program name's `Usage:` line alone.
_DROPPED = (
    "XDG_CONFIG_HOME", "APPDATA", "FORCE_COLOR", "CLICOLOR_FORCE", "TERMINAL_WIDTH",
)

# SGR escapes, which survive `NO_COLOR`. The sibling idiom at
# `tests/app/test_fail_closed_startup.py:94-96`.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _launchers() -> tuple[list[str], list[str]]:
    """The two forms, both pinned to this checkout's virtualenv.

    The console-script half is `test_fail_closed_startup.py`'s: a proof that silently
    ran against another build is worse than no proof. The module half needs the same
    pin, which `sys.executable` gives only while it is the interpreter beside that
    script -- so the two are required to share a `bin` directory.
    """
    executable = shutil.which("hmc-mcp")
    if executable is None:
        pytest.skip("the hmc-mcp console script is not on PATH")
    assert str(REPO_ROOT) in str(Path(executable).resolve().parents[1]), (
        "the console script resolves outside this checkout, so the equivalence proved "
        "would belong to a different build of hmc-mcp"
    )
    # Unresolved on both sides: a venv's `bin/python` is a symlink out to the
    # interpreter it was created from, and resolving it would leave the venv.
    assert Path(sys.executable).parent == Path(executable).parent, (
        f"{sys.executable} is not beside {executable}, so the two forms would compare "
        "two different installs rather than two entry points into one"
    )
    return [executable], [sys.executable, "-m", "hmc_mcp"]


def _child_env(home: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("HMC_")}
    for name in _DROPPED:
        env.pop(name, None)
    env["HOME"] = str(home)
    env["NO_COLOR"] = "1"
    env["COLUMNS"] = "100"
    env["LINES"] = "50"
    return env


def _run_both(args: list[str], tmp_path: Path) -> tuple[
    subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]
]:
    """Run one argument list under both forms, from one environment."""
    console, module = _launchers()
    env = _child_env(tmp_path)
    return tuple(  # type: ignore[return-value]
        subprocess.run(
            [*launcher, *args],
            capture_output=True, text=True, env=env, cwd=tmp_path,
            stdin=subprocess.DEVNULL, timeout=60, check=False,
        )
        for launcher in (console, module)
    )


def _rstripped(text: str) -> list[str]:
    """Unstyle, then rstrip: rich pads every line out to the terminal width.

    In that order, because a forced-terminal render puts the padding *inside* the
    styling and closes with a reset, so the spaces are not trailing until the escapes
    are gone.
    """
    return [_ANSI.sub("", line).rstrip() for line in text.splitlines()]


def _usage_line(lines: list[str], form: str) -> str:
    usage = [line for line in lines if line.lstrip().startswith("Usage:")]
    assert len(usage) == 1, (
        f"expected exactly one Usage: line from the {form}, got {len(usage)}; the run "
        "did not render the help it was asked for"
    )
    return usage[0]


def _common_suffix(left: str, right: str) -> str:
    return os.path.commonprefix([left[::-1], right[::-1]])[::-1]


def _normalise(
    console_lines: list[str], module_lines: list[str], command_path: tuple[str, ...]
) -> tuple[str, str]:
    """Replace each form's program name with one placeholder, in its `Usage:` line."""
    console_usage = _usage_line(console_lines, "console script")
    module_usage = _usage_line(module_lines, "module form")
    if console_usage == module_usage:
        return "\n".join(console_lines), "\n".join(module_lines)

    prefix = os.path.commonprefix([console_usage, module_usage])
    tail = _common_suffix(console_usage, module_usage)
    # Without this, a divergence in the command *path* would sit in the differing
    # middle and be normalised away along with the program name. Whole tokens, not
    # substrings: a `systems` renamed to `legacysystems` is a suffix of itself.
    # What it bounds is that the command path survived, not that the substituted
    # middle held the program name and nothing else.
    for token in (*command_path, "[OPTIONS]"):
        assert token in tail.split(), (
            f"{token!r} is not a whole token in the two Usage lines' shared tail "
            f"{tail!r}: either the command trees diverged rather than only the program "
            "name, or the Usage line wrapped at the pinned width"
        )

    replaced = prefix + _PLACEHOLDER + tail
    return (
        "\n".join(replaced if line == console_usage else line for line in console_lines),
        "\n".join(replaced if line == module_usage else line for line in module_lines),
    )


@pytest.mark.parametrize("command_path", [(), ("systems",)], ids=["root", "systems"])
def test_both_forms_render_the_same_command_tree(tmp_path, command_path):
    """`--help` for the root and one representative subcommand, program name aside."""
    console, module = _run_both([*command_path, "--help"], tmp_path)

    for form, result in (("console script", console), ("module form", module)):
        assert result.returncode == 0, (
            f"the {form} exited {result.returncode} rather than rendering help: "
            f"{result.stderr}"
        )
    # Rendering help writes nothing to stderr under either form, so anything one of
    # them emits there is logic the other does not have -- a warning filter, a logging
    # handler, or a banner added to `__main__.py`, which no stdout comparison sees.
    assert console.stderr == module.stderr == "", (
        f"rendering help wrote to stderr: console {console.stderr!r}, module "
        f"{module.stderr!r}"
    )

    normalised_console, normalised_module = _normalise(
        _rstripped(console.stdout), _rstripped(module.stdout), command_path
    )
    assert normalised_console == normalised_module, (
        "the console script and python -m hmc_mcp render different command trees; "
        "only the program name is an intended difference (ADR 0128:117-119)"
    )


@pytest.mark.parametrize(
    "args",
    [["definitely-not-a-command"], ["serve"]],
    ids=["unknown-subcommand", "missing-required-option"],
)
def test_both_forms_reject_a_bad_argument_with_the_same_status(tmp_path, args):
    """One usage error raised by the parser, one raised inside the command body.

    The second is the arm that would move first if `hmc_mcp.main` started returning a
    status: it is raised by `serve` itself rather than by Click's parser.
    """
    console, module = _run_both(args, tmp_path)

    assert console.returncode == module.returncode, (
        f"the console script exited {console.returncode} and python -m hmc_mcp exited "
        f"{module.returncode} on `{' '.join(args)}`; the two entry points no longer "
        "signal the same exit status (ADR 0128:111-114)"
    )
    assert console.returncode == 2, (
        f"expected a usage error (2) from `{' '.join(args)}`, got "
        f"{console.returncode}; both forms agreeing on some other status would not "
        "prove they reached the program"
    )
