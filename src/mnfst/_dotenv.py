"""The Manifest variables a project keeps in its dotenv files.

Django and FastAPI apps commonly load `MNFST_KEY` from a `.env` file at
startup, through `python-dotenv` or `pydantic-settings` — the app works fine.
`mnfst doctor` runs as its own process, so nothing loads that file for it; it
has to read it itself. Only `MNFST_KEY` and `MNFST_URL`, from plain
`NAME=value` lines: nothing is evaluated or expanded, and no dependency is
added to parse them.
"""
from __future__ import annotations

import os
import re

# `.env.local` is the override file (Next.js/Vite convention, and the one
# python-dotenv users reach for too); an earlier file wins.
FILES = (".env.local", ".env")

_LINE = re.compile(r"^\s*(?:export\s+)?(MNFST_KEY|MNFST_URL)\s*=(.*)$")


def _value(raw: str) -> str:
    """A quoted value up to its closing quote; an unquoted one up to a ` #` comment."""
    raw = raw.strip()
    if raw and raw[0] in "\"'":
        end = raw.find(raw[0], 1)
        return raw[1:] if end == -1 else raw[1:end]
    return raw.split(" #", 1)[0].strip()


def read(root: str) -> dict[str, str]:
    """MNFST_KEY and MNFST_URL from `root`'s dotenv files, first file that
    sets a variable wins."""
    found: dict[str, str] = {}
    for name in FILES:
        try:
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        in_file: dict[str, str] = {}
        for line in lines:
            match = _LINE.match(line)
            if match:
                in_file[match.group(1)] = _value(match.group(2))
        for key, value in in_file.items():
            if value and key not in found:
                found[key] = value
    return found
