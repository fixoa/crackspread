"""Unit tests for scripts/common.py (offline)."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest
import requests

import common

# Reference values from the brief (real FRED observations for 2026-10-06), not invented.
BRENT_2026_10_06 = 125.44
DIESEL_CRACK_2026_10_06 = 72.51
ULSD_2026_10_06 = 4.713
FETCHED = "2026-10-08T10:00:12Z"


# ------------------------------------------------------------------------------------ time

def test_now_utc_honours_env(monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T10:00:00Z")
    assert common.now_utc() == datetime(2026, 10, 8, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("CRACKSPREAD_NOW")
    real = common.now_utc()
    assert real.tzinfo is not None and real.utcoffset() == timedelta(0)
    assert real.microsecond == 0


def test_iso_parse_round_trip(now):
    assert common.iso_utc(now) == "2026-10-08T10:00:00Z"
    assert common.parse_iso("2026-10-08T10:00:00Z") == now
    assert common.parse_iso(common.iso_utc(now)) == now
    # date-only → midnight UTC
    assert common.parse_iso("2026-10-06") == datetime(2026, 10, 6, tzinfo=timezone.utc)
    # month-only → first of month
    assert common.parse_iso("2026-10") == datetime(2026, 10, 1, tzinfo=timezone.utc)
    # offsets are converted to UTC; naive is taken as UTC
    assert common.parse_iso("2026-10-08T12:00:00+02:00") == now
    assert common.parse_iso("2026-10-08T10:00:00") == now
    assert common.parse_iso("2026-10-08 10:00:00.123456Z") == now.replace(microsecond=123456)
    # iso_utc of a non-UTC aware datetime converts
    vienna = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert common.iso_utc(vienna) == "2026-10-08T10:00:00Z"


def test_iso_utc_default_uses_now(monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T10:00:00Z")
    assert common.iso_utc() == "2026-10-08T10:00:00Z"


def test_now_utc_rejects_bad_env(monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", "yesterday")
    with pytest.raises(ValueError, match="CRACKSPREAD_NOW"):
        common.now_utc()
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T12:00:00+02:00")   # offsets are normalised to UTC
    assert common.iso_utc() == "2026-10-08T10:00:00Z"


@pytest.mark.parametrize("bad", ["", "not a date", "2026-13-45", None, 20261008])
def test_parse_iso_rejects_garbage(bad):
    with pytest.raises(ValueError):
        common.parse_iso(bad)  # type: ignore[arg-type]


def test_hours_since(now):
    assert common.hours_since("2026-10-08T09:00:00Z", now) == pytest.approx(1.0)
    assert common.hours_since("2026-10-06", now) == pytest.approx(58.0)
    assert common.hours_since("2026-10-08T11:00:00Z", now) == pytest.approx(-1.0)
    assert common.hours_since(now - timedelta(minutes=30), now) == pytest.approx(0.5)


# ----------------------------------------------------------------------------------- config

def test_load_config_is_cached_copy(cfg):
    again = common.load_config()
    assert again == cfg
    assert again is not cfg  # a copy: mutating one must not leak into the other
    again["site_name"] = "mutated"
    assert common.load_config()["site_name"] == "Crackspread"


def test_config_validates(cfg):
    common.validate(cfg, "config")
    assert cfg["stale_after_hours"]["prices"] == 96


def test_config_schema_rejects_bad_provider(cfg):
    cfg["ai_provider"] = "openai"
    with pytest.raises(jsonschema.ValidationError):
        common.validate(cfg, "config")


# ------------------------------------------------------------------------------------- HTTP

class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = ""
        self.closed = False

    def close(self):
        self.closed = True


class _StubSession:
    """Replays a scripted sequence of responses / exceptions and records the calls."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def quiet_sleep(monkeypatch):
    delays = []
    monkeypatch.setattr(common, "_sleep", lambda s: delays.append(s))
    return delays


def test_http_get_sets_user_agent_and_returns_2xx(no_network, quiet_sleep):
    http_get = no_network
    sess = _StubSession([_Resp(200)])
    resp = http_get("https://example.test/x", params={"a": 1}, session=sess, headers={"Accept": "text/csv"})
    assert resp.status_code == 200
    url, kw = sess.calls[0]
    assert url == "https://example.test/x"
    assert kw["headers"]["User-Agent"] == common.UA
    assert kw["headers"]["Accept"] == "text/csv"
    assert kw["timeout"] == 20
    assert kw["params"] == {"a": 1}
    assert quiet_sleep == []


