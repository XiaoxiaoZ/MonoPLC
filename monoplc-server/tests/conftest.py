"""
Shared pytest fixtures for MonoPLC algebra tests.
"""

import sys
from pathlib import Path

# Add monoplc-server to sys.path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Add tests/ to sys.path so strategies.py is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from models import Effect, EffectType


@pytest.fixture
def sample_effects() -> list[Effect]:
    """A deterministic list of sample effects for non-property-based tests."""
    return [
        Effect(e_type=EffectType.EFF_VALVE_CTRL, target="CoolingValve", payload="", value=1.0),
        Effect(e_type=EffectType.EFF_IOT_PUB, target="system/status", payload="T=75.3", value=75.3),
        Effect(e_type=EffectType.EFF_ALARM, target="OverTemp", payload="TOO HOT", value=1.0),
        Effect(e_type=EffectType.EFF_SYSTEM_TICK, target="", payload="", value=0.0),
        Effect(e_type=EffectType.EFF_VALVE_CTRL, target="CoolingValve", payload="", value=0.0),
        Effect(e_type=EffectType.EFF_IOT_PUB, target="system/status", payload="T=72.1", value=72.1),
        Effect(e_type=EffectType.EFF_NONE, target="", payload="", value=0.0),
        Effect(e_type=EffectType.EFF_SETPOINT_CHANGE, target="TempHighLimit", payload="", value=80.0),
    ]
