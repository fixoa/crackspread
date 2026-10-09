"""fetch_shipping (IMF PortWatch chokepoint transits): fixture-mode output, averages, paging, plausibility."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import common
import fetch_shipping as fs

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def doc(cfg):
    d, info = fs.run(cfg, None, fixtures=True, now=NOW)
    return d, info


def test_fixture_output_validates(doc):
    d, info = doc
    common.validate(d, "shipping")
    assert info["via"] == "portwatch" and info["rows"] > 3000
    assert d["baseline_year"] == 2025 and d["window_days"] == 7
    assert d["as_of"] == "2026-10-04"


def test_hormuz_collapsed_against_2025_baseline(doc):
    d, _ = doc
    h = next(c for c in d["chokepoints"] if c["id"] == "hormuz")
    assert h["latest_date"] == "2026-10-04"
    assert h["tankers_7d"] < 1.0                      # 2026-09-28..10-04: 2,0,4,0,0,0,0 → 0.9
    assert 47.5 < h["tankers_baseline"] < 49.0        # 2025 daily mean ≈ 48.2
    assert h["tankers_change_pct"] <= -95
    assert len(h["series"]["weeks"]) == len(h["series"]["tankers"]) > 80


def test_every_chokepoint_present_in_order(doc):
    d, _ = doc
    assert [c["id"] for c in d["chokepoints"]] == ["hormuz", "bab_el_mandeb", "suez", "cape", "malacca"]
    assert all(c["tankers_baseline"] is not None for c in d["chokepoints"])


def test_missing_chokepoint_yields_nulls_not_zeros():
    rows = [{"date": "2026-10-01", "portid": "chokepoint6", "n_tanker": 3, "n_total": 5, "capacity_tanker": 1, "capacity": 2}]
    d, _ = fs.build(rows, NOW, common.iso_utc(NOW), 2025)
    suez = next(c for c in d["chokepoints"] if c["id"] == "suez")
    assert suez["tankers_7d"] is None and suez["tankers_baseline"] is None and suez["latest_date"] is None
    hormuz = next(c for c in d["chokepoints"] if c["id"] == "hormuz")
    assert hormuz["tankers_7d"] == 3.0 and hormuz["tankers_baseline"] is None and hormuz["tankers_change_pct"] is None
    common.validate(d, "shipping")


def test_no_rows_raises():
    with pytest.raises(common.FetchError):
        fs.build([], NOW, common.iso_utc(NOW), 2025)


def test_implausible_count_rejected():
    rows = [{"date": "2026-10-01", "portid": "chokepoint6", "n_tanker": 99999}]
    with pytest.raises(common.PlausibilityError):
        fs.build(rows, NOW, common.iso_utc(NOW), 2025)


def test_live_path_pages_through_the_service(monkeypatch, cfg):
    """Two pages: the first flagged exceededTransferLimit, the second final. Offsets must advance."""
    calls = []

    class R:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_get(url, *, params=None, **kw):
        calls.append(params["resultOffset"])
        if params["resultOffset"] == 0:
            feats = [{"attributes": {"date": "2025-01-0%d" % (i + 1), "portid": "chokepoint6", "n_tanker": 40}} for i in range(3)]
            return R({"features": feats, "exceededTransferLimit": True})
        return R({"features": [{"attributes": {"date": "2026-10-01", "portid": "chokepoint6", "n_tanker": 0}}]})

    monkeypatch.setattr(common, "http_get", fake_get)
    d, info = fs.run(cfg, None, fixtures=False, now=NOW)
    assert calls == [0, 3] and info["rows"] == 4
    h = next(c for c in d["chokepoints"] if c["id"] == "hormuz")
    assert h["tankers_baseline"] == 40.0 and h["tankers_7d"] == 0.0 and h["tankers_change_pct"] == -100


def test_service_error_is_a_fetch_error(monkeypatch, cfg):
    class R:
        def json(self):
            return {"error": {"code": 400, "message": "bad"}}

    monkeypatch.setattr(common, "http_get", lambda *a, **k: R())
    with pytest.raises(common.FetchError):
        fs.run(cfg, None, fixtures=False, now=NOW)
