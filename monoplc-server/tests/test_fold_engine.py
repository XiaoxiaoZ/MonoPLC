"""
Tests for FoldEngine (Design C: StateMonoid Fold / Time-Slice Replay).

Demonstrates that Monoid associativity enables:
  1. Parallel fold produces identical results to sequential fold
  2. Checkpoint reuse accelerates state_at(t) without sacrificing correctness
  3. Time-window replay correctly isolates effects in a period
"""

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings as hsettings

from models import Effect, EffectType
from monoid import MWStateMonoid, SumMonoid, DictProductMonoid
from fold_engine import FoldEngine, EffectLogEntry, Checkpoint, FoldComparison
from homomorphism import phi
from state_store import PRODUCT_COMPONENTS

from strategies import effect_list_strategy


class TestFoldSequential:
    """Test sequential fold correctness."""

    engine = FoldEngine(MWStateMonoid(), lambda e: phi([e]))

    def test_fold_empty(self):
        """fold([]) == ε (empty dict)"""
        assert self.engine.fold([]) == {}

    def test_fold_single_effect(self):
        """fold([e]) == φ([e])"""
        e = Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0)
        result = self.engine.fold([e])
        assert result == {"EFF_VALVE_CTRL.V1": {"value": 1.0, "payload": ""}}

    def test_fold_filters_noise(self):
        """EFF_SYSTEM_TICK and EFF_NONE should not appear in fold result."""
        effects = [
            Effect(e_type=EffectType.EFF_SYSTEM_TICK, target="", payload="", value=0.0),
            Effect(e_type=EffectType.EFF_NONE, target="", payload="", value=0.0),
            Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0),
        ]
        result = self.engine.fold(effects)
        assert "EFF_SYSTEM_TICK." not in str(result)
        assert "EFF_NONE" not in str(result)
        assert "EFF_VALVE_CTRL.V1" in result

    def test_fold_last_writer_wins(self, sample_effects):
        """Later effects overwrite earlier ones for the same key."""
        result = self.engine.fold(sample_effects)
        # CoolingValve: first 1.0, then 0.0 → should be 0.0
        assert result["EFF_VALVE_CTRL.CoolingValve"]["value"] == 0.0
        # system/status: first 75.3, then 72.1 → should be 72.1
        assert result["EFF_IOT_PUB.system/status"]["value"] == 72.1


class TestFoldParallel:
    """
    Test parallel fold — MUST produce identical results to sequential fold.

    This is the core Design C capability: Monoid associativity guarantees
    that splitting the effect list at any boundary and folding each part
    independently, then merging, produces the same result as sequential fold.
    """

    engine = FoldEngine(MWStateMonoid(), lambda e: phi([e]))

    def test_parallel_empty(self):
        """Parallel fold of empty list == ε."""
        assert self.engine.fold_parallel([], n_workers=4) == {}

    def test_parallel_equals_sequential(self, sample_effects):
        """fold(effects) == fold_parallel(effects, n) for fixed sample."""
        seq = self.engine.fold(sample_effects)
        for n in [1, 2, 3, 4, 7]:
            par = self.engine.fold_parallel(sample_effects, n_workers=n)
            assert seq == par, f"Mismatch with {n} workers"

    @given(effects=effect_list_strategy(min_size=1, max_size=50))
    @hsettings(max_examples=50)
    def test_parallel_equals_sequential_property(self, effects):
        """Property-based: fold(e) == fold_parallel(e, n) for random effects."""
        seq = self.engine.fold(effects)
        par = self.engine.fold_parallel(effects, n_workers=4)
        assert seq == par

    def test_fold_compare(self, sample_effects):
        """fold_compare should report identical=True."""
        comparison = self.engine.fold_compare(sample_effects, n_workers=3)
        assert comparison.identical
        assert comparison.sequential == comparison.parallel


class TestStateAt:
    """
    Test time-travel: state_at(t) reconstructs historical state.

    With checkpoints, only the delta after the checkpoint needs to be folded.
    Without checkpoints, all effects before t are folded.
    Both produce the same result — guaranteed by associativity.
    """

    engine = FoldEngine(MWStateMonoid(), lambda e: phi([e]))

    def _make_log(self, effects: list[Effect], base_time: datetime) -> list[EffectLogEntry]:
        """Create a timestamped log from effects."""
        return [
            EffectLogEntry(effect=e, timestamp=base_time + timedelta(seconds=i))
            for i, e in enumerate(effects)
        ]

    def test_state_at_no_checkpoint(self, sample_effects):
        """state_at(t) without checkpoints folds all effects before t."""
        base = datetime(2026, 1, 1, 9, 0, 0)
        log = self._make_log(sample_effects, base)

        # Query at t = base + 4s → should include first 5 effects (indices 0-4)
        t = base + timedelta(seconds=4)
        result = self.engine.state_at(t, log, checkpoints=[])

        expected = self.engine.fold([e.effect for e in log[:5]])
        assert result.state == expected
        assert "full fold" in result.method

    def test_state_at_with_checkpoint(self, sample_effects):
        """state_at(t) with checkpoint should use checkpoint + delta."""
        base = datetime(2026, 1, 1, 9, 0, 0)
        log = self._make_log(sample_effects, base)

        # Create checkpoint after 3 effects
        checkpoint_state = self.engine.fold([e.effect for e in log[:3]])
        cp = Checkpoint(
            timestamp=base + timedelta(seconds=2),
            state=checkpoint_state,
            effect_index=3,
        )

        # Query at t = base + 6s → should use checkpoint + fold effects 3-6
        t = base + timedelta(seconds=6)
        result = self.engine.state_at(t, log, checkpoints=[cp])

        # Verify same as full fold
        full_result = self.engine.state_at(t, log, checkpoints=[])
        assert result.state == full_result.state
        assert "checkpoint+delta" in result.method

    def test_state_at_before_any_effects(self):
        """state_at(t) before any effects returns empty state."""
        base = datetime(2026, 1, 1, 9, 0, 0)
        e = Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0)
        log = [EffectLogEntry(effect=e, timestamp=base + timedelta(seconds=10))]

        t = base  # Before any effect
        result = self.engine.state_at(t, log, checkpoints=[])
        assert result.state == {}
        assert result.effects_folded == 0


