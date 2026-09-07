from mnfst.merge import merge_healed_body


def test_full_travel_healed_wins_and_deletes_by_omission():
    original = {"model": "gpt-5", "temperature": 0.2, "messages": [1]}
    traveled = dict(original)
    healed = {"model": "gpt-5", "messages": [1]}  # temperature omitted -> dropped
    assert merge_healed_body(original, traveled, healed) == {"model": "gpt-5", "messages": [1]}


def test_never_traveled_keys_are_restored():
    original = {"secret": "s3cr3t", "reps": "10"}
    traveled = {"reps": "10"}          # strip mode kept 'secret' home
    healed = {"reps": 10}
    assert merge_healed_body(original, traveled, healed) == {"secret": "s3cr3t", "reps": 10}


def test_healed_can_add_new_keys():
    original = {"a": 1}
    assert merge_healed_body(original, {"a": 1}, {"a": 1, "b": 2}) == {"a": 1, "b": 2}


def test_empty_travel_restores_all_untouched_keys():
    assert merge_healed_body({"a": 1, "b": 2}, {}, {"b": 3}) == {"a": 1, "b": 3}


def test_healed_named_key_beats_restore():
    original = {"secret": "old", "a": 1}
    # server named 'secret' even though it didn't travel -> healed value wins
    assert merge_healed_body(original, {"a": 1}, {"a": 1, "secret": "new"}) == {"a": 1, "secret": "new"}


def test_non_object_bodies_are_replaced_wholesale():
    assert merge_healed_body(["a", "bad"], ["a", "bad"], ["a"]) == ["a"]
    assert merge_healed_body({"a": 1}, {"a": 1}, ["now", "a", "list"]) == ["now", "a", "list"]
    assert merge_healed_body("scalar", "scalar", {"obj": True}) == {"obj": True}
