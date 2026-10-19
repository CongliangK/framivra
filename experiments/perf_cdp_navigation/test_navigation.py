"""Explicit ordinary-message tests. No browser, socket, filesystem or discovery."""

import itertools
import unittest

from experiments.perf_cdp_navigation.navigation import (
    LEGACY_EXPRESSION, NavigationWait, State,
)


URL = "http://example.invalid/scene.html?f=4"


def frame_event(loader="loader-4", url=URL, frame="main", **extra):
    return {"method": "Page.frameNavigated", "params": {"frame": {
        "id": frame, "loaderId": loader, "url": url, **extra}}}


def load_event(loader="loader-4", frame="main", name="load"):
    return {"method": "Page.lifecycleEvent", "params": {
        "frameId": frame, "loaderId": loader, "name": name, "timestamp": 12.0}}


def nav_response(**extra):
    return {"id": 11, "result": {"frameId": "main", "loaderId": "loader-4", **extra}}


def check_response(value="?f=4|complete"):
    return {"id": 12, "result": {"result": {"type": "string", "value": value}}}


def fetch_event(request_id="resource-1"):
    return {"method": "Fetch.requestPaused", "params": {
        "requestId": request_id, "frameId": "main",
        "request": {"url": "http://example.invalid/asset.png"}}}