class TestStateBetween:
    """Test time-window replay."""

    engine = FoldEngine(MWStateMonoid(), lambda e: phi([e]))

    def test_state_between(self):
        """state_between(t1, t2) folds only effects in [t1, t2]."""
        base = datetime(2026, 1, 1, 9, 0, 0)
        effects = [
            Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0),
            Effect(e_type=EffectType.EFF_IOT_PUB, target="temp", payload="hot", value=80.0),
            Effect(e_type=EffectType.EFF_ALARM, target="OverTemp", payload="!", value=1.0),
            Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=0.0),
        ]
        log = [
            EffectLogEntry(effect=e, timestamp=base + timedelta(seconds=i))
            for i, e in enumerate(effects)
        ]

        # Window [base+1, base+2] should include effects at index 1 and 2
        result = self.engine.state_between(
            base + timedelta(seconds=1),
            base + timedelta(seconds=2),
            log,
        )
        assert result.effects_folded == 2
        assert "EFF_IOT_PUB.temp" in result.state
        assert "EFF_ALARM.OverTemp" in result.state
        # V1 effects are outside the window
        assert "EFF_VALVE_CTRL.V1" not in result.state

    def test_state_between_empty_window(self):
        """Empty time window returns empty state."""
        base = datetime(2026, 1, 1, 9, 0, 0)
        e = Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0)
        log = [EffectLogEntry(effect=e, timestamp=base)]

        result = self.engine.state_between(
            base + timedelta(hours=1),
            base + timedelta(hours=2),
            log,
        )
        assert result.state == {}
        assert result.effects_folded == 0


class TestFoldIncremental:
    """
    Test incremental computation: state_old ⊕ φ([e_new]) == fold(all).

    This is the core of Advantage #1: O(1) update produces the same
    result as O(n) full re-fold — guaranteed by associativity.
    """

    engine = FoldEngine(MWStateMonoid(), lambda e: phi([e]))

    def test_incremental_equals_full(self, sample_effects):
        """Incremental update must equal full re-fold."""
        result = self.engine.fold_incremental_compare(sample_effects)
        assert result.identical
        assert result.incremental_result == result.full_result

    def test_incremental_timing_recorded(self, sample_effects):
        """Timing values should be non-negative."""
        result = self.engine.fold_incremental_compare(sample_effects)
        assert result.incremental_time_ms >= 0
        assert result.full_time_ms >= 0

    def test_incremental_empty(self):
        """Incremental on < 2 effects returns empty."""
        result = self.engine.fold_incremental_compare([])
        assert result.identical
        assert result.effects_total == 0

    @given(effects=effect_list_strategy(min_size=2, max_size=50))
    @hsettings(max_examples=50)
    def test_incremental_property(self, effects):
        """Property: incremental == full for arbitrary effect lists."""
        result = self.engine.fold_incremental_compare(effects)
        assert result.identical


class TestFoldProduct:
    """
    Test Product Monoid fold: ONE pass → N views.

    This is the core of Advantage #3: ProductMonoid(state, count, alarm)
    computes all three simultaneously in a single traversal.
    """

    _pm = DictProductMonoid(PRODUCT_COMPONENTS)
    engine = FoldEngine(_pm, _pm.map_effect)

    def test_product_fold_basic(self, sample_effects):
        """Product fold should produce correct state, count, and alarms."""
        result = self.engine.fold(sample_effects)

        # State should match regular fold (minus filtered types)
        regular_engine = FoldEngine(MWStateMonoid(), lambda e: phi([e]))
        regular_state = regular_engine.fold(sample_effects)
        assert result["state"] == regular_state

        # Count should be > 0 (sample_effects has meaningful effects)
        assert result["effect_count"] > 0

        # Alarm count should match number of EFF_ALARM in sample
        expected_alarms = sum(
            1 for e in sample_effects
            if e.e_type == EffectType.EFF_ALARM
        )
        assert result["alarm_count"] == expected_alarms

    def test_product_fold_empty(self):
        """Product fold on empty list returns zeros."""
        result = self.engine.fold([])
        assert result["state"] == {}
        assert result["effect_count"] == 0
        assert result["alarm_count"] == 0

    def test_product_fold_only_noise(self):
        """Product fold filters out SYSTEM_TICK and NONE."""
        effects = [
            Effect(e_type=EffectType.EFF_SYSTEM_TICK, target="", payload="", value=0.0),
            Effect(e_type=EffectType.EFF_NONE, target="", payload="", value=0.0),
        ]
        result = self.engine.fold(effects)
        assert result["state"] == {}
        assert result["effect_count"] == 0
        assert result["alarm_count"] == 0
