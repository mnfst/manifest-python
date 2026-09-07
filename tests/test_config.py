import pytest

from mnfst.config import HEAL_TIMEOUT_SECONDS, HOSTED_URL, resolve_config


def test_defaults(monkeypatch):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    monkeypatch.delenv("MNFST_URL", raising=False)
    c = resolve_config()
    assert c.api_key is None
    assert c.base_url == HOSTED_URL
    assert c.on_heal is None
    assert HEAL_TIMEOUT_SECONDS == 60.0  # a constant, not configuration


def test_env(monkeypatch):
    monkeypatch.setenv("MNFST_KEY", "mnfx_test_abc")
    monkeypatch.setenv("MNFST_URL", "http://localhost:9911/")
    c = resolve_config()
    assert c.api_key == "mnfx_test_abc"
    assert c.base_url == "http://localhost:9911"  # trailing slash stripped


def test_kwarg_beats_env(monkeypatch):
    monkeypatch.setenv("MNFST_KEY", "mnfx_env")
    monkeypatch.setenv("MNFST_URL", "http://from-env:1234")
    c = resolve_config(api_key="mnfx_kwarg", url="http://x/")
    assert c.api_key == "mnfx_kwarg"
    assert c.base_url == "http://x"


def test_unknown_option_is_an_error():
    # The old kwargs surface silently swallowed typos; the explicit signature
    # makes a removed or misspelled option fail loudly.
    with pytest.raises(TypeError):
        resolve_config(timeout=5)
