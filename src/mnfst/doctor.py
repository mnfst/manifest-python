"""`mnfst doctor`: run the install checks the dashboard's troubleshooting
prompt could only ask a coding agent to perform by reading code.

A broken install and a healthy app look identical from the outside — both are
silence. Four causes hide behind that silence, and this command separates them
by running, not by reading: the SDK may not be importable, MNFST_KEY may be
unset, the key may be invalid, or the init may never run in the process that
makes the calls.

The key check is the one nothing else does. A typo'd or revoked key behaves
exactly like a correct one, so a single authenticated round trip answers the
question in the second it matters most. It uses the handshake endpoint the SDK
announces on, so it proves the same credential the heal path will use.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from .config import HEAL_TIMEOUT_SECONDS, Config, resolve_config
from .outbound import installed_config
from .version import VERSION

HELLO_PATH = "/v1/hello"

OK = "ok"
FAIL = "fail"
WARN = "warn"

MARKS = {OK: "✅", FAIL: "❌", WARN: "⚠️"}
LABEL_WIDTH = 22


@dataclass
class Check:
    status: str
    label: str
    detail: str = ""


def mask_key(key: str) -> str:
    """Enough of a key to recognize it, never enough to use it."""
    if len(key) <= 11:
        return "…"
    return f"{key[:8]}…{key[-4:]}"


def project_name(response: httpx.Response) -> Optional[str]:
    """The project a valid key belongs to, when the server names it."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    project = body.get("project")
    if isinstance(project, dict) and isinstance(project.get("name"), str):
        return project["name"] or None
    for field in ("projectName", "name"):
        value = body.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def probe_key(config: Config) -> Check:
    """One authenticated round trip: does the server accept this key?

    `probe` marks it a key check rather than a boot. Without it the server
    records an install, and the dashboard reports the app as connected because
    someone ran a diagnostic — while this same command prints "not loaded
    here" two lines below. Only a real `manifest()` announces.
    """
    try:
        response = httpx.post(
            config.base_url + HELLO_PATH,
            json={"probe": True},
            headers={
                "authorization": f"Bearer {config.api_key}",
                "user-agent": f"mnfst-python/{VERSION}",
            },
            timeout=HEAL_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return Check(FAIL, "Key valid",
                     f"cannot reach {config.base_url} ({exc.__class__.__name__})")
    if response.status_code in (401, 403):
        return Check(FAIL, "Key valid", "the server rejected the key")
    if response.status_code == 404:
        return Check(WARN, "Key valid",
                     "this server has no handshake endpoint to verify against")
    if response.status_code != 200:
        return Check(WARN, "Key valid", f"unexpected HTTP {response.status_code}")
    name = project_name(response)
    return Check(OK, "Key valid", f'project "{name}"' if name else "accepted")


def check_init() -> Check:
    """Whether `manifest()` ran in this process, which is the one that calls."""
    if installed_config() is not None:
        return Check(OK, "Preload active", "the SDK runs in this process")
    return Check(FAIL, "Preload active",
                 "not loaded here; start the app with `mnfst run …` or call "
                 "manifest() at startup")


def checks(probe: Callable[[Config], Check] = probe_key) -> list[Check]:
    config = resolve_config()
    result = [Check(OK, "SDK installed", f"mnfst {VERSION}")]
    if config.api_key:
        result.append(Check(OK, "MNFST_KEY set", mask_key(config.api_key)))
        result.append(probe(config))
    else:
        result.append(Check(FAIL, "MNFST_KEY set", "not set in this environment"))
        result.append(Check(WARN, "Key valid", "skipped; no key to check"))
    result.append(check_init())
    return result


def render(results: list[Check]) -> str:
    lines = []
    for check in results:
        mark = MARKS.get(check.status, check.status)
        row = f"  {mark} {check.label:<{LABEL_WIDTH}}"
        lines.append(f"{row} {check.detail}".rstrip())
    return "\n".join(lines) + "\n"


def run_doctor() -> int:
    results = checks()
    sys.stdout.write(render(results))
    return 1 if any(check.status == FAIL for check in results) else 0
