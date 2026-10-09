"""Crackspread foundation module: HTTP, time, JSON I/O, schema validation, stale logic.

Every other script imports this as a flat module (``import common``); ``scripts/`` is not a
package.  Keep stdout clean: all diagnostics go through :func:`log` (stderr).  No ``sys.exit``.
Runs on Python 3.9 and 3.12.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

import requests
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

__all__ = [
    "UA", "ROOT", "SITE", "DATA", "SCHEMAS", "FIXTURES",
    "FetchError", "PlausibilityError",
    "now_utc", "iso_utc", "parse_iso", "hours_since",
    "load_config", "http_get", "http_post",
    "read_json", "write_json_atomic", "write_json_if_changed", "strip_keys",
    "load_schema", "validate",
    "datapoint", "ensure_range",
    "stale_threshold_hours", "is_stale", "apply_time_stale", "mark_all_stale",
    "sha1", "slug", "log",
]

UA = "crackspread-bot/1.0 (+https://github.com/fixoa/crackspread)"

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = SITE / "data"
SCHEMAS = ROOT / "scripts" / "schemas"
FIXTURES = ROOT / "tests" / "fixtures"

#: Keys ignored by :func:`write_json_if_changed` when deciding whether a document changed.
DEFAULT_IGNORE = ("generated_at", "fetched_at", "run_id", "last_run")

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

PathLike = Union[str, "os.PathLike[str]"]


class FetchError(Exception):
    """Network/HTTP failure after retries (or a non-retryable HTTP status)."""


class PlausibilityError(ValueError):
    """A fetched value lies outside the allowed range."""


# --------------------------------------------------------------------------------------- time

def now_utc() -> datetime:
    """Aware UTC "now", truncated to whole seconds.

    Honours the environment variable ``CRACKSPREAD_NOW`` (ISO-8601, e.g. ``2026-10-08T10:00:00Z``)
    so that runs and tests are deterministic.
    """
    override = os.environ.get("CRACKSPREAD_NOW", "").strip()
    if override:
        try:
            return parse_iso(override)
        except ValueError as exc:
            raise ValueError(
                "CRACKSPREAD_NOW=%r is not an ISO-8601 timestamp (expected e.g. 2026-10-08T10:00:00Z)" % override
            ) from exc
    return datetime.now(timezone.utc).replace(microsecond=0)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: Optional[datetime] = None) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC). Defaults to :func:`now_utc`."""
    if dt is None:
        dt = now_utc()
    return _to_utc(dt).strftime(_TIMESTAMP_FMT)


def parse_iso(s: Union[str, datetime]) -> datetime:
    """Parse ``YYYY-MM-DD`` (→ midnight UTC), ``YYYY-MM`` (→ first of month) or any ISO-8601
    datetime (``Z`` or offset; naive = UTC) into an aware UTC datetime. Raises ``ValueError``.
    """
    if isinstance(s, datetime):
        return _to_utc(s)
    if not isinstance(s, str) or not s.strip():
        raise ValueError("parse_iso: expected a non-empty ISO-8601 string, got %r" % (s,))
    text = s.strip()
    if _DATE_RE.match(text):
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if _MONTH_RE.match(text):
        return datetime.strptime(text, "%Y-%m").replace(tzinfo=timezone.utc)
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        return _to_utc(datetime.fromisoformat(candidate))
    except ValueError:
        pass
    # Python 3.9's fromisoformat is strict (no "20261008T10", odd fraction lengths, ...).
    from dateutil import parser as _dateutil_parser  # lazy: only needed for unusual inputs

    try:
        return _to_utc(_dateutil_parser.isoparse(text))
    except (ValueError, OverflowError):
        try:
            return _to_utc(_dateutil_parser.parse(text))
        except (ValueError, OverflowError) as exc:
            raise ValueError("parse_iso: cannot parse %r" % (s,)) from exc


def hours_since(as_of: Union[str, datetime], now: Optional[datetime] = None) -> float:
    """Hours elapsed between ``as_of`` and ``now`` (default :func:`now_utc`). Negative if in the future."""
    ref = parse_iso(as_of)
    current = _to_utc(now) if now is not None else now_utc()
    return (current - ref).total_seconds() / 3600.0


