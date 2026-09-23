"""Build the /v1/heal payload. The SDK never parses error dialects —
the raw error body travels (capped) and the server normalizes (CONTRACT §3).

Everything about the failing request travels — URL with query, headers,
body — so the server has the whole context to heal with. Credential VALUES
never do (CONTRACT §6): query/header values are masked to REDACTED with
their names kept, and credential-named top-level body keys are withheld and
restored on retry by the merge. `safe_url` / `safe_headers` output is for
the wire only; never feed it back into a live request.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RESPONSE_BODY_CAP = 65536
HEADER_VALUE_CAP = 1024
ERROR_TEXT_CAP = 512

# The client-side MINIMUM of credential-named fields (query params and body
# keys). Matched after normalization; the server may know more names.
SECRET_PARAMS = frozenset({
    "api_key", "apikey", "api_token", "key", "token", "access_token", "refresh_token",
    "auth", "authorization", "signature", "sig", "secret", "client_secret",
    "password", "session", "session_id",
    "bearer", "jwt", "id_token", "auth_token", "pwd", "passwd", "private_key",
})

# Header names are matched on ROOTS: any header whose normalized name contains
# one carries a credential (authorization, proxy_authorization, x_api_key,
# x_goog_api_key, cookie, x_amz_security_token, ...). Over-masking a harmless
# header (idempotency_key) costs nothing — its presence still travels.
SECRET_HEADER_ROOTS = ("auth", "key", "token", "secret", "session", "password",
                       "passwd", "cookie", "signature", "credential", "bearer", "jwt")

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _normalize(name: str) -> str:
    """X-Api-Key, apiKey and api_key are all the same secret."""
    normalized = _CAMEL_BOUNDARY.sub(r"\1_\2", name).replace("-", "_").lower()
    return normalized[2:] if normalized.startswith("x_") else normalized


def is_secret_field(name: Any) -> bool:
    return isinstance(name, str) and _normalize(name) in SECRET_PARAMS


def is_secret_header(name: str) -> bool:
    normalized = _normalize(name)
    return normalized in SECRET_PARAMS or any(root in normalized for root in SECRET_HEADER_ROOTS)


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        query = urlencode([(k, "REDACTED" if is_secret_field(k) else v) for k, v in pairs])
    # user:password@host is a credential too — keep host[:port] only.
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def tracked_url(url: str) -> Optional[str]:
    """The URL a tracked call is reported under: scheme, host, port and path
    only. The query, fragment and userinfo are where credentials ride, so they
    never leave the process. None when the URL is not http(s)."""
    try:
        parts = urlsplit(str(url))
    except Exception:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def safe_headers(headers: Mapping[str, Any]) -> dict:
    """Every header travels, lowercased; credential values are masked."""
    out: dict = {}
    try:
        for name, value in headers.items():
            key = str(name).lower()
            text = value.decode("latin-1") if isinstance(value, bytes) else str(value)
            out[key] = "REDACTED" if is_secret_header(key) else text[:HEADER_VALUE_CAP]
    except Exception:
        pass
    return out


def traveling_body(body: Any) -> Any:
    """What of the body goes on the wire: an object minus its credential-named
    top-level keys (the merge restores them on retry, CONTRACT §4); any other
    JSON as-is."""
    if isinstance(body, dict):
        return {k: v for k, v in body.items() if not is_secret_field(k)}
    return body


# What a client's exception text embeds: a whole URL, or the rooted path plus
# query it failed on (requests says "Max retries exceeded with url: /p?key=…").
_URL_IN_TEXT = re.compile(
    r"https?://[^\s'\"<>)\]}]+"
    r"|/[^\s'\"<>)\]}]*\?[^\s'\"<>)\]}]+"
)
_PARAM_ASSIGNMENT = re.compile(r"([A-Za-z0-9_-]+)=([^\s&'\"<>)\]}]+)")


def _mask_url_match(match: "re.Match") -> str:
    try:
        return safe_url(match.group(0))
    except Exception:
        return "REDACTED_URL"  # unparseable: drop it whole rather than guess


def _mask_param_match(match: "re.Match") -> str:
    name = match.group(1)
    return f"{name}=REDACTED" if is_secret_field(name) else match.group(0)


def safe_error_text(exc: Any) -> str:
    """Exception text travels to Phoenix in the outcome report. An HTTP client
    error embeds the URL it failed on — query secrets included — so mask
    before it leaves the process."""
    try:
        text = str(exc)
    except Exception:
        return type(exc).__name__
    try:
        text = _URL_IN_TEXT.sub(_mask_url_match, text)
        text = _PARAM_ASSIGNMENT.sub(_mask_param_match, text)
    except Exception:
        return type(exc).__name__
    return text.encode("utf-8")[:ERROR_TEXT_CAP].decode("utf-8", "ignore")


def capped_response_body(raw: bytes) -> Tuple[Any, bool]:
    truncated = len(raw) > RESPONSE_BODY_CAP
    raw = raw[:RESPONSE_BODY_CAP]
    try:
        return json.loads(raw), truncated
    except Exception:
        text = raw.decode("utf-8", "replace")
        # Decoding can grow the body (each undecodable byte becomes a 3-byte
        # replacement char), and it is the text that travels — so cap the text.
        encoded = text.encode("utf-8")
        if len(encoded) > RESPONSE_BODY_CAP:
            text = encoded[:RESPONSE_BODY_CAP].decode("utf-8", "ignore")
            truncated = True
        return text, truncated


def heal_payload(*, trace_id: str, method: str, url: str, headers: Mapping[str, Any],
                 body: Any, status_code: int, response_body: Any, truncated: bool,
                 response_time_ms: int) -> dict:
    return {
        "traceId": trace_id,
        "request": {
            "method": method.upper(),
            "url": safe_url(url),
            "headers": safe_headers(headers),
            "body": traveling_body(body),  # any JSON; None when absent/huge/not JSON
        },
        "response": {"statusCode": status_code, "body": response_body, "truncated": truncated},
        "responseTimeMs": response_time_ms,
    }
