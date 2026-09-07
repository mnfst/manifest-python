from mnfst.config import resolve_config
from mnfst.heal_api import HealApi
from tests.stub_phoenix import StubPhoenix

PAYLOAD = {"traceId": "t1",
           "request": {"method": "POST", "url": "/workouts", "routeTemplate": None,
                       "body": {"reps": "1e3"}},
           "response": {"statusCode": 422, "body": {"detail": []}, "truncated": False},
           "responseTimeMs": 5}


def test_heal_roundtrip_against_real_socket():
    with StubPhoenix() as stub:
        api = HealApi(resolve_config(api_key="mnfx_k", url=stub.url))
        result = api.heal(PAYLOAD)
        assert result == {"status": "no_patch", "issueId": "stub-issue"}
        assert stub.heals == [PAYLOAD]

        stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                       "healedRequest": {"body": {"reps": 10}}}
        assert api.heal(PAYLOAD)["healedRequest"] == {"body": {"reps": 10}}

        api.report_outcome("a1", 200)
        api.join_pending_reports()
        assert stub.outcomes == [("a1", {"response": {"statusCode": 200}})]


def test_disabled_stub_trips_backoff():
    with StubPhoenix() as stub:
        stub.disabled = True
        api = HealApi(resolve_config(api_key="mnfx_k", url=stub.url))
        assert api.heal(PAYLOAD) is None
        assert api.healing_enabled() is False
