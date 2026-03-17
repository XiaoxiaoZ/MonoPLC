"""
Property-based tests for Monoid laws and Homomorphism (Design D).

These tests prove algebraically that:
  1. PLCEffectMonoid satisfies associativity and identity
  2. MWStateMonoid satisfies associativity and identity
  3. φ is a valid Monoid homomorphism: φ(a ⊕₁ b) == φ(a) ⊕₂ φ(b)
  4. MonoidHomomorphism.transport_combine is equivalent to individual transport + combine
"""

from hypothesis import given, settings as hsettings
from hypothesis import strategies as st
import pytest

from monoid import PLCEffectMonoid, MWStateMonoid
from homomorphism import phi, create_monoplc_bridge, MonoidHomomorphism
from models import Effect, EffectType

from strategies import effect_list_strategy, effect_strategy


# ---------------------------------------------------------------------------
# PLCEffectMonoid — Monoid Law Tests
# ---------------------------------------------------------------------------

class TestPLCEffectMonoid:
    """M_PLC = (list[Effect], concat, []) must satisfy Monoid laws."""

    m = PLCEffectMonoid()

    @given(a=effect_list_strategy(), b=effect_list_strategy(), c=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        """(a ⊕ b) ⊕ c == a ⊕ (b ⊕ c)"""
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_left_identity(self, a):
        """ε ⊕ a == a"""
        assert self.m.combine(self.m.empty(), a) == a

    @given(a=effect_list_strategy())
    @hsettings(max_examples=50)
    def test_right_identity(self, a):
        """a ⊕ ε == a"""
        assert self.m.combine(a, self.m.empty()) == a


# ---------------------------------------------------------------------------
# MWStateMonoid — Monoid Law Tests
# ---------------------------------------------------------------------------

class TestMWStateMonoid:
    """M_MW = (dict, right-biased merge, {}) must satisfy Monoid laws."""

    m = MWStateMonoid()

    @given(
        a=effect_list_strategy(),
        b=effect_list_strategy(),
        c=effect_list_strategy(),
    )
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        """(φ(a) ⊕ φ(b)) ⊕ φ(c) == φ(a) ⊕ (φ(b) ⊕ φ(c))

        We test with phi-derived dicts to use realistic state data.
        """
        da, db, dc = phi(a), phi(b), phi(c)
        lhs = self.m.combine(self.m.combine(da, db), dc)
        rhs = self.m.combine(da, self.m.combine(db, dc))
        assert lhs == rhs

    def test_left_identity(self):
        """ε ⊕ a == a"""
        a = {"EFF_VALVE_CTRL.CoolingValve": {"value": 1.0, "payload": ""}}
        assert self.m.combine(self.m.empty(), a) == a

    def test_right_identity(self):
        """a ⊕ ε == a"""
        a = {"EFF_VALVE_CTRL.CoolingValve": {"value": 1.0, "payload": ""}}
        assert self.m.combine(a, self.m.empty()) == a


# ---------------------------------------------------------------------------
# Homomorphism φ — Structure Preservation Tests (Design D core)
# ---------------------------------------------------------------------------

class TestHomomorphismPhi:
    """
    φ: M_PLC → M_MW must satisfy:
      φ(a ⊕₁ b) == φ(a) ⊕₂ φ(b)     (homomorphism law)
      φ(ε₁)     == ε₂                 (identity preservation)
    """

    plc = PLCEffectMonoid()
    mw = MWStateMonoid()

    @given(a=effect_list_strategy(), b=effect_list_strategy())
    @hsettings(max_examples=200)
    def test_homomorphism_law(self, a, b):
        """φ(a ⊕_plc b) == φ(a) ⊕_mw φ(b)

        This is THE core property of Design D: two independently designed
        Monoids, connected by φ, are guaranteed to produce consistent results
        regardless of which side you combine on.
        """
        # Left-hand side: combine in M_PLC, then map via φ
        lhs = phi(self.plc.combine(a, b))

        # Right-hand side: map each via φ, then combine in M_MW
        rhs = self.mw.combine(phi(a), phi(b))

        assert lhs == rhs

    def test_identity_preservation(self):
        """φ(ε_plc) == ε_mw"""
        assert phi(self.plc.empty()) == self.mw.empty()

    def test_homomorphism_with_overlapping_keys(self):
        """Verify φ homomorphism when effects share the same (e_type, target).

        Last-writer-wins means order matters — but the homomorphism still holds
        because list concat preserves order and dict merge is right-biased.
        """
        e1 = Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0)
        e2 = Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=0.0)

        a = [e1]
        b = [e2]

        lhs = phi(self.plc.combine(a, b))
        rhs = self.mw.combine(phi(a), phi(b))
        assert lhs == rhs

        # The result should have V1=0.0 (last writer wins)
        assert lhs["EFF_VALVE_CTRL.V1"]["value"] == 0.0

    def test_homomorphism_with_filtered_effects(self):
        """φ filters out EFF_SYSTEM_TICK and EFF_NONE — verify this
        doesn't break the homomorphism."""
        a = [Effect(e_type=EffectType.EFF_SYSTEM_TICK, target="", payload="", value=0.0)]
        b = [Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="", value=1.0)]

        lhs = phi(self.plc.combine(a, b))
        rhs = self.mw.combine(phi(a), phi(b))
        assert lhs == rhs


