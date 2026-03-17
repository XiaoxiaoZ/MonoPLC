"""
MonoPLC — Named Monoid Types (Design D: Cross-Layer Decoupling)

Defines the two independently designed Monoids that operate across the ADS boundary:
  - PLCEffectMonoid (M_PLC): list concatenation, mirrors FC_CombineEffects on PLC side
  - MWStateMonoid   (M_MW):  right-biased dict merge, mirrors StateStore._consume_effect

Each Monoid is designed independently with its own combine/identity,
optimised for its runtime constraints.  A MonoidHomomorphism (see homomorphism.py)
automatically bridges the two without either side knowing about the other.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from models import Effect

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Abstract Monoid Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Monoid(Protocol[T]):
    """
    Abstract Monoid: (S, combine, empty)

    Any concrete Monoid must satisfy:
      1. Associativity:  combine(combine(a, b), c) == combine(a, combine(b, c))
      2. Identity:       combine(a, empty()) == a == combine(empty(), a)
    """

    def empty(self) -> T: ...
    def combine(self, a: T, b: T) -> T: ...


# ---------------------------------------------------------------------------
# M_PLC — PLC-side Effect List Monoid
# ---------------------------------------------------------------------------

class PLCEffectMonoid:
    """
    M_PLC = (list[Effect], concat, [])

    Mirrors the PLC-side DUT_Effect_Monoid / FC_CombineEffects:
      - Carrier S:  list of Effect structs
      - combine ⊕:  list concatenation (order-preserving)
      - identity ε: empty list []

    Designed for real-time array operations on IEC 61131-3 runtime.
    """

    def empty(self) -> list[Effect]:
        return []

    def combine(self, a: list[Effect], b: list[Effect]) -> list[Effect]:
        return a + b


# ---------------------------------------------------------------------------
# M_MW — Middleware State Dict Monoid
# ---------------------------------------------------------------------------

class MWStateMonoid:
    """
    M_MW = (dict[str, dict[str, Any]], right-biased merge, {})

    Mirrors the Python-side state reconstruction (last-writer-wins):
      - Carrier S:  dict keyed by composite string "EFF_TYPE.target"
      - combine ⊕:  right-biased dict merge ({**a, **b})
      - identity ε: empty dict {}

    Designed for fast key-value lookups in the middleware layer.
    Right-biased means keys in b overwrite keys in a — this is the
    last-writer-wins semantic that makes the fold order-sensitive but
    still associative: {**({**a, **b}), **c} == {**a, **({**b, **c})}.
    """

    def empty(self) -> dict[str, dict[str, Any]]:
        return {}

    def combine(
        self,
        a: dict[str, dict[str, Any]],
        b: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return {**a, **b}


# ---------------------------------------------------------------------------
# M_Sum — Integer Sum Monoid (for counting / aggregation)
# ---------------------------------------------------------------------------

class SumMonoid:
    """
    M_Sum = (int, +, 0)

    The simplest useful Monoid: integer addition.
    Used as a sub-monoid in ProductMonoid for counting effects, alarms, etc.
    """

    def empty(self) -> int:
        return 0

    def combine(self, a: int, b: int) -> int:
        return a + b


# ---------------------------------------------------------------------------
# M_Max — Max Monoid (for tracking peak values)
# ---------------------------------------------------------------------------

class MaxMonoid:
    """
    M_Max = (float, max, -∞)

    Tracks the maximum value seen. Identity is -∞ (any real value wins).
    """

    def empty(self) -> float:
        return float('-inf')

    def combine(self, a: float, b: float) -> float:
        return max(a, b)


# ---------------------------------------------------------------------------
# Product Monoid — Component-wise composition of N sub-monoids
# ---------------------------------------------------------------------------

class ProductMonoid:
    """
    Given Monoids M₁, M₂, ..., Mₙ, the Product Monoid is:

        (M₁ × M₂ × ... × Mₙ,  component-wise combine,  (ε₁, ε₂, ..., εₙ))

    combine((a₁,...,aₙ), (b₁,...,bₙ)) = (m₁.combine(a₁,b₁), ..., mₙ.combine(aₙ,bₙ))
    empty() = (m₁.empty(), ..., mₙ.empty())

    Key advantage: ONE fold pass produces N independent aggregation views.
    Each sub-monoid is designed independently — zero coupling between dimensions.
    """

    def __init__(self, *monoids):
        self.monoids = monoids

    def empty(self) -> tuple:
        return tuple(m.empty() for m in self.monoids)

    def combine(self, a: tuple, b: tuple) -> tuple:
        return tuple(
            m.combine(ai, bi)
            for m, ai, bi in zip(self.monoids, a, b)
        )
