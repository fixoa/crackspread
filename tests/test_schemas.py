"""Every data file in site/data/ validates against its schema; the config validates; every schema
is itself a valid JSON Schema (draft 2020-12). Files that do not exist yet are skipped (they are
produced by other modules / the first real run), so this test stays green while the repo grows.
"""
from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

import common

#: site/data/<file> → scripts/schemas/<schema>.schema.json
FILE_TO_SCHEMA = {
    "prices.json": "prices",
    "balance.json": "balance",
    "countries.json": "countries",
    "shipping.json": "shipping",
    "news.json": "news",
    "summary.json": "summary",
    "proposals.json": "proposals",
    "manual.json": "manual",
    "meta.json": "meta",
}

ALL_SCHEMAS = sorted(set(FILE_TO_SCHEMA.values()) | {"config"})


@pytest.mark.parametrize("filename", sorted(FILE_TO_SCHEMA))
def test_data_file_validates(filename: str):
    path = common.DATA / filename
    if not path.exists():
        pytest.skip("%s does not exist yet" % path.relative_to(common.ROOT))
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    common.validate(doc, FILE_TO_SCHEMA[filename])
    assert doc["schema_version"] == 1


def test_no_unmapped_data_files():
    """A new data file must get a schema and an entry in FILE_TO_SCHEMA."""
    if not common.DATA.exists():
        pytest.skip("site/data does not exist yet")
    unmapped = sorted(p.name for p in common.DATA.glob("*.json") if p.name not in FILE_TO_SCHEMA)
    assert unmapped == [], "data files without a schema mapping: %s" % unmapped


def test_config_validates(cfg):
    common.validate(cfg, "config")
    assert cfg["ai_provider"] in ("gemini", "anthropic", "none")
    for kind in ("prices", "steo", "news", "country", "summary"):
        assert common.stale_threshold_hours(kind, cfg) > 0


@pytest.mark.parametrize("name", ALL_SCHEMAS)
def test_schema_file_is_valid_draft_2020_12(name: str):
    schema = common.load_schema(name)
    Draft202012Validator.check_schema(schema)
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert schema.get("type") == "object"


def test_manual_json_is_the_whiteboard_ledger():
    """manual.json is hand-maintained and must stay labelled as an estimate (brief §6.6)."""
    path = common.DATA / "manual.json"
    if not path.exists():
        pytest.skip("site/data/manual.json does not exist yet")
    doc = common.read_json(path)
    assert doc is not None
    common.validate(doc, "manual")
    assert "not live data" in doc["ledger_label"]
    assert doc["default_source"]["kind"] == "estimate"
    assert doc["as_of"] == doc["updated_at"]