# ------------------------------------------------------------------------------------- config

_CONFIG_CACHE: Dict[str, dict] = {}


def load_config(path: Optional[PathLike] = None) -> dict:
    """Load ``site/config.json`` (or ``path``). The parsed file is cached per path; each call
    returns a deep copy so callers may mutate their copy freely."""
    cfg_path = Path(path) if path is not None else SITE / "config.json"
    key = str(cfg_path.resolve())
    if key not in _CONFIG_CACHE:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            raise ValueError("config at %s is not a JSON object" % cfg_path)
        _CONFIG_CACHE[key] = cfg
    return copy.deepcopy(_CONFIG_CACHE[key])


def _reset_config_cache() -> None:
    """Forget cached configs (used by tests that rewrite config files)."""
    _CONFIG_CACHE.clear()


# --------------------------------------------------------------------------------------- HTTP

_RETRYABLE_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _sleep(seconds: float) -> None:  # separate so tests can monkeypatch it
    time.sleep(seconds)


_SECRET_QUERY_RE = re.compile(r"(?i)\b(api_key|apikey|key|token|access_token|secret)=([^&\s'\")]+)")


def _redact(text: str) -> str:
    """Mask credential-looking query parameters (``api_key=…``, ``key=…``, ``token=…``) in URLs
    and exception texts. urllib3 repeats the full request path (incl. query string) in its error
    messages, and FetchError texts end up in meta.json and the public Actions log."""
    return _SECRET_QUERY_RE.sub(r"\1=***", text)


_SECRET_HEADER_RE = re.compile(r"(?i)(key|token|auth|secret|credential)")


def _header_secrets(headers: dict) -> list:
    """Values of credential-looking request headers (``x-api-key``, ``x-goog-api-key``,
    ``Authorization`` …), with a ``Bearer``/``Basic`` prefix stripped; used to mask the literal
    key should a server echo it in an error body or an exception text."""
    out = []
    for name, value in (headers or {}).items():
        if not isinstance(value, str) or not _SECRET_HEADER_RE.search(str(name)):
            continue
        token = value.strip()
        parts = token.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bearer", "basic"):
            token = parts[1].strip()
        if len(token) >= 6:
            out.append(token)
    return out


