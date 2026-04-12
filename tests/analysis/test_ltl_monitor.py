"""Tests for LTL runtime monitors."""

from __future__ import annotations

import pytest

from vt_protocol.analysis.ltl_monitor import (
    DEFAULT_PROPERTIES,
    LTLViolation,
    MonitorResult,
    MonitorStatus,
    PatternType,
    PropertySpec,
    check_absence,
    check_precedence,
    check_response,
    evaluate_properties,
)
from vt_protocol.observation.trajectory import TrajectoryEvent


def _event(action: str) -> TrajectoryEvent:
    return TrajectoryEvent(action=action)


class TestCheckResponse:
    def test_satisfied(self) -> None:
        events = [_event("decision"), _event("review")]
        status, violation = check_response(
            events, trigger_action="decision", response_action="review",
        )
        assert status == MonitorStatus.SATISFIED
        assert violation is None

    def test_violated(self) -> None:
        events = [_event("decision"), _event("implement")]
        status, violation = check_response(
            events, trigger_action="decision", response_action="review",
        )
        assert status == MonitorStatus.VIOLATED
        assert violation is not None
        assert violation.pattern == PatternType.RESPONSE

    def test_multiple_triggers_one_response(self) -> None:
        events = [
            _event("decision"), _event("decision"), _event("review"),
        ]
        status, _ = check_response(
            events, trigger_action="decision", response_action="review",
        )
        assert status == MonitorStatus.SATISFIED

    def test_no_triggers(self) -> None:
        events = [_event("other"), _event("review")]
        status, _ = check_response(
            events, trigger_action="decision", response_action="review",
        )
        assert status == MonitorStatus.SATISFIED

    def test_empty_events(self) -> None:
        status, _ = check_response(
            [], trigger_action="decision", response_action="review",
        )
        assert status == MonitorStatus.SATISFIED


class TestCheckPrecedence:
    def test_satisfied(self) -> None:
        events = [_event("auth"), _event("data_access")]
        status, _ = check_precedence(
            events, required_first="auth", guarded_action="data_access",
        )
        assert status == MonitorStatus.SATISFIED

    def test_violated(self) -> None:
        events = [_event("data_access"), _event("auth")]
        status, violation = check_precedence(
            events, required_first="auth", guarded_action="data_access",
        )
        assert status == MonitorStatus.VIOLATED
        assert violation is not None
        assert "precede" in violation.message.lower() or "precedence" in violation.message.lower()

    def test_no_guarded_action(self) -> None:
        events = [_event("auth"), _event("other")]
        status, _ = check_precedence(
            events, required_first="auth", guarded_action="data_access",
        )
        assert status == MonitorStatus.SATISFIED

    def test_empty_events(self) -> None:
        status, _ = check_precedence(
            [], required_first="auth", guarded_action="data_access",
        )
        assert status == MonitorStatus.SATISFIED


class TestCheckAbsence:
    def test_satisfied_no_forbidden(self) -> None:
        events = [_event("flag_breaking"), _event("other"), _event("resolve_breaking")]
        status, _ = check_absence(
            events,
            scope_start="flag_breaking",
            scope_end="resolve_breaking",
            forbidden_action="deploy",
        )
        assert status == MonitorStatus.SATISFIED

    def test_violated(self) -> None:
        events = [
            _event("flag_breaking"),
            _event("deploy"),  # Forbidden while in scope
            _event("resolve_breaking"),
        ]
        status, violation = check_absence(
            events,
            scope_start="flag_breaking",
            scope_end="resolve_breaking",
            forbidden_action="deploy",
        )
        assert status == MonitorStatus.VIOLATED
        assert violation is not None
        assert "absence" in violation.message.lower()

    def test_deploy_before_scope_ok(self) -> None:
        events = [_event("deploy"), _event("flag_breaking"), _event("resolve_breaking")]
        status, _ = check_absence(
            events,
            scope_start="flag_breaking",
            scope_end="resolve_breaking",
            forbidden_action="deploy",
        )
        assert status == MonitorStatus.SATISFIED

    def test_deploy_after_scope_ok(self) -> None:
        events = [
            _event("flag_breaking"),
            _event("resolve_breaking"),
            _event("deploy"),
        ]
        status, _ = check_absence(
            events,
            scope_start="flag_breaking",
            scope_end="resolve_breaking",
            forbidden_action="deploy",
        )
        assert status == MonitorStatus.SATISFIED

    def test_multiple_scopes(self) -> None:
        events = [
            _event("flag_breaking"),
            _event("resolve_breaking"),
            _event("flag_breaking"),
            _event("deploy"),  # Violated in second scope
        ]
        status, violation = check_absence(
            events,
            scope_start="flag_breaking",
            scope_end="resolve_breaking",
            forbidden_action="deploy",
        )
        assert status == MonitorStatus.VIOLATED

    def test_empty_events(self) -> None:
        status, _ = check_absence(
            [],
            scope_start="flag_breaking",
            scope_end="resolve_breaking",
            forbidden_action="deploy",
        )
        assert status == MonitorStatus.SATISFIED


class TestLTLViolation:
    def test_to_dict(self) -> None:
        v = LTLViolation(
            pattern=PatternType.RESPONSE,
            property_name="test",
            message="violated",
            trigger_event=_event("decision"),
        )
        d = v.to_dict()
        assert d["pattern"] == "response"
        assert d["trigger_action"] == "decision"

    def test_to_dict_no_event(self) -> None:
        v = LTLViolation(pattern=PatternType.PRECEDENCE, property_name="p", message="m")
        d = v.to_dict()
        assert d["trigger_action"] is None


class TestMonitorResult:
    def test_clean(self) -> None:
        result = MonitorResult(satisfied=["a", "b"])
        assert result.is_clean is True

    def test_not_clean(self) -> None:
        result = MonitorResult(
            violations=[LTLViolation(pattern=PatternType.RESPONSE, property_name="r", message="v")],
        )
        assert result.is_clean is False

    def test_to_dict(self) -> None:
        result = MonitorResult(satisfied=["a"], pending=["b"])
        d = result.to_dict()
        assert d["satisfied_count"] == 1
        assert d["pending_count"] == 1


class TestEvaluateProperties:
    def test_all_satisfied(self) -> None:
        events = [
            _event("auth"),
            _event("decision"),
            _event("review"),
            _event("data_access"),
        ]
        result = evaluate_properties(events)
        # Check that decision_reviewed and auth_before_data pass
        assert "decision_reviewed" in result.satisfied
        assert "auth_before_data" in result.satisfied

    def test_custom_properties(self) -> None:
        props = [PropertySpec(
            name="custom_response",
            pattern=PatternType.RESPONSE,
            params={"trigger_action": "request", "response_action": "respond"},
        )]
        events = [_event("request")]
        result = evaluate_properties(events, properties=props)
        assert len(result.violations) == 1
        assert result.violations[0].property_name == "custom_response"

    def test_default_properties_exist(self) -> None:
        assert len(DEFAULT_PROPERTIES) == 3

    def test_empty_events(self) -> None:
        result = evaluate_properties([])
        # No triggers means no violations
        assert result.is_clean

    def test_property_spec_to_dict(self) -> None:
        ps = PropertySpec(name="test", pattern=PatternType.ABSENCE, params={"a": "b"})
        d = ps.to_dict()
        assert d["name"] == "test"
        assert d["pattern"] == "absence"
