"""
MonoPLC Bridge Server — Algebra Endpoints (Design C & D)

Exposes the algebraic capabilities as REST API:
  - Design C: time-travel (state_at), replay (state_between), parallel fold comparison
  - Design D: live homomorphism verification
"""

from dataclasses import asdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/algebra", tags=["Algebra (Design C & D)"])


# ------------------------------------------------------------------
# Design C: Time-travel & fold
# ------------------------------------------------------------------

@router.get(
    "/state/at",
    summary="Time-travel: reconstruct state at timestamp t (Design C)",
)
async def state_at(
    t: str = Query(..., description="ISO 8601 timestamp, e.g. 2026-03-17T09:04:00"),
):
    """
    Reconstruct the system state at historical timestamp t.

    Uses Monoid associativity to enable checkpoint reuse:
        state_at(t) = checkpoint@t0 ⊕ fold(effects between t0 and t)

    Without associativity, you would have to fold ALL effects from the beginning.
    """
    from main import app_state

    # Strip tzinfo because local Python uses naive datetime.now()
    ts = datetime.fromisoformat(t).replace(tzinfo=None)
    result = app_state.state_store.state_at(ts)
    d = asdict(result)
    state_dict, count, alarms = d.pop("state")
    d["state"] = state_dict
    d["effect_count"] = count
    d["alarm_count"] = alarms
    return d


@router.get(
    "/state/replay",
    summary="Replay: fold effects in a time window (Design C)",
)
async def state_replay(
    t_from: str = Query(..., alias="from", description="Start timestamp (ISO 8601)"),
    t_to: str = Query(..., alias="to", description="End timestamp (ISO 8601)"),
):
    """
    Replay effects in the time window [from, to].

    Returns the state produced by ONLY those effects — showing what changed
    in that period.
    """
    from main import app_state

    ts_from = datetime.fromisoformat(t_from).replace(tzinfo=None)
    ts_to = datetime.fromisoformat(t_to).replace(tzinfo=None)
    result = app_state.state_store.state_between(ts_from, ts_to)
    d = asdict(result)
    state_dict, count, alarms = d.pop("state")
    d["state"] = state_dict
    d["effect_count"] = count
    d["alarm_count"] = alarms
    return d


@router.get(
    "/fold/compare",
    summary="Compare sequential vs parallel fold (Design C)",
)
async def fold_compare(
    workers: int = Query(default=4, ge=1, le=16, description="Number of parallel workers"),
):
    """
    Compute the same fold both sequentially and in parallel, then compare.

    Monoid associativity GUARANTEES identical results regardless of how
    the effect list is partitioned. A plain queue with arbitrary callbacks
    cannot make this guarantee.

    This endpoint lets you demonstrate the associativity advantage live.
    """
    from main import app_state

    result = app_state.state_store.fold_compare(workers)
    d = asdict(result)
    
    seq_dict, seq_count, seq_alarms = d.pop("sequential")
    par_dict, par_count, par_alarms = d.pop("parallel")
    
    d["sequential"] = {
        "state": seq_dict,
        "effect_count": seq_count,
        "alarm_count": seq_alarms
    }
    d["parallel"] = {
        "state": par_dict,
        "effect_count": par_count,
        "alarm_count": par_alarms
    }
    
    return d


@router.get(
    "/checkpoints",
    summary="List all stored checkpoints (Design C)",
)
async def list_checkpoints():
    """List all checkpoints with timestamps and metadata."""
    from main import app_state

    return {"checkpoints": app_state.state_store.get_checkpoints()}


@router.post(
    "/checkpoint",
    summary="Manually trigger a checkpoint (Design C)",
)
async def create_checkpoint():
    """
    Create a state checkpoint at the current moment.

    Checkpoints accelerate future state_at(t) queries by providing
    a pre-computed partial fold that can be reused (associativity guarantee).
    """
    from main import app_state

    return app_state.state_store.create_checkpoint_now()


# ------------------------------------------------------------------
# Design D: Homomorphism verification
# ------------------------------------------------------------------

