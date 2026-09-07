"""``python -m hmc_mcp`` — the console script's program, reached without the script.

Delegation is the whole body, deliberately: `hmc-mcp` and `python -m hmc_mcp` are
meant to be one program, and logic here is where they would start to diverge.
``SystemExit(main())`` rather than a bare ``main()`` because that is exactly what
the generated console script does — dropping it would leave the exit status as
the one way the two could still diverge (ADR 0128).

The live audit proof launches the server this way because `uv` generates the
console script as a `/bin/sh` trampoline past a threshold on the interpreter path
length, and that form cannot be relied on to exec with stderr closed (ADR 0128).
"""

from hmc_mcp import main

if __name__ == "__main__":
    raise SystemExit(main())
