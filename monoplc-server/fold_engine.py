"""
MonoPLC — StateMonoid Fold Engine (Design C: Time-Slice Replay)

Exploits Monoid associativity to enable capabilities impossible with a plain queue:

  1. Parallel fold:  split history → N workers → merge.
     Correctness guaranteed by associativity, not testing.

  2. Checkpoint reuse:  state_at(t) = checkpoint@t0 ⊕ fold(effects after t0 up to t).
     Correctness guaranteed by associativity — splitting the fold at the checkpoint
     boundary always produces the same result as folding from scratch.

  3. Time-travel:  reconstruct state at any historical timestamp.

Key formula:
    fold(e₁...eₙ) = φ([e₁]) ⊕_mw φ([e₂]) ⊕_mw ... ⊕_mw φ([eₙ])

    Because ⊕_mw is associative:
    (S₁ ⊕ S₂) ⊕ S₃ = S₁ ⊕ (S₂ ⊕ S₃)

    → can be split at any boundary, computed in parallel, and recombined.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import reduce
from typing import Any, Callable

from models import Effect, EffectType

logger = logging.getLogger(__name__)


@dataclass
class EffectLogEntry:
    """An effect with its consumption timestamp (used for time-slice queries)."""
    effect: Effect
    timestamp: datetime


@dataclass
class Checkpoint:
    """A frozen state snapshot at a point in the effect history."""
    timestamp: datetime
    state: Any
    effect_index: int  # index into persistent_log at checkpoint creation


@dataclass
class FoldResult:
    """Result of a fold operation with diagnostic metadata."""
    state: Any
    effects_folded: int
    method: str  # "sequential", "parallel", "checkpoint+delta"


@dataclass
class FoldComparison:
    """Side-by-side comparison of sequential vs parallel fold — must be identical."""
    sequential: Any
    parallel: Any
    identical: bool
    sequential_time_ms: float
    parallel_time_ms: float
    speedup_factor: float
    effects_processed: int
    parallel_workers: int


@dataclass
class IncrementalComparison:
    """Compares incremental update vs full re-fold — must be identical."""
    incremental_result: Any
    full_result: Any
    identical: bool
    incremental_time_ms: float
    full_time_ms: float
    speedup: float
    effects_total: int
    formula: str  # e.g. "state_old ⊕ φ([e_new])"


class FoldEngine:
    """
    StateMonoid Fold Engine — exploits associativity for parallel fold,
    checkpoint reuse, and time-travel state reconstruction.

    All fold operations use:
      - φ (homomorphism.phi) to map effects to state dicts
      - MWStateMonoid.combine (right-biased dict merge) to merge partial results
    """

    def __init__(self, monoid: Any, map_fn: Callable[[Effect], Any]):
        self._monoid = monoid
        self._map_fn = map_fn

    # ------------------------------------------------------------------
    # Sequential fold
    # ------------------------------------------------------------------

    def fold(self, effects: list[Effect]) -> Any:
        """
        Sequential left fold: foldl(⊕, ε, [φ(e) for e in effects])

        This is the baseline — produces the correct result by mapping
        each effect into the monoid carrier domain and combining them.
        """
        if not effects:
            return self._monoid.empty()
        
        mapped = [self._map_fn(e) for e in effects]
        return reduce(self._monoid.combine, mapped, self._monoid.empty())

    # ------------------------------------------------------------------
    # Parallel fold (Design C core capability)
    # ------------------------------------------------------------------

    def fold_parallel(
        self,
        effects: list[Effect],
        n_workers: int = 4,
    ) -> Any:
        """
        Parallel fold exploiting Monoid associativity.

        Splits the effect list into n_workers chunks, folds each chunk
        independently (potentially on different threads), then merges
        the partial results.

        Produces **identical** results to sequential fold — guaranteed by:
            (a ⊕ b) ⊕ c = a ⊕ (b ⊕ c)

        A plain queue with arbitrary callbacks CANNOT do this because
        the processing function might have hidden state dependencies.
        With a Monoid, associativity is a mathematical law.
        """
        if not effects:
            return self._monoid.empty()

        if len(effects) <= n_workers:
            return self.fold(effects)

        # Split into chunks
        chunks = self._split(effects, n_workers)

        # Fold each chunk independently (parallel)
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            partial_results = list(executor.map(self.fold, chunks))

        # Merge partial results — associativity guarantees correctness
        return reduce(self._monoid.combine, partial_results, self._monoid.empty())

    # ------------------------------------------------------------------
    # Time-travel: state_at(t)
    # ------------------------------------------------------------------

    def state_at(
        self,
        t: datetime,
        log: list[EffectLogEntry],
        checkpoints: list[Checkpoint],
    ) -> FoldResult:
        """
        Reconstruct the system state at historical timestamp t.

        Algorithm:
          1. Find the latest checkpoint BEFORE t (if any)
          2. Collect effects between checkpoint and t
          3. Fold those effects and merge with checkpoint state

        With checkpoint:
            state_at(t) = checkpoint@t0 ⊕_mw fold(effects between t0 and t)

        Without checkpoint:
            state_at(t) = fold(all effects before t)

        Checkpoint reuse correctness is guaranteed by associativity:
            fold(e₁...eₙ) = fold(e₁...eₖ) ⊕ fold(eₖ₊₁...eₙ)
                           = checkpoint    ⊕ fold(delta)
        """
        # Find latest checkpoint before t
        best_checkpoint: Checkpoint | None = None
        for cp in reversed(checkpoints):
            if cp.timestamp <= t:
                best_checkpoint = cp
                break

        if best_checkpoint is not None:
            # Fold only the delta after the checkpoint
            delta_effects = [
                entry.effect for entry in log[best_checkpoint.effect_index:]
                if entry.timestamp <= t
            ]
            delta_state = self.fold(delta_effects)
            final_state = self._monoid.combine(best_checkpoint.state, delta_state)
            return FoldResult(
                state=final_state,
                effects_folded=len(delta_effects),
                method=f"checkpoint+delta (checkpoint@{best_checkpoint.timestamp.isoformat()}, "
                       f"delta={len(delta_effects)} effects)",
            )
        else:
            # No checkpoint — fold everything from the start
            effects_before_t = [
                entry.effect for entry in log
                if entry.timestamp <= t
            ]
            state = self.fold(effects_before_t)
            return FoldResult(
                state=state,
                effects_folded=len(effects_before_t),
                method=f"full fold ({len(effects_before_t)} effects, no checkpoint)",
            )

    # ------------------------------------------------------------------
    # Time-window replay: state_between(t_from, t_to)
    # ------------------------------------------------------------------

    def state_between(
        self,
        t_from: datetime,
        t_to: datetime,
        log: list[EffectLogEntry],
    ) -> FoldResult:
        """
        Replay: fold only the effects in the time window [t_from, t_to].

        Returns the state that would result from ONLY those effects,
        showing what changed in that period.
        """
        window_effects = [
            entry.effect for entry in log
            if t_from <= entry.timestamp <= t_to
        ]
        state = self.fold(window_effects)
        return FoldResult(
            state=state,
            effects_folded=len(window_effects),
            method=f"window [{t_from.isoformat()}, {t_to.isoformat()}]",
        )

    # ------------------------------------------------------------------
    # Diagnostic: compare sequential vs parallel fold
    # ------------------------------------------------------------------

    def fold_compare(
        self,
        effects: list[Effect],
        n_workers: int = 4,
    ) -> FoldComparison:
        """
        Compute the same fold both sequentially and in parallel,
        then compare results.

        If the Monoid laws hold, these MUST be identical.
        This endpoint lets you demonstrate the associativity guarantee live.
        """
        import time
        
        t0 = time.perf_counter()
        seq = self.fold(effects)
        t1 = time.perf_counter()
        
        t2 = time.perf_counter()
        par = self.fold_parallel(effects, n_workers)
        t3 = time.perf_counter()
        
        seq_ms = (t1 - t0) * 1000.0
        par_ms = (t3 - t2) * 1000.0
        speedup = seq_ms / max(par_ms, 0.001)

        return FoldComparison(
            sequential=seq,
            parallel=par,
            identical=(seq == par),
            sequential_time_ms=seq_ms,
            parallel_time_ms=par_ms,
            speedup_factor=round(speedup, 2),
            effects_processed=len(effects),
            parallel_workers=n_workers,
        )

    # ------------------------------------------------------------------
    # Incremental Computation (Advantage #1)
    # ------------------------------------------------------------------

    def fold_incremental_compare(
        self,
        effects: list[Effect],
    ) -> IncrementalComparison:
        """
        Demonstrate O(1) incremental update vs O(n) full re-fold.

        Incremental:  state_old ⊕ φ(e_new)   — one combine operation
        Full:         fold(all_effects)      — fold from scratch

        Both produce identical results — guaranteed by Monoid associativity:
            fold([e₁,...,eₙ]) = fold([e₁,...,eₙ₋₁]) ⊕ φ(eₙ)
        """
        import time

        if len(effects) < 2:
            empty = self._monoid.empty()
            return IncrementalComparison(
                incremental_result=empty,
                full_result=empty,
                identical=True,
                incremental_time_ms=0.0,
                full_time_ms=0.0,
                speedup=1.0,
                effects_total=len(effects),
                formula="state_old ⊕ φ(e_new)",
            )

        # Incremental path: fold all-but-last, then combine with last
        state_old = self.fold(effects[:-1])
        e_new = effects[-1]

        t0 = time.perf_counter()
        incremental = self._monoid.combine(state_old, self._map_fn(e_new))
        t_incr = (time.perf_counter() - t0) * 1000

        # Full re-fold path
        t0 = time.perf_counter()
        full = self.fold(effects)
        t_full = (time.perf_counter() - t0) * 1000

        speedup = t_full / t_incr if t_incr > 0 else float('inf')

        return IncrementalComparison(
            incremental_result=incremental,
            full_result=full,
            identical=(incremental == full),
            incremental_time_ms=round(t_incr, 4),
            full_time_ms=round(t_full, 4),
            speedup=round(speedup, 2),
            effects_total=len(effects),
            formula="state_old ⊕ φ(e_new)",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split(lst: list, n: int) -> list[list]:
        """Split a list into n roughly equal chunks."""
        k, remainder = divmod(len(lst), n)
        chunks = []
        start = 0
        for i in range(n):
            end = start + k + (1 if i < remainder else 0)
            if start < end:
                chunks.append(lst[start:end])
            start = end
        return chunks