class NavigationTests(unittest.TestCase):
    def model(self, **overrides):
        options = dict(generation=7, target="tab-a", frame="main", url=URL,
                       navigate_id=11, check_id=12, event_wait=1.0)
        options.update(overrides)
        self.now = 10.0
        model = NavigationWait(**options)
        start = model.start(self.now)
        self.assertEqual(start[0].message,
                         {"id": options["navigate_id"], "method": "Page.navigate",
                          "params": {"url": options["url"]}})
        return model

    def emit(self, model, message, **envelope):
        self.now += 0.01
        options = dict(generation=model.generation, target=model.target, now=self.now)
        options.update(envelope)
        return model.receive(message, **options)

    def complete_events(self, model):
        self.emit(model, nav_response())
        self.emit(model, frame_event())
        return self.emit(model, load_event())

    def assert_no_send(self, actions):
        self.assertFalse(any(a.kind == "send" for a in actions), actions)

    def test_all_six_response_frame_load_orders_require_final_original_check(self):
        for sequence in itertools.permutations((nav_response(), frame_event(), load_event())):
            with self.subTest(sequence=sequence):
                model = self.model()
                actions = [a for message in sequence for a in self.emit(model, message)]
                sends = [a for a in actions if a.kind == "send"]
                self.assertEqual(len(sends), 1)
                self.assertEqual(sends[0].message, {
                    "id": 12, "method": "Runtime.evaluate", "params": {
                        "expression": LEGACY_EXPRESSION, "returnByValue": True}})
                self.assertGreater(sends[0].remaining, 0)
                self.assertEqual(model.state, State.CHECK_PENDING)
                result = self.emit(model, check_response())
                self.assertEqual(result[0].kind, "legacy_check_passed")
                self.assertEqual(model.reason, "query_and_ready_state_only")

    def test_load_alone_lacks_url_proof(self):
        model = self.model()
        self.emit(model, nav_response())
        self.assert_no_send(self.emit(model, load_event()))
        self.assertEqual(model.state, State.WAIT_EVENT)

    def test_url_alone_lacks_load_proof(self):
        model = self.model()
        self.emit(model, nav_response())
        self.assert_no_send(self.emit(model, frame_event()))
        self.assertEqual(model.state, State.WAIT_EVENT)

    def test_old_loader_and_child_frames_never_wake(self):
        model = self.model()
        self.emit(model, frame_event(loader="old", url=URL.replace("f=4", "f=3")))
        self.emit(model, load_event(loader="old"))
        self.emit(model, nav_response())
        for message in (load_event(loader="old"), load_event(frame="child"),
                        frame_event(frame="child", parentId="main"),
                        frame_event(parentId="another-frame")):
            self.assert_no_send(self.emit(model, message))
        self.assertEqual(model.state, State.WAIT_EVENT)

    def test_old_frame_navigation_after_binding_is_explicit_fallback(self):
        model = self.model()
        self.emit(model, nav_response())
        self.emit(model, frame_event(loader="old"))
        self.assertEqual(model.state, State.FALLBACK)
        self.assertEqual(model.reason, "different_document_after_response")

    def test_url_is_exact_not_merely_same_query(self):
        model = self.model()
        self.emit(model, frame_event(url="http://example.invalid/other.html?f=4"))
        self.emit(model, load_event())
        self.assert_no_send(self.emit(model, nav_response()))
        self.assertEqual(model.reason, "document_url_mismatch")

    def test_frame_url_fragment_is_part_of_identity(self):
        model = self.model()
        self.emit(model, frame_event(urlFragment="#section"))
        self.emit(model, load_event())
        self.assert_no_send(self.emit(model, nav_response()))
        self.assertEqual(model.reason, "document_url_mismatch")

    def test_conflicting_same_loader_url_is_not_repaired_by_later_match(self):
        model = self.model()
        self.emit(model, frame_event(url=URL.replace("f=4", "f=3")))
        self.emit(model, frame_event())
        self.emit(model, load_event())
        self.assert_no_send(self.emit(model, nav_response()))
        self.assertEqual(model.reason, "document_url_mismatch")

    def test_pre_response_new_document_invalidates_old_matching_pair(self):
        model = self.model()
        self.emit(model, frame_event())
        self.emit(model, load_event())
        self.emit(model, frame_event(loader="newer", url="http://example.invalid/other.html?f=4"))
        self.emit(model, load_event(loader="newer"))
        self.assert_no_send(self.emit(model, nav_response()))
        self.emit(model, check_response())
        self.assertEqual(model.state, State.FALLBACK)
        self.assertEqual(model.reason, "document_evidence_invalidated")

    def test_pre_response_invalidated_document_cannot_be_repaired_by_duplicates(self):
        model = self.model()
        for message in (frame_event(), load_event(), frame_event(loader="newer"),
                        frame_event(), load_event(), nav_response(), check_response()):
            self.assert_no_send(self.emit(model, message))
        self.assertEqual(model.reason, "document_evidence_invalidated")

    def test_older_document_before_fresh_expected_document_remains_allowed(self):
        model = self.model()
        self.emit(model, frame_event(loader="old", url=URL.replace("f=4", "f=3")))
        self.emit(model, load_event(loader="old"))
        self.emit(model, frame_event())
        self.emit(model, load_event())
        result = self.emit(model, nav_response())
        self.assertEqual(sum(a.kind == "send" for a in result), 1)
        self.emit(model, check_response())
        self.assertEqual(model.state, State.LEGACY_CHECK_PASSED)

    def test_pre_response_same_document_change_cannot_hide_behind_equal_query(self):
        model = self.model()
        self.emit(model, frame_event())
        self.emit(model, load_event())
        self.emit(model, {"method": "Page.navigatedWithinDocument", "params": {
            "frameId": "main", "url": "http://example.invalid/other.html?f=4",
            "navigationType": "historyApi"}})
        self.assert_no_send(self.emit(model, nav_response()))
        self.emit(model, check_response())
        self.assertEqual(model.reason, "same_document_before_response")
        self.assertEqual(model.state, State.FALLBACK)

    def test_early_same_document_evidence_is_sticky_even_before_any_loader(self):
        model = self.model()
        self.emit(model, {"method": "Page.navigatedWithinDocument", "params": {
            "frameId": "main", "url": URL}})
        self.emit(model, frame_event())
        self.emit(model, load_event())
        self.assert_no_send(self.emit(model, nav_response()))
        self.assertEqual(model.reason, "same_document_before_response")

    def test_pre_response_context_loss_invalidates_early_evidence(self):
        for method in ("Runtime.executionContextsCleared", "Runtime.executionContextDestroyed"):
            for early in ((frame_event(),), (load_event(),), (frame_event(), load_event())):
                with self.subTest(method=method, early=early):
                    model = self.model()
                    for message in early:
                        self.emit(model, message)
                    self.emit(model, {"method": method, "params": {"executionContextId": 2}})
                    self.emit(model, frame_event())
                    self.emit(model, load_event())
                    self.assert_no_send(self.emit(model, nav_response()))
                    self.emit(model, check_response())
                    self.assertEqual(model.reason, "document_evidence_invalidated")

    def test_context_loss_while_strict_pending_blocks_late_policy_completion(self):
        model = self.model(strict=True)
        self.emit(model, fetch_event())
        self.complete_events(model)
        self.assertEqual(model.state, State.WAIT_STRICT)
        self.emit(model, {"method": "Runtime.executionContextsCleared", "params": {}})
        self.assert_no_send(model.fetch_finished("resource-1", generation=7, target="tab-a",
                                                 now=self.now, success=True))
        self.assertEqual(model.reason, "context_changed_after_evidence")
        self.assertEqual(model.pending_fetch, {"resource-1"})

    def test_foreign_same_document_and_context_messages_cannot_invalidate(self):
        model = self.model()
        self.emit(model, frame_event())
        self.emit(model, load_event())
        self.emit(model, {"method": "Page.navigatedWithinDocument", "params": {
            "frameId": "child", "url": "http://example.invalid/other.html?f=4"}})
        self.emit(model, {"method": "Runtime.executionContextsCleared", "params": {}}, generation=6)
        result = self.emit(model, nav_response())
        self.assertEqual(sum(a.kind == "send" for a in result), 1)

    def test_unscoped_load_and_nonload_lifecycle_are_forwarded(self):
        model = self.model()
        self.emit(model, nav_response())
        self.emit(model, frame_event())
        for message in ({"method": "Page.loadEventFired", "params": {"timestamp": 12}},
                        load_event(name="DOMContentLoaded")):
            result = self.emit(model, message)
            self.assertEqual(result[0].kind, "forward")
            self.assertIs(result[0].message, message)
            self.assert_no_send(result)

    def test_recovery_generation_and_wrong_target_are_isolated(self):
        old = self.model(generation=7)
        self.complete_events(old)
        old.disconnect(generation=7, target="tab-a", now=self.now)
        recovered = self.model(generation=8)
        for message in (nav_response(), frame_event(), load_event(), check_response()):
            self.assert_no_send(self.emit(recovered, message, generation=7))
            self.assert_no_send(self.emit(recovered, message, target="tab-b"))
        self.assertEqual(recovered.state, State.WAIT_NAVIGATE)
        self.assertEqual(len(recovered.records), 0)
        self.complete_events(recovered)
        self.assertEqual(recovered.state, State.CHECK_PENDING)

    def test_new_attempt_does_not_inherit_same_generation_cache(self):
        old = self.model()
        self.complete_events(old)
        self.emit(old, check_response())
        new = self.model()
        self.assert_no_send(self.emit(new, nav_response()))
        self.assertEqual(new.state, State.WAIT_EVENT)

    def test_navigate_failures_even_with_complete_early_event_pair(self):
        cases = [({"id": 11, "error": {"code": -1}}, "navigate_protocol_error"),
                 (nav_response(errorText="failed"), "navigate_error_text"),
                 (nav_response(isDownload=True), "navigation_is_download"),
                 (nav_response(frameId="child"), "navigation_frame_mismatch"),
                 ({"id": 11, "result": None}, "malformed_navigate_result")]
        for message, reason in cases:
            with self.subTest(reason=reason):
                model = self.model()
                self.emit(model, frame_event())
                self.emit(model, load_event())
                self.assert_no_send(self.emit(model, message))
                self.assertEqual(model.state, State.FAILED)
                self.assertEqual(model.reason, reason)

    def test_missing_loader_same_document_has_no_success(self):
        for result in ({"frameId": "main"}, {"frameId": "main", "loaderId": ""}):
            model = self.model()
            actions = self.emit(model, {"id": 11, "result": result})
            self.assert_no_send(actions)
            self.assertEqual(model.reason, "same_document_or_missing_loader")
            self.assertEqual(model.state, State.FALLBACK)

    def test_capability_unavailable_and_fragment_url_explicit_fallback(self):
        for config, reason in (({"lifecycle_enabled": False}, "lifecycle_not_enabled"),
                               ({"url": URL + "#section"}, "fragment_url_not_modeled")):
            model = self.model(**config)
            self.assert_no_send(self.emit(model, nav_response()))
            self.assertEqual(model.reason, reason)

    def test_missing_events_falls_back_with_remaining_shared_budget(self):
        model = self.model()
        self.emit(model, nav_response())
        result = model.tick(11.02)
        self.assertEqual(model.state, State.FALLBACK)
        self.assertAlmostEqual(result[0].remaining, 3.98)
        self.assertEqual(model.reason, "matching_event_missing")

    def test_response_deadline_and_check_deadline_are_not_reset(self):
        for checking in (False, True):
            model = self.model()
            if checking:
                self.complete_events(model)
            message = check_response() if checking else nav_response()
            self.assert_no_send(self.emit(model, message, now=15.0))
            self.assertEqual(model.reason, "deadline_expired")
            self.assertEqual(model.state, State.FAILED)

    def test_deadline_advances_even_on_stale_traffic(self):
        model = self.model()
        self.emit(model, load_event(), generation=6, now=15.0)
        self.assertEqual(model.reason, "deadline_expired")

    def test_event_deadline_boundary_does_not_accept_late_load(self):
        model = self.model()
        self.emit(model, nav_response())
        self.emit(model, frame_event())
        self.assert_no_send(self.emit(model, load_event(), now=11.01))
        self.assertEqual(model.reason, "matching_event_missing")

    def test_zero_event_wait_can_use_complete_early_pair(self):
        model = self.model(event_wait=0)
        self.emit(model, frame_event())
        self.emit(model, load_event())
        result = self.emit(model, nav_response())
        self.assertEqual(sum(a.kind == "send" for a in result), 1)

    def test_disconnect_and_stale_disconnect(self):
        model = self.model()
        self.assertEqual(model.disconnect(generation=6, target="tab-a", now=10.1), [])
        self.assertEqual(model.state, State.WAIT_NAVIGATE)
        model.disconnect(generation=7, target="tab-a", now=10.2)
        self.assertEqual(model.reason, "connection_closed")
        self.assert_no_send(self.emit(model, nav_response(), now=10.3))

    def test_event_capacity_is_bounded_and_overflow_explicit(self):
        model = self.model(max_records=2)
        self.emit(model, load_event(loader="old-1"))
        self.emit(model, load_event(loader="old-2"))
        self.assertEqual(len(model.records), 2)
        self.assert_no_send(self.emit(model, load_event()))
        self.assertEqual(model.reason, "event_queue_full")
        self.assertEqual(model.state, State.FAILED)

    def test_duplicate_events_are_idempotent_and_do_not_repeat_check(self):
        model = self.model(event_wait=4)
        self.emit(model, nav_response())
        for _ in range(100):
            self.emit(model, load_event())
        self.assertEqual(len(model.records), 1)
        first = self.emit(model, frame_event())
        self.assertEqual(sum(a.kind == "send" for a in first), 1)
        for message in (frame_event(), load_event(), frame_event()):
            self.assert_no_send(self.emit(model, message))

    def test_strict_fetch_and_policy_reply_preserved_before_check(self):
        model = self.model(strict=True)
        paused = fetch_event()
        actions = self.emit(model, paused)
        self.assertIs(actions[0].message, paused)
        self.assert_no_send(self.complete_events(model))
        self.assertEqual(model.state, State.WAIT_STRICT)
        reply = {"id": 90, "result": {}}
        result = self.emit(model, reply)
        self.assertIs(result[0].message, reply)
        self.assertEqual(model.pending_fetch, {"resource-1"})
        self.now += 0.01
        result = model.fetch_finished("resource-1", generation=7, target="tab-a",
                                      now=self.now, success=True)
        self.assertEqual(sum(a.kind == "send" for a in result), 1)

    def test_fetch_during_final_check_prevents_predicate_completion(self):
        model = self.model(strict=True)
        self.complete_events(model)
        self.emit(model, fetch_event())
        self.assertEqual(self.emit(model, check_response()), [])
        self.assertEqual(model.state, State.CHECK_PENDING)
        result = model.fetch_finished("resource-1", generation=7, target="tab-a",
                                      now=self.now, success=True)
        self.assertEqual(result[0].kind, "legacy_check_passed")

    def test_fetch_failure_never_releases_event_or_check(self):
        for after_check in (False, True):
            model = self.model(strict=True)
            if after_check:
                self.complete_events(model)
            self.emit(model, fetch_event())
            if after_check:
                self.emit(model, check_response())
            else:
                self.complete_events(model)
            model.fetch_finished("resource-1", generation=7, target="tab-a",
                                 now=self.now, success=False)
            self.assertEqual(model.reason, "strict_policy_failed")
            self.assertEqual(model.state, State.FAILED)

    def test_fetch_queue_full_still_forwards_the_overflow_message(self):
        model = self.model(strict=True, max_pending_fetch=1)
        self.emit(model, fetch_event("first"))
        overflow = fetch_event("second")
        result = self.emit(model, overflow)
        self.assertIs(result[0].message, overflow)
        self.assertEqual(model.reason, "fetch_queue_full")
        self.assertEqual(model.pending_fetch, {"first"})

    def test_duplicate_fetch_pause_fails_instead_of_false_drain(self):
        model = self.model(strict=True)
        self.emit(model, fetch_event())
        self.emit(model, fetch_event())
        self.assertEqual(model.reason, "duplicate_fetch_pause")

    def test_stale_fetch_receipt_and_unmatched_receipt(self):
        model = self.model(strict=True)
        self.emit(model, fetch_event())
        self.complete_events(model)
        model.fetch_finished("resource-1", generation=6, target="tab-a",
                             now=self.now, success=True)
        self.assertEqual(model.state, State.WAIT_STRICT)
        model.fetch_finished("unknown", generation=7, target="tab-a",
                             now=self.now, success=True)
        self.assertEqual(model.reason, "unmatched_fetch_receipt")

    def test_pending_strict_times_out_without_receipt(self):
        model = self.model(strict=True)
        self.emit(model, fetch_event())
        self.complete_events(model)
        model.tick(15.0)
        self.assertEqual(model.reason, "deadline_expired")
        self.assertEqual(model.pending_fetch, {"resource-1"})

    def test_fetch_is_forwarded_even_after_timeout_or_completion(self):
        for completed in (False, True):
            model = self.model(strict=True)
            if completed:
                self.complete_events(model)
                self.emit(model, check_response())
            else:
                model.tick(15.0)
            paused = fetch_event()
            result = self.emit(model, paused, now=16.0)
            self.assertIs(result[0].message, paused)
            self.assert_no_send(result)

    def test_non_strict_fetch_is_preserved_but_does_not_invent_policy(self):
        model = self.model(strict=False)
        paused = fetch_event()
        self.assertIs(self.emit(model, paused)[0].message, paused)
        self.assertEqual(model.pending_fetch, set())
        self.complete_events(model)
        self.assertEqual(model.state, State.CHECK_PENDING)

    def test_wrong_query_or_not_complete_requires_real_fallback(self):
        for value in ("?f=3|complete", "?f=4|interactive", "?f=4|loading", "complete"):
            model = self.model()
            self.complete_events(model)
            self.assert_no_send(self.emit(model, check_response(value)))
            self.assertEqual(model.reason, "legacy_predicate_not_satisfied")
            self.assertEqual(model.state, State.FALLBACK)

    def test_empty_query_matches_unchanged_legacy_predicate(self):
        model = self.model(url="http://example.invalid/scene.html")
        self.emit(model, nav_response())
        self.emit(model, frame_event(url=model.url))
        self.emit(model, load_event())
        self.emit(model, check_response("|complete"))
        self.assertEqual(model.state, State.LEGACY_CHECK_PASSED)

    def test_check_error_exception_and_malformed_result_fail(self):
        for message in ({"id": 12, "error": {"code": -32000}},
                        {"id": 12, "result": {"exceptionDetails": {},
                                              "result": {"value": "?f=4|complete"}}},
                        {"id": 12, "result": None}, check_response(True)):
            model = self.model()
            self.complete_events(model)
            self.emit(model, message)
            self.assertEqual(model.state, State.FAILED)

    def test_context_change_during_check_invalidates_stale_result(self):
        for method in ("Runtime.executionContextDestroyed", "Runtime.executionContextsCleared"):
            model = self.model()
            self.complete_events(model)
            self.emit(model, {"method": method, "params": {"executionContextId": 2}})
            self.assert_no_send(self.emit(model, check_response()))
            self.assertEqual(model.reason, "context_changed_during_check")
            self.assertEqual(model.state, State.FALLBACK)

    def test_normal_precheck_context_clear_does_not_invent_readiness(self):
        model = self.model()
        self.emit(model, {"method": "Runtime.executionContextsCleared", "params": {}})
        self.assertEqual(model.state, State.WAIT_NAVIGATE)
        self.complete_events(model)
        self.assertEqual(model.state, State.CHECK_PENDING)

    def test_same_document_event_after_response_falls_back(self):
        model = self.model()
        self.emit(model, nav_response())
        self.emit(model, {"method": "Page.navigatedWithinDocument",
                          "params": {"frameId": "main", "url": URL}})
        self.assertEqual(model.reason, "same_document_event")

    def test_main_frame_detachment_fails(self):
        model = self.model()
        self.emit(model, {"method": "Page.frameDetached", "params": {"frameId": "child"}})
        self.assertEqual(model.state, State.WAIT_NAVIGATE)
        self.emit(model, {"method": "Page.frameDetached", "params": {"frameId": "main"}})
        self.assertEqual(model.reason, "main_frame_detached")

    def test_unsolicited_check_and_duplicate_navigation_response_fail(self):
        model = self.model()
        self.emit(model, check_response())
        self.assertEqual(model.reason, "unsolicited_check_response")
        model = self.model()
        self.emit(model, nav_response())
        self.emit(model, nav_response())
        self.assertEqual(model.reason, "duplicate_navigation_response")

    def test_boolean_response_id_does_not_alias_an_integer_id(self):
        model = self.model(navigate_id=1)
        result = self.emit(model, {"id": True, "result": {"frameId": "main"}})
        self.assertEqual(result[0].kind, "forward")
        self.assertEqual(model.state, State.WAIT_NAVIGATE)

    def test_policy_error_response_remains_owned_by_existing_policy(self):
        model = self.model(strict=True)
        self.emit(model, fetch_event())
        self.complete_events(model)
        reply = {"id": 90, "error": {"code": -32000, "message": "policy failure"}}
        result = self.emit(model, reply)
        self.assertIs(result[0].message, reply)
        self.assertEqual(model.state, State.WAIT_STRICT)
        model.fetch_finished("resource-1", generation=7, target="tab-a",
                             now=self.now, success=False)
        self.assertEqual(model.state, State.FAILED)

    def test_backward_or_nonfinite_clock_fails(self):
        for now in (9.0, float("nan"), float("inf"), True):
            model = self.model()
            model.tick(now)
            self.assertEqual(model.reason, "invalid_monotonic_clock")

    def test_malformed_events_do_not_turn_green(self):
        for message in (load_event(loader=None), frame_event(url=""),
                        {"method": "Page.frameNavigated", "params": {"frame": None}},
                        {"method": "Page.lifecycleEvent", "params": None},
                        fetch_event(request_id=None)):
            model = self.model(strict=True)
            self.emit(model, message)
            self.assertEqual(model.state, State.FAILED)

    def test_constructor_rejects_unbounded_or_invalid_configuration(self):
        for changes in ({"generation": True}, {"frame": ""}, {"url": ""},
                        {"url": "x" * 8193}, {"navigate_id": True}, {"check_id": 11},
                        {"timeout": float("inf")}, {"timeout": 0}, {"event_wait": -1},
                        {"max_records": 257}, {"max_pending_fetch": 0}, {"strict": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.model(**changes)

    def test_single_attempt_and_stable_terminal_state(self):
        model = self.model()
        with self.assertRaises(ValueError):
            model.start(11)
        self.complete_events(model)
        self.emit(model, check_response())
        for message in (None, nav_response(), check_response("?f=3|complete")):
            self.assert_no_send(self.emit(model, message))
            self.assertEqual(model.state, State.LEGACY_CHECK_PASSED)


if __name__ == "__main__":
    unittest.main()