def test_http_get_retries_on_5xx_and_connection_errors(no_network, quiet_sleep):
    http_get = no_network
    sess = _StubSession([_Resp(503), requests.exceptions.ConnectionError("boom"), _Resp(200)])
    resp = http_get("https://example.test/y", session=sess)
    assert resp.status_code == 200
    assert len(sess.calls) == 3
    assert quiet_sleep == [1.5, 3.0]  # exponential backoff


def test_http_get_gives_up_after_retries(no_network, quiet_sleep):
    http_get = no_network
    sess = _StubSession([_Resp(500), _Resp(502), _Resp(500)])
    with pytest.raises(common.FetchError) as exc:
        http_get("https://example.test/z", params={"api_key": "SECRET"}, session=sess)
    msg = str(exc.value)
    assert "500" in msg and "https://example.test/z" in msg and "SECRET" not in msg
    assert len(sess.calls) == 3


def test_http_get_timeout_then_fail(no_network, quiet_sleep):
    http_get = no_network
    sess = _StubSession([requests.exceptions.Timeout("slow"), requests.exceptions.Timeout("slow")])
    with pytest.raises(common.FetchError) as exc:
        http_get("https://example.test/t", session=sess, retries=2)
    assert "Timeout" in str(exc.value) and "https://example.test/t" in str(exc.value)
    assert len(sess.calls) == 2


def test_http_get_no_retry_on_4xx(no_network, quiet_sleep):
    http_get = no_network
    sess = _StubSession([_Resp(404), _Resp(200)])
    with pytest.raises(common.FetchError) as exc:
        http_get("https://example.test/missing", session=sess)
    assert "404" in str(exc.value) and "https://example.test/missing" in str(exc.value)
    assert len(sess.calls) == 1
    assert quiet_sleep == []


def test_http_get_never_leaks_credentials(no_network, quiet_sleep, capsys):
    """Keys must not reach FetchError texts (→ meta.json, committed) or stderr (→ Actions log)."""
    http_get = no_network
    # urllib3 repeats the request path incl. query string in its exception text
    boom = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.eia.gov', port=443): Max retries exceeded with url: "
        "/v2/steo/data/?api_key=SECRETKEY123&frequency=monthly (Caused by NewConnectionError(...))")
    sess = _StubSession([boom, boom, boom])
    with pytest.raises(common.FetchError) as exc:
        http_get("https://api.eia.gov/v2/steo/data/", params={"api_key": "SECRETKEY123"}, session=sess)
    msg, err = str(exc.value), capsys.readouterr().err
    assert "SECRETKEY123" not in msg and "SECRETKEY123" not in err
    assert "api_key=***" in msg and "api.eia.gov" in msg
    # Gemini-style key in the URL itself (5xx path and 4xx path)
    url = "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=GEMSECRET&alt=json"
    with pytest.raises(common.FetchError) as exc:
        http_get(url, session=_StubSession([_Resp(500), _Resp(502)]), retries=2)
    msg, err = str(exc.value), capsys.readouterr().err
    assert "GEMSECRET" not in msg and "GEMSECRET" not in err and "key=***&alt=json" in msg
    with pytest.raises(common.FetchError) as exc:
        http_get(url, session=_StubSession([_Resp(403)]))
    assert "GEMSECRET" not in str(exc.value)
    # non-retryable requests exception (e.g. invalid URL) is wrapped and masked too
    with pytest.raises(common.FetchError) as exc:
        http_get(url, session=_StubSession([requests.exceptions.InvalidURL("bad url ?token=TOK123")]))
    assert "TOK123" not in str(exc.value) and "InvalidURL" in str(exc.value)
    assert quiet_sleep == [1.5, 3.0, 1.5]


def test_http_get_closes_failed_responses(no_network, quiet_sleep):
    http_get = no_network
    first, second = _Resp(503), _Resp(200)
    assert http_get("https://example.test/s", session=_StubSession([first, second])) is second
    assert first.closed and not second.closed
    not_found = _Resp(404)
    with pytest.raises(common.FetchError):
        http_get("https://example.test/s", session=_StubSession([not_found]))
    assert not_found.closed