# ---------------------------------------------------------------------------
# MonoidHomomorphism class — Integration Tests (Design D API)
# ---------------------------------------------------------------------------

class TestMonoidHomomorphismClass:
    """Test the generic MonoidHomomorphism class that bridges two Monoids."""

    bridge = create_monoplc_bridge()

    @given(a=effect_list_strategy(), b=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_verify_holds(self, a, b):
        """bridge.verify(a, b) should always hold for our φ."""
        result = self.bridge.verify(a, b)
        assert result.holds, f"Homomorphism violated: {result.description}"

    def test_verify_identity(self):
        """bridge.verify_identity() should hold."""
        result = self.bridge.verify_identity()
        assert result.holds, f"Identity preservation violated: {result.description}"

    @given(a=effect_list_strategy(), b=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_transport_combine_equivalence(self, a, b):
        """transport_combine([a, b]) == target.combine(transport(a), transport(b))

        This demonstrates the key advantage: you can either
          1. Combine on the source side, then transport (what transport_combine does)
          2. Transport each independently, then combine on the target side
        Both produce the same result — that's the homomorphism guarantee.
        """
        # Path 1: transport_combine
        result_via_combine = self.bridge.transport_combine([a, b])

        # Path 2: transport each, then combine
        result_via_separate = self.bridge.target.combine(
            self.bridge.transport(a),
            self.bridge.transport(b),
        )

        assert result_via_combine == result_via_separate

    def test_transport_single_effect(self):
        """transport() on a single-effect list should produce the correct dict."""
        effects = [Effect(e_type=EffectType.EFF_VALVE_CTRL, target="V1", payload="open", value=1.0)]
        result = self.bridge.transport(effects)
        assert result == {"EFF_VALVE_CTRL.V1": {"value": 1.0, "payload": "open"}}

    def test_transport_empty(self):
        """transport(ε) should produce ε."""
        result = self.bridge.transport([])
        assert result == {}


# ---------------------------------------------------------------------------
# SumMonoid — Monoid Law Tests
# ---------------------------------------------------------------------------

class TestSumMonoid:
    """M_Sum = (int, +, 0) must satisfy Monoid laws."""

    from monoid import SumMonoid
    m = SumMonoid()

    @given(a=st.integers(-1000, 1000), b=st.integers(-1000, 1000), c=st.integers(-1000, 1000))
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        """(a + b) + c == a + (b + c)"""
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs

    @given(a=st.integers(-1000, 1000))
    @hsettings(max_examples=50)
    def test_left_identity(self, a):
        """0 + a == a"""
        assert self.m.combine(self.m.empty(), a) == a

    @given(a=st.integers(-1000, 1000))
    @hsettings(max_examples=50)
    def test_right_identity(self, a):
        """a + 0 == a"""
        assert self.m.combine(a, self.m.empty()) == a


# ---------------------------------------------------------------------------
# ProductMonoid — Monoid Law Tests
# ---------------------------------------------------------------------------

class TestProductMonoid:
    """
    ProductMonoid(MWStateMonoid, SumMonoid, SumMonoid) must satisfy Monoid laws.
    If each sub-monoid is a Monoid, the product is automatically a Monoid.
    """

    from monoid import SumMonoid as _Sum, ProductMonoid as _Prod
    m = _Prod(MWStateMonoid(), _Sum(), _Sum())

    @given(
        a_effects=effect_list_strategy(),
        b_effects=effect_list_strategy(),
        c_effects=effect_list_strategy(),
        a_count=st.integers(0, 100),
        b_count=st.integers(0, 100),
        c_count=st.integers(0, 100),
        a_alarm=st.integers(0, 50),
        b_alarm=st.integers(0, 50),
        c_alarm=st.integers(0, 50),
    )
    @hsettings(max_examples=50)
    def test_associativity(self, a_effects, b_effects, c_effects,
                           a_count, b_count, c_count, a_alarm, b_alarm, c_alarm):
        """(a ⊕ b) ⊕ c == a ⊕ (b ⊕ c) — component-wise."""
        a = (phi(a_effects), a_count, a_alarm)
        b = (phi(b_effects), b_count, b_alarm)
        c = (phi(c_effects), c_count, c_alarm)
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs

    def test_identity(self):
        """ε_product == (ε_state, ε_sum, ε_sum)"""
        empty = self.m.empty()
        assert empty == ({}, 0, 0)
        a = ({"key": {"value": 1.0}}, 5, 2)
        assert self.m.combine(self.m.empty(), a) == a
        assert self.m.combine(a, self.m.empty()) == a
