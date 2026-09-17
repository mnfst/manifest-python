from mnfst.config import Config
from mnfst.doctor import (
    Check,
    checks,
    mask_key,
    probe_key,
    render,
    run_doctor,
)
from tests.stub_phoenix import StubPhoenix

KEY = "mnfst_proj_abcdefghijklmnopDZDw"


def config(url: str, key: str = KEY) -> Config:
    return Config(api_key=key, base_url=url, on_heal=None)


def test_mask_never_prints_the_whole_key():
    masked = mask_key(KEY)
    assert masked != KEY
    assert KEY not in masked
    assert masked.endswith("DZDw")


def test_short_keys_are_fully_hidden():
    assert mask_key("secret") == "…"


def test_probe_accepts_a_valid_key():
    with StubPhoenix() as stub:
        result = probe_key(config(stub.url))
    assert result.status == "ok"
    assert "Stub project" in result.detail
    assert stub.hellos[0]["runtime"].startswith("python-")


def test_probe_flags_a_rejected_key():
    with StubPhoenix() as stub:
        stub.hello_status = 401
        result = probe_key(config(stub.url))
    assert result.status == "fail"
    assert "rejected" in result.detail


def test_probe_warns_when_the_server_cannot_verify():
    with StubPhoenix() as stub:
        stub.hello_status = 404
        result = probe_key(config(stub.url))
    assert result.status == "warn"


def test_probe_reports_an_unreachable_server():
    result = probe_key(config("http://127.0.0.1:1"))
    assert result.status == "fail"
    assert "cannot reach" in result.detail


def test_all_checks_pass_on_a_healthy_install(monkeypatch):
    with StubPhoenix() as stub:
        monkeypatch.setenv("MNFST_KEY", KEY)
        monkeypatch.setenv("MNFST_URL", stub.url)
        monkeypatch.setattr("mnfst.doctor.installed_config", lambda: object())
        results = checks()
    assert [check.status for check in results] == ["ok", "ok", "ok", "ok"]
    assert "Stub project" in results[2].detail


def test_missing_key_is_a_failure(monkeypatch):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    monkeypatch.setattr("mnfst.doctor.installed_config", lambda: object())
    results = checks()
    assert results[1].status == "fail"
    assert "not set" in results[1].detail


def test_missing_key_skips_the_round_trip(monkeypatch):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    monkeypatch.setattr("mnfst.doctor.installed_config", lambda: object())
    called = []

    def probe(_config):
        called.append(True)
        return Check("ok", "Key valid")

    checks(probe=probe)
    assert called == []


def test_unloaded_sdk_is_a_failure(monkeypatch):
    monkeypatch.setenv("MNFST_KEY", KEY)
    monkeypatch.setattr("mnfst.doctor.installed_config", lambda: None)
    results = checks(probe=lambda _config: Check("ok", "Key valid"))
    assert results[-1].status == "fail"
    assert "mnfst run" in results[-1].detail


def test_render_marks_every_status():
    text = render([Check("ok", "a"), Check("fail", "b"), Check("warn", "c")])
    assert "✅" in text and "❌" in text and "⚠️" in text


def test_run_doctor_exit_code_follows_failures(monkeypatch, capsys):
    monkeypatch.setattr("mnfst.doctor.checks", lambda: [Check("fail", "Key valid")])
    assert run_doctor() == 1
    assert "❌" in capsys.readouterr().out


def test_run_doctor_passes_when_only_warnings(monkeypatch, capsys):
    monkeypatch.setattr(
        "mnfst.doctor.checks",
        lambda: [Check("ok", "SDK installed"), Check("warn", "Key valid")],
    )
    assert run_doctor() == 0