class _StreamResp(_Resp):
    """A 2xx response whose body arrives in chunks (what ``max_bytes`` reads)."""

    def __init__(self, chunks, content_length=None):
        super().__init__(200)
        self.chunks = list(chunks)
        self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}
        self._content = False
        self._content_consumed = False

    def iter_content(self, chunk_size=1):
        for chunk in self.chunks:
            yield chunk

    @property
    def content(self):
        return self._content


def test_http_get_max_bytes_bounds_the_body(no_network, quiet_sleep):
    http_get = no_network
    ok = _StreamResp([b"a" * 1000, b"b" * 1000])
    sess = _StubSession([ok])
    resp = http_get("https://example.test/big.csv", session=sess, max_bytes=5000)
    assert resp is ok and resp.content == b"a" * 1000 + b"b" * 1000 and resp._content_consumed is True
    assert sess.calls[0][1]["stream"] is True                       # max_bytes implies streaming
    # actual body over the limit: FetchError, response closed, no retry
    big = _StreamResp([b"x" * 4000, b"y" * 4000])
    with pytest.raises(common.FetchError, match="too large") as exc:
        http_get("https://example.test/big.csv?api_key=SECRET", session=_StubSession([big]), max_bytes=5000)
    assert big.closed and "SECRET" not in str(exc.value) and quiet_sleep == []
    # announced size over the limit: rejected before reading
    announced = _StreamResp([b"z"], content_length=10 ** 9)
    with pytest.raises(common.FetchError, match="announced"):
        http_get("https://example.test/zip", session=_StubSession([announced]), max_bytes=5000)
    assert announced.closed
    # without max_bytes nothing changes (no stream, body untouched)
    plain = _Resp(200)
    assert http_get("https://example.test/x", session=_StubSession([plain])) is plain


def test_no_network_guard_blocks_requests():
    with pytest.raises(AssertionError, match="network call in test"):
        common.http_get("https://example.test/")
    with pytest.raises(AssertionError, match="network call in test"):
        common.http_post("https://example.test/", json={})
    with pytest.raises(AssertionError, match="network call in test"):
        requests.get("https://example.test/")
    with pytest.raises(AssertionError, match="network call in test"):
        requests.Session().get("https://example.test/")


# ------------------------------------------------------------------------------ http_post

#: The real function, captured at import time (the autouse ``no_network`` guard replaces the
#: module attribute before each test, so tests go through this reference instead).
_REAL_HTTP_POST = common.http_post


def _real_http_post():
    return _REAL_HTTP_POST


def test_http_post_sends_json_with_ua_and_content_type(no_network, quiet_sleep):
    post = _real_http_post()
    sess = _StubSession([_Resp(200)])
    resp = post("https://llm.test/v1", json={"q": 1}, headers={"x-api-key": "K"}, session=sess)
    assert resp.status_code == 200
    method, url, kw = sess.calls[0]
    assert method == "POST" and url == "https://llm.test/v1"
    assert kw["json"] == {"q": 1}
    assert kw["headers"]["User-Agent"] == common.UA
    assert kw["headers"]["Content-Type"] == "application/json"
    assert kw["headers"]["x-api-key"] == "K"
    assert kw["timeout"] == 60
    assert quiet_sleep == []


def test_http_post_retries_on_429_and_5xx_then_succeeds(no_network, quiet_sleep):
    post = _real_http_post()
    sess = _StubSession([_Resp(429), _Resp(200)])
    assert post("https://llm.test/v1", json={}, session=sess).status_code == 200
    assert len(sess.calls) == 2 and quiet_sleep == [2.0]
    sess = _StubSession([_Resp(503), requests.exceptions.Timeout("slow"), _Resp(200)])
    assert post("https://llm.test/v1", json={}, session=sess, retries=3).status_code == 200
    assert len(sess.calls) == 3