@router.get(
    "/homomorphism/verify",
    summary="Live-verify φ(A⊕B) = φ(A)⊕φ(B) on real data (Design D)",
)
async def verify_homomorphism(
    split: Optional[int] = Query(
        default=None,
        description="Split point in the persistent log (default: midpoint)",
    ),
):
    """
    Verify the Monoid homomorphism law on real effect data.

    Takes the persistent effect log, splits it at the given point, and checks:
        φ(left ++ right) == combine(φ(left), φ(right))

    Because M_PLC and M_MW are designed independently, this check is the
    ONLY thing needed to guarantee cross-layer correctness.

    Also verifies identity preservation: φ(ε₁) == ε₂
    """
    from main import app_state

    store = app_state.state_store
    log = store._persistent_log
    bridge = store.bridge

    if len(log) < 2:
        return {
            "error": "Need at least 2 effects in persistent log to verify",
            "persistent_log_size": len(log),
        }

    effects = [entry.effect for entry in log]

    # Split into two halves
    split_point = split if split is not None else len(effects) // 2
    split_point = max(1, min(split_point, len(effects) - 1))

    a = effects[:split_point]
    b = effects[split_point:]

    # Verify homomorphism law: φ(a ⊕₁ b) == φ(a) ⊕₂ φ(b)
    combine_result = bridge.verify(a, b)

    # Verify identity preservation: φ(ε₁) == ε₂
    identity_result = bridge.verify_identity()

    return {
        "homomorphism_law": {
            "description": "φ(A ⊕_plc B) == φ(A) ⊕_mw φ(B)",
            "holds": combine_result.holds,
            "split_point": split_point,
            "left_size": len(a),
            "right_size": len(b),
            "lhs_keys": len(combine_result.lhs),
            "rhs_keys": len(combine_result.rhs),
        },
        "identity_preservation": {
            "description": "φ(ε_plc) == ε_mw",
            "holds": identity_result.holds,
        },
        "persistent_log_size": len(effects),
        "conclusion": combine_result.description + " | " + identity_result.description,
    }


# ------------------------------------------------------------------
# Advantage #1: Incremental Computation
# ------------------------------------------------------------------

@router.get(
    "/incremental/compare",
    summary="Compare O(1) incremental update vs O(n) full re-fold (Advantage #1)",
)
async def incremental_compare():
    """
    Demonstrate Monoid incremental computation.

    Incremental:  state_old ⊕ φ([e_new])   — ONE combine operation
    Full re-fold: fold(all_effects)          — fold everything from scratch

    Both produce **identical** results — guaranteed by associativity:
        fold([e₁...eₙ]) = fold([e₁...eₙ₋₁]) ⊕ φ([eₙ])

    A plain callback queue CANNOT guarantee this without full regression testing.
    With a Monoid, it is a mathematical law.
    """
    from main import app_state

    log = app_state.state_store._persistent_log
    if len(log) < 2:
        return {
            "error": "Need at least 2 effects in persistent log",
            "persistent_log_size": len(log),
        }

    effects = [entry.effect for entry in log]
    result = app_state.state_store._fold_engine.fold_incremental_compare(effects)

    return {
        "incremental_result": {
            "state_keys": len(result.incremental_result[0]),
            "state": result.incremental_result[0],
            "effect_count": result.incremental_result[1],
            "alarm_count": result.incremental_result[2],
        },
        "full_result": {
            "state_keys": len(result.full_result[0]),
            "state": result.full_result[0],
            "effect_count": result.full_result[1],
            "alarm_count": result.full_result[2],
        },
        "identical": result.identical,
        "incremental_time_ms": result.incremental_time_ms,
        "full_time_ms": result.full_time_ms,
        "speedup": result.speedup,
        "effects_total": result.effects_total,
        "formula": result.formula,
        "advantage": "O(1) incremental update vs O(n) full re-fold — correctness by associativity",
    }


# ------------------------------------------------------------------
# Additional endpoint for Time-Travel UI
# ------------------------------------------------------------------

@router.get(
    "/log/range",
    summary="Get time bounds of persistent log",
)
async def log_range():
    """Get the start and end timestamp of the persistent log for UI slider."""
    from main import app_state

    log = app_state.state_store._persistent_log
    if not log:
        return None

    return {
        "start_time": log[0].timestamp.isoformat(),
        "end_time": log[-1].timestamp.isoformat(),
        "effect_count": len(log),
    }
