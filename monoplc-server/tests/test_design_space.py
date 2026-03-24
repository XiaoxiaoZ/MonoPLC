"""
Design Space Tests — Section 5.7

Tests all alternative Monoid implementations for:
  1. Monoid laws (associativity, identity)
  2. Homomorphism compatibility with MWStateMonoid via phi
  3. Design-specific engineering properties

Design A': BoundedPLCEffectMonoid — take(N) truncation
Design B:  LWWEffectMonoid         — key-aware last-writer-wins merge
Design D:  DetectingPLCEffectMonoid — take(N) + overflow side-channel
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hypothesis import given, settings as hsettings
from hypothesis import strategies as st
import pytest

from monoid import (
    BoundedPLCEffectMonoid,
    LWWEffectMonoid,
    DetectingPLCEffectMonoid,
    MWStateMonoid,
)
from homomorphism import phi, MonoidHomomorphism
from models import Effect, EffectType
from strategies import effect_list_strategy, effect_strategy


# ===========================================================================
# Design A': Bounded Concat — mirrors PLC's take(20)
# ===========================================================================

class TestBoundedPLCEffectMonoid:
    """M_PLC_bounded = (list[Effect], take(20, concat), [])"""
    m = BoundedPLCEffectMonoid(capacity=20)

    @given(a=effect_list_strategy(), b=effect_list_strategy(), c=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        """take(N, take(N, A++B) ++ C) == take(N, A ++ take(N, B++C))"""
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_left_identity(self, a):
        assert self.m.combine(self.m.empty(), a) == a

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_right_identity(self, a):
        assert self.m.combine(a, self.m.empty()) == a

    def test_truncation_at_capacity(self):
        """Combine two lists that exceed capacity -> result is truncated."""
        e = Effect(e_type=EffectType.EFF_IOT_PUB, target="t", value=1.0)
        a = [e] * 15
        b = [e] * 10
        result = self.m.combine(a, b)
        assert len(result) == 20  # truncated from 25

    @given(a=effect_list_strategy(max_size=10), b=effect_list_strategy(max_size=10))
    @hsettings(max_examples=200)
    def test_homomorphism_within_capacity(self, a, b):
        """When total effects <= capacity, homomorphism holds exactly."""
        mw = MWStateMonoid()
        lhs = phi(self.m.combine(a, b))
        rhs = mw.combine(phi(a), phi(b))
        assert lhs == rhs


# ===========================================================================
# Design B: LWW Merge
# ===========================================================================

class TestLWWEffectMonoid:
    """M_LWW = (list[Effect], lww_merge, [])"""
    m = LWWEffectMonoid()

    @given(a=effect_list_strategy(), b=effect_list_strategy(), c=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_left_identity(self, a):
        result = self.m.combine(self.m.empty(), a)
        # LWW deduplicates, so identity holds up to key-equivalence
        assert self._as_dict(result) == self._as_dict(a)

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_right_identity(self, a):
        result = self.m.combine(a, self.m.empty())
        assert self._as_dict(result) == self._as_dict(a)

    def test_deduplication(self):
        """Same (e_type, target) -> only latest value retained."""
        e1 = Effect(e_type=EffectType.EFF_SETPOINT_CHANGE, target="TempHigh", value=80.0)
        e2 = Effect(e_type=EffectType.EFF_SETPOINT_CHANGE, target="TempHigh", value=85.0)
        e3 = Effect(e_type=EffectType.EFF_IOT_PUB, target="status", value=1.0)
        result = self.m.combine([e1, e3], [e2])
        d = self._as_dict(result)
        assert d[(9, "TempHigh")].value == 85.0  # e2 wins
        assert (1, "status") in d                 # e3 retained

    def test_natural_capacity_bound(self):
        """LWW result size is bounded by number of distinct keys."""
        a = [Effect(e_type=EffectType.EFF_IOT_PUB, target="same", value=float(i)) for i in range(100)]
        result = self.m.combine(a, [])
        assert len(result) == 1  # all same key -> one entry

    @given(a=effect_list_strategy(), b=effect_list_strategy())
    @hsettings(max_examples=200)
    def test_homomorphism_with_mw(self, a, b):
        """phi o lww_merge is compatible with MW dict merge (both LWW)."""
        mw = MWStateMonoid()
        lhs = phi(self.m.combine(a, b))
        rhs = mw.combine(phi(a), phi(b))
        assert lhs == rhs

    @staticmethod
    def _as_dict(effects: list[Effect]) -> dict[tuple[int, str], Effect]:
        return {(int(e.e_type), e.target): e for e in effects}


# ===========================================================================
# Design D: Bounded Concat + Overflow Detection
# ===========================================================================

class TestDetectingPLCEffectMonoid:
    """M_PLC_detecting — algebraically identical to Bounded, with diagnostics."""

    def setup_method(self):
        self.m = DetectingPLCEffectMonoid(capacity=20)

    @given(a=effect_list_strategy(), b=effect_list_strategy(), c=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        """Algebraic combine is identical to BoundedPLCEffectMonoid."""
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_left_identity(self, a):
        assert self.m.combine(self.m.empty(), a) == a

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_right_identity(self, a):
        assert self.m.combine(a, self.m.empty()) == a

    def test_overflow_detection(self):
        """Overflow is detected without breaking algebraic properties."""
        e = Effect(e_type=EffectType.EFF_IOT_PUB, target="t", value=1.0)
        a = [e] * 15
        b = [e] * 10
        result = self.m.combine_with_meta(a, b)
        assert len(result.effects) == 20
        assert result.overflow_occurred is True
        assert self.m.overflow_detected is True
        assert self.m.total_overflow_count == 1

    def test_no_overflow_no_flag(self):
        """No overflow -> flag stays false."""
        e = Effect(e_type=EffectType.EFF_IOT_PUB, target="t", value=1.0)
        result = self.m.combine_with_meta([e] * 5, [e] * 5)
        assert len(result.effects) == 10
        assert result.overflow_occurred is False
        assert self.m.overflow_detected is False

    def test_overflow_counter_accumulates(self):
        """Multiple overflows accumulate in counter."""
        e = Effect(e_type=EffectType.EFF_IOT_PUB, target="t", value=1.0)
        big = [e] * 15
        self.m.combine_with_meta(big, big)  # overflow 1
        self.m.combine_with_meta(big, big)  # overflow 2
        assert self.m.total_overflow_count == 2

    def test_reset_diagnostics(self):
        """Diagnostics can be reset independently of algebraic state."""
        e = Effect(e_type=EffectType.EFF_IOT_PUB, target="t", value=1.0)
        self.m.combine_with_meta([e] * 15, [e] * 15)
        assert self.m.overflow_detected is True
        self.m.reset_diagnostics()
        assert self.m.overflow_detected is False
        assert self.m.total_overflow_count == 0

    def test_combine_matches_bounded(self):
        """Algebraic combine produces identical results to BoundedPLCEffectMonoid."""
        bounded = BoundedPLCEffectMonoid(capacity=20)
        for a_len, b_len in [(5, 5), (15, 10), (20, 1), (0, 0), (10, 10)]:
            a = [Effect(e_type=EffectType.EFF_IOT_PUB, target=f"t{i}", value=float(i)) for i in range(a_len)]
            b = [Effect(e_type=EffectType.EFF_IOT_PUB, target=f"t{i+100}", value=float(i)) for i in range(b_len)]
            assert self.m.combine(a, b) == bounded.combine(a, b)

    @given(a=effect_list_strategy(max_size=10), b=effect_list_strategy(max_size=10))
    @hsettings(max_examples=200)
    def test_homomorphism_within_capacity(self, a, b):
        """Within capacity, homomorphism holds identically."""
        mw = MWStateMonoid()
        lhs = phi(self.m.combine(a, b))
        rhs = mw.combine(phi(a), phi(b))
        assert lhs == rhs


# ===========================================================================
# Cross-Design: Interface interchangeability
# ===========================================================================

class TestDesignInterchangeability:
    """All designs share the same algebraic interface."""

    def test_all_designs_have_empty_and_combine(self):
        designs = [
            BoundedPLCEffectMonoid(capacity=20),
            LWWEffectMonoid(),
            DetectingPLCEffectMonoid(capacity=20),
        ]
        for m in designs:
            assert hasattr(m, "empty"), f"{type(m).__name__} missing empty()"
            assert hasattr(m, "combine"), f"{type(m).__name__} missing combine()"

    def test_identity_is_empty_list_for_all(self):
        designs = [
            BoundedPLCEffectMonoid(capacity=20),
            LWWEffectMonoid(),
            DetectingPLCEffectMonoid(capacity=20),
        ]
        for m in designs:
            assert m.empty() == [], f"{type(m).__name__} identity is not []"