def _mask_values(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _close_quietly(resp: Any) -> None:
    """Release a response we are not going to return (failed attempt), ignoring errors."""
    close = getattr(resp, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001  (best effort)
            pass


def _http(
    method: str,
    url: str,
    kwargs: dict,
    *,
    timeout: float,
    retries: int,
    backoff: float,
    headers: Optional[dict],
    session: Any,
    retry_statuses: Iterable[int] = (),
    with_body: bool = False,
) -> requests.Response:
    """Shared request loop of :func:`http_get` and :func:`http_post` (see there for the rules).
    ``kwargs`` are passed to ``session.<method>(url, headers=..., timeout=..., **kwargs)``.
    ``retry_statuses`` are retried in addition to 5xx; ``with_body`` appends a short, masked excerpt
    of the response body to HTTP error texts (useful for API error payloads)."""
    attempts = max(1, int(retries))
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    secrets = _header_secrets(hdrs)

    def mask(text: str) -> str:
        return _mask_values(_redact(text), secrets)

    shown = mask(url)  # the only form of the URL that may appear in logs/errors
    client = session if session is not None else requests
    extra_retry = set(retry_statuses)
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            resp = getattr(client, method)(url, headers=hdrs, timeout=timeout, **kwargs)
        except _RETRYABLE_EXC as exc:
            last_error = "%s: %s" % (type(exc).__name__, _short(mask(str(exc))))
        except requests.exceptions.RequestException as exc:
            raise FetchError("%s: %s for %s" % (type(exc).__name__, _short(mask(str(exc))), shown)) from exc
        else:
            status = getattr(resp, "status_code", None)
            if status is not None and 200 <= status < 300:
                return resp
            body = ""
            if with_body:
                try:
                    body = _short(mask(str(getattr(resp, "text", "") or "")))
                except Exception:  # noqa: BLE001  (best effort: never fail on an error body)
                    body = ""
            _close_quietly(resp)
            detail = (": " + body) if body else ""
            if status is not None and (500 <= status < 600 or status in extra_retry):
                last_error = "HTTP %s%s" % (status, detail)
            else:
                raise FetchError("HTTP %s for %s%s" % (status, shown, detail))
        if attempt < attempts:
            delay = backoff * (2 ** (attempt - 1))
            log("http_%s: %s (attempt %d/%d) for %s; retrying in %.1fs" % (
                method, last_error, attempt, attempts, shown, delay))
            _sleep(delay)
    raise FetchError("%s after %d attempts for %s" % (last_error, attempts, shown))


def http_get(
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: float = 20,
    retries: int = 3,
    backoff: float = 1.5,
    headers: Optional[dict] = None,
    session: Any = None,
    stream: bool = False,
    max_bytes: Optional[int] = None,
) -> requests.Response:
    """GET ``url`` with the project User-Agent and bounded retries.

    * ``retries`` is the maximum number of attempts in total (default 3). Between attempts the
      function sleeps ``backoff * 2**(attempt-1)`` seconds (1.5 s, 3 s, ...).
    * Retries on connection errors, timeouts and HTTP 5xx.
    * Raises :class:`FetchError` immediately on 4xx (no retry) and on any other non-2xx status.
    * Never returns a non-2xx response; responses of failed attempts are closed.
    * ``max_bytes`` bounds the response body (brief §7: one oversized or crafted download must
      fail as *that* source, not kill the run): the body is read in chunks and
      :class:`FetchError` is raised (no retry) as soon as the limit is exceeded or a
      ``Content-Length`` header announces more. ``resp.content``, ``resp.text`` and
      ``resp.iter_lines()`` then serve the buffered body as usual.
    * Error messages and log lines mention the status and the ``url`` with credential-looking
      query parameters masked (``api_key=***``, ``key=***``, ``token=***``); ``params`` never
      appear, and exception texts from ``requests``/``urllib3`` are masked the same way, so an
      EIA or Gemini key cannot reach meta.json or the Actions log.
    """
    resp = _http("get", url, {"params": params, "stream": stream or max_bytes is not None},
                 timeout=timeout, retries=retries, backoff=backoff, headers=headers, session=session)
    if max_bytes is not None:
        _limit_body(resp, int(max_bytes), url)
    return resp


def _limit_body(resp: Any, max_bytes: int, url: str) -> None:
    """Read at most ``max_bytes`` of a streamed 2xx response into ``resp.content``; close it and
    raise :class:`FetchError` when the body is larger (declared or actual)."""
    shown = _redact(url)
    headers = getattr(resp, "headers", None) or {}
    try:
        declared = int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError, AttributeError):
        declared = 0
    if declared > max_bytes:
        _close_quietly(resp)
        raise FetchError("response too large (%d bytes announced, limit %d) for %s" % (declared, max_bytes, shown))
    chunks = []
    size = 0
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise FetchError("response too large (more than %d bytes) for %s" % (max_bytes, shown))
            chunks.append(chunk)
    except FetchError:
        _close_quietly(resp)
        raise
    except requests.exceptions.RequestException as exc:   # connection dropped mid-body
        _close_quietly(resp)
        raise FetchError("%s: %s while reading the body of %s" % (type(exc).__name__, _short(_redact(str(exc))), shown)) from exc
    resp._content = b"".join(chunks)      # what requests itself sets once a body is read
    resp._content_consumed = True


def http_post(
    url: str,
    *,
    json: Any = None,
    headers: Optional[dict] = None,
    timeout: float = 60,
    retries: int = 2,
    backoff: float = 2.0,
    session: Any = None,
) -> requests.Response:
    """POST a JSON body to ``url`` (used by the LLM providers in ``summarize.py``).

    Same rules as :func:`http_get` (User-Agent, bounded retries with backoff, credential masking,
    never returns a non-2xx response) with two differences: HTTP **429** is retried like a 5xx
    (rate limits), and HTTP error texts carry a short, masked excerpt of the response body so an
    API's error payload ("API key not valid", "model not found") reaches the log. API keys belong in
    ``headers``, never in the URL. Defaults: 60 s timeout, 2 attempts, 2 s backoff.
    """
    hdrs = {"Content-Type": "application/json"} if json is not None else {}
    if headers:
        hdrs.update(headers)
    return _http("post", url, {"json": json},
                 timeout=timeout, retries=retries, backoff=backoff, headers=hdrs, session=session,
                 retry_statuses=(429,), with_body=True)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------------------- JSON

def read_json(path: PathLike) -> Optional[Any]:
    """Return the parsed JSON document at ``path``; ``None`` if the file is missing or unparsable
    (the latter is logged as a warning)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        log("warning: cannot read %s: %s" % (p, _short(str(exc))))
        return None


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def write_json_atomic(path: PathLike, obj: Any) -> None:
    """Write ``obj`` as stable JSON (sorted keys, indent 1, UTF-8, trailing newline) to ``path``
    via ``<path>.tmp`` + ``os.replace``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    text = _dumps(obj)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def strip_keys(obj: Any, keys: Iterable[str]) -> Any:
    """Recursive copy of ``obj`` (dicts/lists) without any of ``keys`` at any depth."""
    drop = set(keys)
    if isinstance(obj, dict):
        return {k: strip_keys(v, drop) for k, v in obj.items() if k not in drop}
    if isinstance(obj, (list, tuple)):
        return [strip_keys(v, drop) for v in obj]
    return obj


def write_json_if_changed(path: PathLike, obj: Any, ignore: Iterable[str] = DEFAULT_IGNORE) -> bool:
    """Write ``obj`` unless the existing file is equal once ``ignore`` keys are stripped
    recursively. Returns ``True`` if written. An unchanged file keeps its old timestamps."""
    existing = read_json(path)
    if existing is not None:
        normalised = json.loads(_dumps(obj))  # tuples → lists, non-str keys → str, etc.
        if strip_keys(existing, ignore) == strip_keys(normalised, ignore):
            return False
    write_json_atomic(path, obj)
    return True


# ------------------------------------------------------------------------------------ schemas

_SCHEMA_CACHE: Dict[str, dict] = {}
_VALIDATOR_CACHE: Dict[str, Draft202012Validator] = {}


def load_schema(name: str) -> dict:
    """Return ``scripts/schemas/<name>.schema.json`` (cached, returned as a deep copy)."""
    base = name[: -len(".schema.json")] if name.endswith(".schema.json") else name
    base = base[: -len(".json")] if base.endswith(".json") else base
    if base not in _SCHEMA_CACHE:
        with open(SCHEMAS / ("%s.schema.json" % base), "r", encoding="utf-8") as fh:
            _SCHEMA_CACHE[base] = json.load(fh)
    return copy.deepcopy(_SCHEMA_CACHE[base])


def _validator(name: str) -> Draft202012Validator:
    base = name[: -len(".schema.json")] if name.endswith(".schema.json") else name
    if base not in _VALIDATOR_CACHE:
        schema = load_schema(base)
        Draft202012Validator.check_schema(schema)
        # format checking deliberately off: format_checker=None
        _VALIDATOR_CACHE[base] = Draft202012Validator(schema, format_checker=None)
    return _VALIDATOR_CACHE[base]


def validate(obj: Any, schema_name: str) -> None:
    """Validate ``obj`` against ``scripts/schemas/<schema_name>.schema.json`` (JSON Schema
    draft 2020-12, no format checking). Raises ``jsonschema.ValidationError`` (best match)."""
    error = best_match(_validator(schema_name).iter_errors(obj))
    if error is not None:
        raise error


# --------------------------------------------------------------------------------- datapoints

def datapoint(
    value: Any,
    unit: str,
    as_of: str,
    source: str,
    source_url: str,
    *,
    fetched_at: Optional[str] = None,
    stale: bool = False,
    **extra: Any,
) -> dict:
    """Build the common datapoint object. ``value`` may be ``None`` (unknown/failed)."""
    dp = {
        "value": value,
        "unit": unit,
        "as_of": as_of,
        "source": source,
        "source_url": source_url,
        "fetched_at": fetched_at if fetched_at is not None else iso_utc(),
        "stale": bool(stale),
    }
    dp.update(extra)
    return dp


def ensure_range(value: Any, lo: float, hi: float, name: str) -> float:
    """Return ``float(value)`` if ``lo <= value <= hi``; otherwise raise :class:`PlausibilityError`
    (also for ``None``, NaN and non-numeric input)."""
    try:
        if value is None or isinstance(value, bool):
            raise TypeError
        number = float(value)
    except (TypeError, ValueError):
        raise PlausibilityError("%s: not a number (%r)" % (name, value))
    if number != number:  # NaN
        raise PlausibilityError("%s: NaN" % name)
    if number < lo or number > hi:
        raise PlausibilityError("%s: %s outside plausible range %s..%s" % (name, number, lo, hi))
    return number


# -------------------------------------------------------------------------------------- stale

def stale_threshold_hours(kind: str, cfg: dict) -> float:
    """``cfg["stale_after_hours"][kind]`` as a float; ``KeyError`` for an unknown kind."""
    table = cfg.get("stale_after_hours") if isinstance(cfg, dict) else None
    if not isinstance(table, dict) or kind not in table:
        raise KeyError("stale_after_hours[%r] missing in config" % kind)
    return float(table[kind])


def is_stale(as_of: Optional[str], kind: str, cfg: dict, now: Optional[datetime] = None) -> bool:
    """``True`` if ``as_of`` is older than the configured threshold for ``kind``, missing or
    unparsable."""
    if as_of is None or as_of == "":
        return True
    try:
        age = hours_since(as_of, now)
    except ValueError:
        log("warning: unparsable as_of %r treated as stale" % (as_of,))
        return True
    return age > stale_threshold_hours(kind, cfg)


def _walk_dicts(node: Any):
    """Yield every dict reachable from ``node`` (including ``node`` itself) through dicts/lists."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_dicts(v)


def apply_time_stale(doc: dict, kind: str, cfg: dict, now: Optional[datetime] = None) -> dict:
    """Time-based staleness. Sets ``doc["stale"]`` from its ``as_of`` (OR-ed with an existing
    flag) and, for every dict anywhere under ``doc["latest"]`` that has an ``as_of`` key, that
    dict's ``stale`` the same way. A datapoint may name its own threshold kind via an optional
    ``stale_kind`` key (e.g. weekly series). Mutates and returns ``doc``."""
    doc["stale"] = bool(doc.get("stale", False)) or is_stale(doc.get("as_of"), kind, cfg, now)
    latest = doc.get("latest")
    if latest is not None:
        thresholds = cfg.get("stale_after_hours", {}) if isinstance(cfg, dict) else {}
        for node in _walk_dicts(latest):
            if "as_of" in node:
                dp_kind = node.get("stale_kind")
                if not (isinstance(dp_kind, str) and dp_kind in thresholds):
                    dp_kind = kind
                node["stale"] = bool(node.get("stale", False)) or is_stale(node.get("as_of"), dp_kind, cfg, now)
    return doc


def mark_all_stale(doc: dict) -> dict:
    """Set ``"stale": True`` on the top level and on every dict (any depth) that has ``as_of``.
    Mutates and returns ``doc``."""
    doc["stale"] = True
    for node in _walk_dicts(doc):
        if "as_of" in node:
            node["stale"] = True
    return doc


# -------------------------------------------------------------------------------------- misc

def sha1(s: str) -> str:
    """Hex SHA-1 digest of ``s`` (UTF-8)."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def slug(s: str) -> str:
    """Lowercase ASCII slug: accents stripped, runs of non ``[a-z0-9]`` become single dashes."""
    decomposed = unicodedata.normalize("NFKD", str(s))
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def log(msg: str) -> None:
    """Print ``[HH:MM:SS] msg`` (wall-clock UTC) to stderr. Never stdout."""
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, msg), file=sys.stderr, flush=True)