def test_http_post_no_retry_on_4xx_and_body_in_error(no_network, quiet_sleep):
    post = _real_http_post()
    bad = _Resp(400)
    bad.text = '{"error": {"message": "API key not valid. key=SECRET123 (got HDRSECRET99)"}}'
    with pytest.raises(common.FetchError) as exc:
        post("https://llm.test/v1?key=GEMSECRET", json={}, session=_StubSession([bad, _Resp(200)]),
             headers={"x-api-key": "HDRSECRET99", "Authorization": "Bearer BEARERSECRET7"})
    msg = str(exc.value)
    assert "400" in msg and "API key not valid" in msg
    assert "SECRET123" not in msg and "GEMSECRET" not in msg and "key=***" in msg
    assert "HDRSECRET99" not in msg, "a key echoed by the server in its error body must be masked"
    assert bad.closed and quiet_sleep == []
    # a key echoed in an exception text is masked too (urllib3 repeats request details)
    boom = requests.exceptions.ConnectionError("pool error while sending Authorization: Bearer BEARERSECRET7")
    with pytest.raises(common.FetchError) as exc:
        post("https://llm.test/v1", json={}, session=_StubSession([boom, boom]),
             headers={"Authorization": "Bearer BEARERSECRET7"})
    assert "BEARERSECRET7" not in str(exc.value) and "ConnectionError" in str(exc.value)


def test_http_post_gives_up_after_attempts(no_network, quiet_sleep):
    post = _real_http_post()
    with pytest.raises(common.FetchError) as exc:
        post("https://llm.test/v1", json={}, session=_StubSession([_Resp(500), _Resp(429)]))
    assert "429" in str(exc.value) and "2 attempts" in str(exc.value)
    assert quiet_sleep == [2.0]


