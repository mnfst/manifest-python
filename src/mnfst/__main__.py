"""`mnfst` console script: run a command with the SDK preloaded.

    mnfst run uvicorn main:app
    mnfst run gunicorn app:app
    mnfst run celery -A tasks worker

`manifest()` must run in the process that makes the calls, before its first
call. Finding that process is the expensive part of Python setup: a monorepo
has several candidate entry points and picking wrong fails silently. `mnfst
run` removes the search. It prepends a bootstrap directory to PYTHONPATH, so
the child interpreter imports our `sitecustomize` (and therefore `manifest()`)
at startup, then execs the real command. `ddtrace-run` and
`opentelemetry-instrument` work the same way.

The source-level call stays supported: a host dashboard start command you
cannot edit, AWS Lambda, and notebooks have no command to prefix.
"""
from __future__ import annotations

import os
import sys
from importlib.resources import files
from typing import Mapping, MutableMapping, Optional, Sequence

USAGE = """usage: mnfst <command> [args...]

commands:
  run <command> [args...]   run a command with Manifest preloaded
  doctor                    verify the install: SDK, key, and preload

examples:
  mnfst run uvicorn main:app
  mnfst run gunicorn app:app
  mnfst run celery -A tasks worker
  mnfst doctor

`mnfst run` requires MNFST_KEY; without it the command still runs, uninstrumented.
"""


def bootstrap_directory() -> str:
    """The shipped directory that holds `sitecustomize.py`.

    Prepending it puts our `sitecustomize` ahead of any other on PYTHONPATH,
    so the interpreter imports it at startup. The directory holds nothing else,
    so it cannot shadow an application module.
    """
    return str(files("mnfst").joinpath("_bootstrap"))


def environment_with_bootstrap(environ: Mapping[str, str]) -> dict:
    """A copy of `environ` with the bootstrap directory first on PYTHONPATH."""
    env: MutableMapping[str, str] = dict(environ)
    bootstrap = bootstrap_directory()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = bootstrap if not existing else bootstrap + os.pathsep + existing
    return env


def main(argv: Optional[Sequence[str]] = None) -> Optional[int]:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return
    if args[0] in ("-V", "--version"):
        from .version import VERSION
        sys.stdout.write(VERSION + "\n")
        return
    if args[0] == "doctor":
        if len(args) > 1:
            sys.stderr.write(f"mnfst: 'doctor' takes no arguments\n\n{USAGE}")
            raise SystemExit(2)
        from .doctor import run_doctor
        return run_doctor()
    if args[0] != "run":
        sys.stderr.write(f"mnfst: unknown command {args[0]!r}\n\n{USAGE}")
        raise SystemExit(2)

    command = args[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        sys.stderr.write(f"mnfst: 'run' needs a command\n\n{USAGE}")
        raise SystemExit(2)

    if not os.environ.get("MNFST_KEY"):
        sys.stderr.write("mnfst: MNFST_KEY is not set; mnfst is disabled.\n")

    try:
        os.execvpe(command[0], command, environment_with_bootstrap(os.environ))
    except FileNotFoundError:
        sys.stderr.write(f"mnfst: command not found: {command[0]}\n")
        raise SystemExit(127)


if __name__ == "__main__":
    raise SystemExit(main())
