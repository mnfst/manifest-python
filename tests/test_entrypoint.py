import warnings

import pytest

import mnfst as mnfst_pkg
from mnfst import HealEvent, manifest
from mnfst.outbound import installed_config, uninstall_outbound


@pytest.fixture(autouse=True)
def cleanup():
    yield
    uninstall_outbound()


def test_no_key_warns_and_is_inert(monkeypatch):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest()
    assert any("MNFST_KEY" in str(w.message) for w in caught)
    assert installed_config() is None  # nothing was patched


def test_installs_outbound():
    manifest(key="mnfx_k", url="http://first.test")
    config = installed_config()
    assert config is not None
    assert config.api_key == "mnfx_k"


def test_takes_no_positional_app():
    # Inbound was removed from v1; passing an app must fail loudly, not be
    # silently ignored.
    with pytest.raises(TypeError):
        manifest(object())  # type: ignore[call-arg]


def test_second_install_with_different_config_warns():
    manifest(key="mnfx_k", url="http://first.test")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest(key="mnfx_k", url="http://second.test")
    assert any("restart" in str(w.message) for w in caught)


def test_second_install_with_same_config_is_quiet():
    manifest(key="mnfx_k", url="http://first.test")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest(key="mnfx_k", url="http://first.test")
    assert [str(w.message) for w in caught] == []


def test_exports():
    assert mnfst_pkg.HealEvent is HealEvent
    assert not hasattr(mnfst_pkg, "autofix")
    assert mnfst_pkg.__all__ == ["manifest", "HealEvent", "VERSION"]
