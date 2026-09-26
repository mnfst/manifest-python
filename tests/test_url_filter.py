import json
import warnings
from pathlib import Path

from mnfst import manifest
from mnfst.config import resolve_config
from mnfst.url_filter import Rule, is_excluded, parse_rule, pick_rules

# Shared with the Node, PHP and Hermes SDKs: the same file, the same answers.
CASES = json.loads((Path(__file__).parent / "fixtures" / "url-filter.json").read_text())


def test_parses_every_shared_entry_the_same_way():
    for case in CASES["parse"]:
        blank = case["entry"].strip() == ""
        rule = None if blank else parse_rule(case["entry"])
        assert (rule._asdict() if rule else None) == case["rule"], case["entry"]
        assert bool(pick_rules([case["entry"]], None)[1]) == bool(case.get("invalid")), case["entry"]


def test_matches_every_shared_url_the_same_way():
    for case in CASES["match"]:
        allow, _ = pick_rules(case.get("allow"), None)
        deny, _ = pick_rules(case.get("deny"), None)
        assert is_excluded(allow, deny or (), case["url"]) == case["excluded"], case


def test_option_beats_env(monkeypatch):
    monkeypatch.setenv("MNFST_DENYLIST", "env.com")
    monkeypatch.setenv("MNFST_ALLOWLIST", "a.com/v1")
    assert resolve_config().denylist == (Rule("env.com", None),)
    assert resolve_config().allowlist == (Rule("a.com", "/v1"),)
    assert resolve_config(denylist=["opt.com"]).denylist == (Rule("opt.com", None),)
    assert resolve_config(allowlist="", denylist=[" "]) == resolve_config()


def test_no_allowlist_is_none(monkeypatch):
    monkeypatch.delenv("MNFST_ALLOWLIST", raising=False)
    assert resolve_config().allowlist is None


def test_manifest_warns_about_dropped_entries(monkeypatch):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest(allowlist="stripe.com, stripe.com/v1/*")
    assert any("stripe.com/v1/*" in str(w.message) for w in caught)