def test_no_network_guard_blocks_other_libraries():
    """feedparser uses urllib's OpenerDirector, openpyxl/anything else may use raw sockets."""
    import socket
    import urllib.request

    import feedparser

    with pytest.raises(AssertionError, match="network call in test"):
        urllib.request.urlopen("http://127.0.0.1:9/")
    with pytest.raises(AssertionError, match="network call in test"):
        urllib.request.build_opener().open("http://127.0.0.1:9/")
    with pytest.raises(AssertionError, match="network call in test"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    with pytest.raises(AssertionError, match="network call in test"):
        feedparser.parse("http://127.0.0.1:9/feed.xml")
    # parsing a string/bytes feed is still fine offline
    assert feedparser.parse("<rss><channel><title>t</title></channel></rss>").feed.title == "t"


# ------------------------------------------------------------------------------------- JSON

def test_read_json_missing_and_broken(tmp_data_dir, capsys):
    assert common.read_json(tmp_data_dir / "nope.json") is None
    broken = tmp_data_dir / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert common.read_json(broken) is None
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean
    assert "broken.json" in captured.err


def test_write_json_atomic_is_stable(tmp_data_dir):
    path = tmp_data_dir / "x.json"
    common.write_json_atomic(path, {"b": 1, "a": {"d": "ü", "c": [1, 2]}})
    text = path.read_text(encoding="utf-8")
    assert text == '{\n "a": {\n  "c": [\n   1,\n   2\n  ],\n  "d": "ü"\n },\n "b": 1\n}\n'
    assert not (tmp_data_dir / "x.json.tmp").exists()
    assert common.read_json(path) == {"a": {"c": [1, 2], "d": "ü"}, "b": 1}


def test_strip_keys_recursive():
    obj = {"generated_at": "x", "latest": {"brent": {"fetched_at": "y", "value": 1}}, "items": [{"run_id": 1, "k": 2}]}
    stripped = common.strip_keys(obj, ("generated_at", "fetched_at", "run_id"))
    assert stripped == {"latest": {"brent": {"value": 1}}, "items": [{"k": 2}]}
    assert "generated_at" in obj  # original untouched


def test_write_json_if_changed(tmp_data_dir):
    path = tmp_data_dir / "prices.json"
    doc = {"schema_version": 1, "generated_at": "2026-10-08T10:00:00Z",
           "latest": {"brent": {"value": BRENT_2026_10_06, "fetched_at": "2026-10-08T10:00:00Z"}}}
    assert common.write_json_if_changed(path, doc) is True
    first = path.read_text(encoding="utf-8")

    # only ignored keys differ → no write, old timestamps kept
    newer = copy.deepcopy(doc)
    newer["generated_at"] = "2026-10-08T17:00:00Z"
    newer["latest"]["brent"]["fetched_at"] = "2026-10-08T17:00:00Z"
    assert common.write_json_if_changed(path, newer) is False
    assert path.read_text(encoding="utf-8") == first

    # a real change → written as-is (including the new timestamps)
    newer["latest"]["brent"]["value"] = BRENT_2026_10_06 + 1
    assert common.write_json_if_changed(path, newer) is True
    on_disk = common.read_json(path)
    assert on_disk["generated_at"] == "2026-10-08T17:00:00Z"
    assert on_disk["latest"]["brent"]["value"] == pytest.approx(BRENT_2026_10_06 + 1)


# ---------------------------------------------------------------------------------- schemas

def test_manual_json_validates():
    """manual.json is hand-edited on GitHub; only the schema is enforced here so that an edit by
    the owner cannot block the deploy over the envelope convention (§3.7)."""
    doc = common.read_json(common.DATA / "manual.json")
    assert doc is not None
    common.validate(doc, "manual")
    assert set(doc) >= {"updated_at", "ledger_label", "default_source", "world", "hormuz_ledger", "shipping",
                        "products_mbd", "refinery_shock", "diesel_math", "impacts"}
    assert doc["ledger_label"].startswith("Whiteboard (Max Fisher's estimate")
    assert [row["id"] for row in doc["hormuz_ledger"]] == ["hormuz_normal", "iran_blockade", "uae_pipeline", "sts_oman",
                                                           "saudi_eastwest", "region_total", "spr", "china_cuts", "net_shortfall"]
    assert doc["stale"] is False


SCHEMA_NAMES = ("config", "prices", "balance", "countries", "news", "summary", "proposals", "manual", "meta", "shipping", "crosscheck")


def test_load_schema_names():
    assert common.load_schema("prices")["title"] == "prices.json"
    assert common.load_schema("prices.schema.json")["title"] == "prices.json"
    for name in SCHEMA_NAMES:
        schema = common.load_schema(name)
        assert schema["$schema"].endswith("2020-12/schema")


def test_every_schema_file_is_a_valid_draft_2020_12_schema():
    on_disk = {p.name for p in common.SCHEMAS.glob("*.schema.json")}
    assert on_disk == {"%s.schema.json" % n for n in SCHEMA_NAMES}
    for name in SCHEMA_NAMES:
        schema = common.load_schema(name)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        with pytest.raises(jsonschema.ValidationError):   # the validator actually runs
            common.validate([], name)


def _prices_doc():
    dp = common.datapoint(BRENT_2026_10_06, "USD/bbl", "2026-10-06", "FRED DCOILBRENTEU",
                          "https://fred.stlouisfed.org/series/DCOILBRENTEU", fetched_at=FETCHED, series_id="DCOILBRENTEU")
    crack = common.datapoint(DIESEL_CRACK_2026_10_06, "USD/bbl", "2026-10-06", "FRED (EIA data), DDFUELNYH & DCOILBRENTEU",
                             "https://fred.stlouisfed.org/series/DDFUELNYH", fetched_at=FETCHED,
                             formula="DDFUELNYH*42 - DCOILBRENTEU",
                             inputs={"DDFUELNYH": ULSD_2026_10_06, "DCOILBRENTEU": BRENT_2026_10_06})
    keys = ["brent", "wti", "ulsd_nyh", "gasoline_nyh", "jet_gulf", "heating_oil_nyh", "retail_diesel_us",
            "retail_gasoline_us", "diesel_crack", "gasoline_crack", "jet_crack", "crack_321", "brent_wti_spread"]
    latest = {k: copy.deepcopy(crack if "crack" in k or k == "crack_321" else dp) for k in keys}
    return {
        "schema_version": 1, "generated_at": FETCHED, "as_of": "2026-10-06", "fetched_at": FETCHED, "stale": False,
        "source": "FRED, Federal Reserve Bank of St. Louis; data: U.S. EIA", "source_url": "https://fred.stlouisfed.org/",
        "latest": latest,
        "history": {"dates": ["2026-10-06"], "diesel_crack": [DIESEL_CRACK_2026_10_06], "gasoline_crack": [None],
                    "jet_crack": [None], "crack_321": [None], "brent": [BRENT_2026_10_06], "wti": [None],
                    "resolution_note": "weekly before 2025-10-08, daily after"},
        "stats": {"diesel_crack": {"max": 116.5, "max_date": "2022-05-11", "mean_2015_2019": 16.0,
                                   "percentile_now": 99.0, "level": 4, "level_label": "Grand Canyon"}},
    }


def test_prices_schema_accepts_valid_doc():
    common.validate(_prices_doc(), "prices")


def test_datapoint_missing_source_fails_prices_schema():
    doc = _prices_doc()
    del doc["latest"]["brent"]["source"]
    with pytest.raises(jsonschema.ValidationError) as exc:
        common.validate(doc, "prices")
    assert "source" in exc.value.message


@pytest.mark.parametrize("mutate", [
    lambda d: d["latest"]["brent"].update(value="125.44"),         # value must be number|null
    lambda d: d["latest"]["brent"].update(stale="no"),             # stale must be boolean
    lambda d: d["latest"]["brent"].update(as_of=20261006),         # as_of must be a string
    lambda d: d["latest"]["brent"].update(source_url="fred.stlouisfed.org"),  # uri-ish
    lambda d: d["latest"].pop("crack_321"),                        # required series
    lambda d: d["history"]["dates"].append("06.10.2026"),          # date pattern
    lambda d: d["stats"]["diesel_crack"].update(level=7),          # level 0..4
    lambda d: d.pop("history"),
    lambda d: d.update(schema_version=2),
])
def test_prices_schema_rejects_bad_shapes(mutate):
    doc = _prices_doc()
    mutate(doc)
    with pytest.raises(jsonschema.ValidationError):
        common.validate(doc, "prices")


def test_prices_schema_allows_null_value_and_extras():
    doc = _prices_doc()
    doc["latest"]["retail_diesel_us"]["value"] = None
    doc["latest"]["retail_diesel_us"]["note"] = "weekly series"
    doc["latest"]["retail_diesel_us"]["stale_kind"] = "retail"
    doc["extra_section"] = {"anything": True}
    common.validate(doc, "prices")


def test_datapoint_builder(monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T10:00:00Z")
    dp = common.datapoint(None, "USD/gal", "2026-10-06", "FRED", "https://fred.stlouisfed.org/series/DDFUELNYH", note="n/a")
    assert dp == {"value": None, "unit": "USD/gal", "as_of": "2026-10-06", "source": "FRED",
                  "source_url": "https://fred.stlouisfed.org/series/DDFUELNYH",
                  "fetched_at": "2026-10-08T10:00:00Z", "stale": False, "note": "n/a"}
    assert common.datapoint(1, "u", "2026-10-06", "s", "https://x", fetched_at=FETCHED, stale=True)["fetched_at"] == FETCHED


# ------------------------------------------------------------------------------ plausibility

def test_ensure_range():
    assert common.ensure_range(BRENT_2026_10_06, 5, 400, "brent") == pytest.approx(BRENT_2026_10_06)
    assert common.ensure_range("4.713", 0.3, 15, "ulsd") == pytest.approx(ULSD_2026_10_06)
    assert common.ensure_range(5, 5, 400, "lo-edge") == 5.0
    for bad in (401, 4.9, None, "abc", float("nan")):
        with pytest.raises(common.PlausibilityError):
            common.ensure_range(bad, 5, 400, "brent")
    assert issubclass(common.PlausibilityError, ValueError)
    with pytest.raises(ValueError):
        common.ensure_range(-1, 0, 1, "x")


# ------------------------------------------------------------------------------------ stale

def test_stale_threshold_hours(cfg):
    assert common.stale_threshold_hours("prices", cfg) == 96
    assert common.stale_threshold_hours("steo", cfg) == 1080
    with pytest.raises(KeyError):
        common.stale_threshold_hours("nope", cfg)


def test_is_stale_thresholds(cfg, now):
    # prices: 96 h → 2026-10-06 (58 h old) fresh, 2026-10-03 (5 days) stale
    assert common.is_stale("2026-10-06", "prices", cfg, now) is False
    assert common.is_stale("2026-10-03", "prices", cfg, now) is True
    assert common.is_stale("2026-10-04T10:00:00Z", "prices", cfg, now) is False   # exactly 96 h: not yet stale
    assert common.is_stale("2026-10-04T09:59:59Z", "prices", cfg, now) is True
    # news: 24 h
    assert common.is_stale("2026-10-07T11:00:00Z", "news", cfg, now) is False
    assert common.is_stale("2026-10-07T09:00:00Z", "news", cfg, now) is True
    # steo: 45 days
    assert common.is_stale("2026-09-01", "steo", cfg, now) is False
    assert common.is_stale("2026-08-01", "steo", cfg, now) is True
    # missing / unparsable → stale
    assert common.is_stale(None, "prices", cfg, now) is True
    assert common.is_stale("", "prices", cfg, now) is True
    assert common.is_stale("yesterday-ish", "prices", cfg, now) is True
    # defaults to now_utc() when now is omitted
    assert common.is_stale("2000-01-01", "country", cfg) is True


def test_apply_time_stale_prices_like(cfg, now):
    doc = _prices_doc()
    doc["latest"]["retail_diesel_us"]["as_of"] = "2026-10-01"           # older than 96 h → stale
    doc["latest"]["wti"]["stale"] = True                                # already flagged stays flagged
    doc["latest"]["retail_gasoline_us"]["as_of"] = "2026-10-01"
    doc["latest"]["retail_gasoline_us"]["stale_kind"] = "steo"          # per-datapoint threshold override
    out = common.apply_time_stale(doc, "prices", cfg, now)
    assert out is doc
    assert doc["stale"] is False                                        # top-level as_of 2026-10-06 is fresh
    assert doc["latest"]["brent"]["stale"] is False
    assert doc["latest"]["retail_diesel_us"]["stale"] is True
    assert doc["latest"]["wti"]["stale"] is True
    assert doc["latest"]["retail_gasoline_us"]["stale"] is False
    common.validate(doc, "prices")

    # recursion: dicts at any depth under "latest" with an as_of key are flagged; others untouched
    nested = {"as_of": "2026-10-06", "latest": {"group": {"inner": {"value": 1, "as_of": "2026-09-01"},
                                                          "no_as_of": {"value": 2},
                                                          "rows": [{"value": 3, "as_of": "2026-10-08"}]}}}
    common.apply_time_stale(nested, "prices", cfg, now)
    assert nested["stale"] is False
    assert nested["latest"]["group"]["inner"]["stale"] is True
    assert "stale" not in nested["latest"]["group"]["no_as_of"]
    assert nested["latest"]["group"]["rows"][0]["stale"] is False

    old = {"schema_version": 1, "as_of": "2026-10-01", "stale": False}
    assert common.apply_time_stale(old, "prices", cfg, now)["stale"] is True
    flagged = {"as_of": "2026-10-08", "stale": True}
    assert common.apply_time_stale(flagged, "prices", cfg, now)["stale"] is True
    assert common.apply_time_stale({"schema_version": 1}, "news", cfg, now)["stale"] is True  # no as_of


def test_weekly_stale_kind_for_retail_series(cfg):
    """FRED GASDESW/GASREGW are Monday-dated and published with a lag: a datapoint that carries
    stale_kind="weekly" stays fresh for 7 days plus the 96 h FRED allowance (264 h)."""
    assert common.stale_threshold_hours("weekly", cfg) == 264
    saturday = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    assert common.is_stale("2026-10-05", "prices", cfg, saturday) is True
    assert common.is_stale("2026-10-05", "weekly", cfg, saturday) is False
    doc = _prices_doc()
    doc["latest"]["retail_diesel_us"].update(as_of="2026-10-05", stale_kind="weekly")
    doc["latest"]["retail_gasoline_us"].update(as_of="2026-10-05")               # no override → prices rule
    common.apply_time_stale(doc, "prices", cfg, saturday)
    assert doc["latest"]["retail_diesel_us"]["stale"] is False
    assert doc["latest"]["retail_gasoline_us"]["stale"] is True
    assert doc["latest"]["brent"]["stale"] is True                               # 2026-10-06 is 106 h old
    assert doc["stale"] is True
    common.validate(doc, "prices")


def test_mark_all_stale():
    doc = _prices_doc()
    doc["stats"]["brent_wti_spread"] = {"value": 29.2, "as_of": "2026-10-06", "mean_2015_2019": 3.9}
    doc["items"] = [{"as_of": "2026-10-06", "stale": False}, {"title": "no as_of"}]
    out = common.mark_all_stale(doc)
    assert out is doc
    assert doc["stale"] is True
    assert all(dp["stale"] is True for dp in doc["latest"].values())
    assert doc["stats"]["brent_wti_spread"]["stale"] is True
    assert doc["items"][0]["stale"] is True
    assert "stale" not in doc["items"][1]
    assert doc["fetched_at"] == FETCHED  # timestamps untouched
    common.validate(doc, "prices")


# ------------------------------------------------------------------------------------- misc

def test_sha1_and_slug():
    assert common.sha1("abc") == "a9993e364706816aba3e25717850c26c9cd0d89d"
    assert common.slug("Strait of Hormuz!") == "strait-of-hormuz"
    assert common.slug("  Bab al-Mandab / Red Sea ") == "bab-al-mandab-red-sea"
    assert common.slug("Habshan–Fujairah über") == "habshan-fujairah-uber"


def test_log_goes_to_stderr(capsys):
    common.log("hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.endswith("] hello\n")
    assert captured.err[0] == "[" and captured.err[9] == "]"


def test_other_schemas_reject_wrong_shapes():
    envelope = {"schema_version": 1, "generated_at": FETCHED, "as_of": FETCHED, "fetched_at": FETCHED,
                "stale": False, "source": "x", "source_url": "https://example.test/"}
    news = dict(envelope, items=[], feeds=[])
    common.validate(news, "news")
    news["items"] = [{"id": "abc", "title": "t", "source": "s", "link": "https://x", "published": FETCHED,
                      "snippet": "", "topics": [], "feed": "f"}]
    with pytest.raises(jsonschema.ValidationError):  # id must be a sha1 hex digest
        common.validate(news, "news")
    news["items"][0]["id"] = common.sha1("https://x")
    common.validate(news, "news")
    news["items"][0]["snippet"] = "x" * 241
    with pytest.raises(jsonschema.ValidationError):
        common.validate(news, "news")

    bullet = {"text": "t", "sources": ["https://x"]}
    summary = dict(envelope, provider="none", model="", fallback=True, headline="h", what_changed=[bullet] * 3,
                   quips=["no digits here"], crack_o_meter={"level": 4, "label": "Grand Canyon"})
    common.validate(summary, "summary")
    summary["quips"] = ["has 2 digits"]
    with pytest.raises(jsonschema.ValidationError):
        common.validate(summary, "summary")
    summary["quips"] = []
    for bad in ({"what_changed": []}, {"what_changed": [bullet] * 2}, {"what_changed": [bullet] * 6}, {"headline": ""}):
        with pytest.raises(jsonschema.ValidationError):      # 3-5 bullets, non-empty headline (brief §6.5)
            common.validate(dict(summary, **bad), "summary")

    proposals = dict(envelope, proposals=[])
    common.validate(proposals, "proposals")
    proposals["proposals"] = [{"target": "shipping.vlcc_day_rate_usd.now", "proposed_value": 1, "quote": "q",
                               "source_url": "https://x"}]
    common.validate(proposals, "proposals")
    proposals["proposals"][0]["target"] = "something.else"
    with pytest.raises(jsonschema.ValidationError):
        common.validate(proposals, "proposals")

    meta = {"schema_version": 1, "last_run": FETCHED, "run_id": "20261008-100000-0a1b2c",
            "sources": {"fred": {"ok": True, "last_success": FETCHED, "error": None, "stale": False}}}
    common.validate(meta, "meta")
    meta["sources"]["fred"]["ok"] = "yes"
    with pytest.raises(jsonschema.ValidationError):
        common.validate(meta, "meta")

    balance = dict(envelope, via="api", steo_release="2026-10-06", current_month="2026-10", months=[], quarters=[],
                   current=None, quote=None, status="deficit", unit="million barrels per day")
    common.validate(balance, "balance")
    balance["status"] = "shortage"
    with pytest.raises(jsonschema.ValidationError):
        common.validate(balance, "balance")

    countries = dict(envelope, via="jodi", unit="thousand barrels per day",
                     producers={"year": None, "rows": []}, consumers={"year": None, "rows": []}, monthly_crude=None)
    common.validate(countries, "countries")
    countries["producers"]["rows"] = [{"iso3": "USA", "name": "United States", "value": "n/a"}]
    with pytest.raises(jsonschema.ValidationError):
        common.validate(countries, "countries")
