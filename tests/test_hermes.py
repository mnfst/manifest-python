"""The Hermes plugin as the runtime fires it (model_tools.handle_function_call):
tool_request middleware -> dispatch -> post_tool_call -> transform_tool_result."""
from importlib import metadata

import pytest

from mnfst import hermes as plugin
from mnfst.config import resolve_config
from mnfst.heal_api import HealApi
from mnfst.hermes.heal import RETRY_LINE, Healer
from tests.helpers import wait_for
from tests.stub_phoenix import StubPhoenix

PATCHED = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
           "healedRequest": {"body": {"sort": "created_at"}}}


def fake_tool(args):  # rejects anything but sort=created_at
    return '{"ok": true}' if args.get("sort") == "created_at" else '{"error": "invalid sort"}'


def hermes_call(callbacks, tool_name, args):
    swap = callbacks["tool_request"](tool_name=tool_name, args=dict(args), original_args=dict(args))
    effective = swap["args"] if swap else args
    result = fake_tool(effective)
    status = "error" if '"error"' in result else "ok"
    error = "invalid sort" if status == "error" else None
    callbacks["post_tool_call"](tool_name=tool_name, args=effective, result=result,
                                status=status, error_message=error)
    replaced = callbacks["transform_tool_result"](tool_name=tool_name, args=effective, result=result,
                                                  status=status, error_message=error)
    return replaced if isinstance(replaced, str) else result


def make_callbacks(stub):
    healer = Healer(HealApi(resolve_config(api_key="mnfx_k", url=stub.url)), timeout=5.0)
    return healer, plugin.build_callbacks(healer)


class FakeContext:
    def __init__(self):
        self.hooks = []
        self.middleware = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))


def test_reject_then_repaired_retry():
    with StubPhoenix(PATCHED) as stub:
        healer, callbacks = make_callbacks(stub)

        first = hermes_call(callbacks, "list_issues", {"sort": "occurrence_count"})
        assert first.endswith(RETRY_LINE.format(tool="list_issues"))
        assert stub.heals[0]["request"]["url"] == "mcp://list_issues"
        assert stub.heals[0]["response"] == {"statusCode": 422, "body": {"error": "invalid sort"},
                                             "truncated": False}

        second = hermes_call(callbacks, "list_issues", {"sort": "occurrence_count"})  # retried as told
        assert second == '{"ok": true}'

        assert wait_for(lambda: len(stub.outcomes) == 1)
        assert len(stub.heals) == 1
        assert stub.outcomes == [("a1", {"response": {"statusCode": 200}})]

        # the patched args failing again are never re-healed
        assert healer.on_error("list_issues", {"sort": "created_at"}, "still bad") is None
        assert len(stub.heals) == 1


def test_no_patch_passes_the_error_through():
    with StubPhoenix() as stub:
        _healer, callbacks = make_callbacks(stub)
        out = hermes_call(callbacks, "list_issues", {"sort": "x"})
        assert out == '{"error": "invalid sort"}'
        assert callbacks["tool_request"](tool_name="list_issues", args={"sort": "x"}) is None


def test_failed_retry_reports_the_error():
    with StubPhoenix({"status": "patched", "issueId": "i1", "healAttemptId": "a2",
                      "healedRequest": {"body": {"sort": "still_wrong"}}}) as stub:
        _healer, callbacks = make_callbacks(stub)
        hermes_call(callbacks, "list_issues", {"sort": "occurrence_count"})
        assert hermes_call(callbacks, "list_issues", {"sort": "occurrence_count"}) == \
            '{"error": "invalid sort"}'
        assert wait_for(lambda: len(stub.outcomes) == 1)
        attempt_id, body = stub.outcomes[0]
        assert attempt_id == "a2"
        assert body == {"response": {"statusCode": 422, "body": {"error": "invalid sort"},
                                     "truncated": False}}


def test_capture_withholds_credential_named_args():
    with StubPhoenix() as stub:
        healer, _callbacks = make_callbacks(stub)
        healer.on_error("list_issues", {"sort": "x", "token": "secret-value"}, "invalid sort")
        assert stub.heals[0]["request"]["body"] == {"sort": "x"}


def test_register_wires_the_three_seams(monkeypatch):
    monkeypatch.setenv("MNFST_KEY", "mnfx_k")
    monkeypatch.setenv("MNFST_HEAL_HTTP", "0")
    ctx = FakeContext()
    plugin.register(ctx)
    assert [name for name, _ in ctx.hooks] == ["transform_tool_result", "post_tool_call"]
    assert [kind for kind, _ in ctx.middleware] == ["tool_request"]


def test_register_without_a_key_does_nothing(monkeypatch):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    monkeypatch.setenv("MNFST_HEAL_HTTP", "0")
    ctx = FakeContext()
    plugin.register(ctx)
    assert ctx.hooks == [] and ctx.middleware == []


def test_entry_point_is_declared():
    try:
        metadata.version("mnfst")
    except metadata.PackageNotFoundError:
        pytest.skip("entry points require an installed package")
    entry_points = [ep for ep in metadata.entry_points(group="hermes_agent.plugins")
                    if ep.name == "manifest"]
    assert len(entry_points) == 1
    assert callable(getattr(entry_points[0].load(), "register", None))
