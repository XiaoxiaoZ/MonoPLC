"""
MonoPLC — Monoid Homomorphism φ and Cross-Layer Bridge (Design D)

Core idea: two Monoids are designed independently (each with its own combine/identity),
and a structure-preserving map φ automatically bridges them.

    φ: M_PLC → M_MW
    Law:  φ(A ⊕₁ B) == φ(A) ⊕₂ φ(B)      (homomorphism preservation)
          φ(ε₁)     == ε₂                   (identity preservation)

This means:
  - PLC side can evolve FC_CombineEffects independently
  - Python side can evolve state merge logic independently
  - Cross-layer correctness is a finite algebraic check (verify φ), not full integration testing
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from typing import Any, Callable, Generic, TypeVar

from models import Effect, EffectType
from monoid import Monoid, PLCEffectMonoid, MWStateMonoid

A = TypeVar("A")
B = TypeVar("B")


# ---------------------------------------------------------------------------
# φ : M_PLC → M_MW  (the concrete homomorphism for MonoPLC)
# ---------------------------------------------------------------------------

def phi(effects: list[Effect]) -> dict[str, dict[str, Any]]:
    """
    Monoid homomorphism: maps a PLC-side effect list (M_PLC carrier)
    to a middleware-side state dict (M_MW carrier).

    This is the same logic previously embedded in StateStore._consume_effect,
    extracted as a **pure function** so it can be tested, composed, and verified
    independently.

    Homomorphism law:
        phi(plc.combine(a, b)) == mw.combine(phi(a), phi(b))

    i.e. concatenating two effect lists then mapping produces the same result
    as mapping each list separately then merging the dicts.
    """
    result: dict[str, dict[str, Any]] = {}
    for effect in effects:
        # Skip noise — same filter as the original _consume_effect
        if effect.e_type in (EffectType.EFF_SYSTEM_TICK, EffectType.EFF_NONE):
            continue

        # Composite key: "EFF_VALVE_CTRL.CoolingValve"
        composite_key = (
            f"{effect.e_type.name}.{effect.target}" if effect.target
            else effect.e_type.name
        )

        # Last-writer-wins: later effects in the list overwrite earlier ones
        result[composite_key] = {
            "value": effect.value,
            "payload": effect.payload,
        }

    return result


# ---------------------------------------------------------------------------
# Homomorphism verification result
# ---------------------------------------------------------------------------

@dataclass
class HomomorphismVerification:
    """Result of verifying the homomorphism law for a given pair (a, b)."""
    lhs: Any             # φ(a ⊕₁ b)
    rhs: Any             # φ(a) ⊕₂ φ(b)
    holds: bool          # lhs == rhs
    description: str = ""


# ---------------------------------------------------------------------------
# Generic MonoidHomomorphism — independent design, automatic bridging
# ---------------------------------------------------------------------------

class MonoidHomomorphism(Generic[A, B]):
    """
    Generic Monoid Homomorphism: given two independently designed Monoids
    and a mapping φ, automatically provides a verified cross-layer pipeline.

    Usage:
        # 1. Design each Monoid independently
        plc_monoid = PLCEffectMonoid()   # combine = list concat, empty = []
        mw_monoid  = MWStateMonoid()     # combine = dict merge,  empty = {}

        # 2. Provide φ mapping
        bridge = MonoidHomomorphism(plc_monoid, mw_monoid, phi)

        # 3. Automatic cross-layer operations
        state  = bridge.transport(effects)            # φ(effects)
        result = bridge.transport_combine([a, b])     # φ(a ⊕₁ b)
        check  = bridge.verify(a, b)                  # prove φ(a⊕b) == φ(a)⊕φ(b)
    """

    def __init__(
        self,
        source: Monoid[A],
        target: Monoid[B],
        phi: Callable[[A], B],
    ):
        self.source = source
        self.target = target
        self.phi = phi

    # --- Core transport ---------------------------------------------------

    def transport(self, value: A) -> B:
        """Map a source-side value to the target side via φ."""
        return self.phi(value)

    def transport_combine(self, values: list[A]) -> B:
        """
        Combine multiple source-side values using ⊕₁, then map via φ.

        Equivalent (by homomorphism law) to:
            reduce(target.combine, [φ(v) for v in values], target.empty())

        The caller can choose either path — the algebra guarantees identical results.
        """
        source_combined = reduce(
            self.source.combine,
            values,
            self.source.empty(),
        )
        return self.phi(source_combined)

    # --- Verification -----------------------------------------------------

    def verify(self, a: A, b: A) -> HomomorphismVerification:
        """
        Verify the homomorphism law for a concrete pair (a, b):

            φ(a ⊕₁ b)  ==  φ(a) ⊕₂ φ(b)

        Because the two Monoids are designed independently, this check is the
        **only** thing needed to guarantee cross-layer correctness.  No full
        integration testing required.
        """
        lhs = self.phi(self.source.combine(a, b))
        rhs = self.target.combine(self.phi(a), self.phi(b))
        return HomomorphismVerification(
            lhs=lhs,
            rhs=rhs,
            holds=(lhs == rhs),
            description=(
                "φ(a ⊕₁ b) == φ(a) ⊕₂ φ(b): "
                + ("HOLDS ✔" if lhs == rhs else "VIOLATED ✘")
            ),
        )

    def verify_identity(self) -> HomomorphismVerification:
        """
        Verify identity preservation: φ(ε₁) == ε₂

        The homomorphism must map the source identity to the target identity.
        """
        lhs = self.phi(self.source.empty())
        rhs = self.target.empty()
        return HomomorphismVerification(
            lhs=lhs,
            rhs=rhs,
            holds=(lhs == rhs),
            description=(
                "φ(ε₁) == ε₂: "
                + ("HOLDS ✔" if lhs == rhs else "VIOLATED ✘")
            ),
        )


# ---------------------------------------------------------------------------
# Pre-built bridge instance for MonoPLC
# ---------------------------------------------------------------------------

def create_monoplc_bridge() -> MonoidHomomorphism[list[Effect], dict[str, dict[str, Any]]]:
    """
    Create the standard MonoPLC cross-layer bridge.

    M_PLC (list concat) ---φ---> M_MW (dict merge)

    Each Monoid is designed independently; φ bridges them automatically.
    """
    return MonoidHomomorphism(
        source=PLCEffectMonoid(),
        target=MWStateMonoid(),
        phi=phi,
    )
