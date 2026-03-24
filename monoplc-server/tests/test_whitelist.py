"""
Experiment #6: Whitelist rejection tests.

Verifies that the LLM_ALLOWED_EFFECTS whitelist correctly separates
Supervisory Layer (allowed) from Control Layer (prohibited) effects.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from models import EffectType, LLM_ALLOWED_EFFECTS


class TestWhitelistRejection:
    """Verify whitelist enforces Supervisory / Control boundary."""

    def test_valve_ctrl_rejected(self):
        """EFF_VALVE_CTRL (3) must be rejected — Control Layer only."""
        assert EffectType.EFF_VALVE_CTRL not in LLM_ALLOWED_EFFECTS

    def test_alarm_rejected(self):
        """EFF_ALARM (4) must be rejected — Control Layer only."""
        assert EffectType.EFF_ALARM not in LLM_ALLOWED_EFFECTS

    def test_system_tick_rejected(self):
        """EFF_SYSTEM_TICK (8) must be rejected — PLC internal only."""
        assert EffectType.EFF_SYSTEM_TICK not in LLM_ALLOWED_EFFECTS

    def test_llm_decision_rejected(self):
        """EFF_LLM_DECISION (10) must be rejected — internal marker only."""
        assert EffectType.EFF_LLM_DECISION not in LLM_ALLOWED_EFFECTS

    def test_setpoint_change_allowed(self):
        """EFF_SETPOINT_CHANGE (9) must be allowed — Supervisory Layer."""
        assert EffectType.EFF_SETPOINT_CHANGE in LLM_ALLOWED_EFFECTS

    def test_iot_pub_allowed(self):
        """EFF_IOT_PUB (1) must be allowed — Supervisory Layer."""
        assert EffectType.EFF_IOT_PUB in LLM_ALLOWED_EFFECTS

    def test_iot_cmd_stop_allowed(self):
        """EFF_IOT_CMD_STOP (5) must be allowed — Supervisory Layer."""
        assert EffectType.EFF_IOT_CMD_STOP in LLM_ALLOWED_EFFECTS

    def test_iot_cmd_start_allowed(self):
        """EFF_IOT_CMD_START (6) must be allowed — Supervisory Layer."""
        assert EffectType.EFF_IOT_CMD_START in LLM_ALLOWED_EFFECTS

    def test_iot_cmd_reset_allowed(self):
        """EFF_IOT_CMD_RESET (7) must be allowed — Supervisory Layer."""
        assert EffectType.EFF_IOT_CMD_RESET in LLM_ALLOWED_EFFECTS

    def test_file_log_allowed(self):
        """EFF_FILE_LOG (2) must be allowed — Supervisory Layer."""
        assert EffectType.EFF_FILE_LOG in LLM_ALLOWED_EFFECTS

    def test_whitelist_completeness(self):
        """Every EffectType must be explicitly allowed or rejected.
        No effect type should be accidentally missing from consideration."""
        all_types = set(EffectType)
        forbidden = {
            EffectType.EFF_VALVE_CTRL,
            EffectType.EFF_ALARM,
            EffectType.EFF_SYSTEM_TICK,
            EffectType.EFF_LLM_DECISION,
        }
        allowed = set(LLM_ALLOWED_EFFECTS)
        # Every type is either allowed or forbidden
        assert allowed | forbidden == all_types, (
            f"Unaccounted types: {all_types - allowed - forbidden}"
        )
        # No type is both allowed and forbidden
        assert allowed & forbidden == set()
