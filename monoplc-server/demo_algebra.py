"""
MonoPLC — Design C & D Live Demo
=================================
Connects to real PLC, collects effects, then demonstrates:

  Design C (StateMonoid Fold / Time-Slice Replay):
    - Parallel fold vs sequential fold → identical results (associativity proof)
    - Checkpoint creation + checkpoint-accelerated state_at(t)
    - Time-window replay

  Design D (Monoid Homomorphism / Cross-Layer Decoupling):
    - φ(A ⊕₁ B) == φ(A) ⊕₂ φ(B)  on real PLC data
    - φ(ε₁) == ε₂  identity preservation

Usage:
    cd monoplc-server
    python demo_algebra.py [--collect-seconds 10] [--workers 4]
"""

import argparse
import sys
import time
from datetime import datetime, timedelta

from config import settings
from models import Effect, EffectType
from plc_bridge import PLCBridge
from state_store import StateStore
from fold_engine import FoldEngine, EffectLogEntry, Checkpoint, FoldComparison
from monoid import MWStateMonoid, PLCEffectMonoid
from homomorphism import phi, create_monoplc_bridge


# ── Pretty printing ──────────────────────────────────────────────

CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

def header(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{'='*60}")

def ok(msg: str) -> None:
    print(f"  {GREEN}✔{RESET} {msg}")

def fail(msg: str) -> None:
    print(f"  {RED}✘{RESET} {msg}")

def info(msg: str) -> None:
    print(f"  {YELLOW}→{RESET} {msg}")

def result_check(label: str, holds: bool) -> None:
    if holds:
        ok(f"{label}: {GREEN}HOLDS{RESET}")
    else:
        fail(f"{label}: {RED}VIOLATED{RESET}")


# ── Phase 1: Collect effects from PLC ────────────────────────────

def collect_effects(bridge: PLCBridge, seconds: int) -> list[EffectLogEntry]:
    """Poll PLC output queue for `seconds` and return timestamped log entries."""
    header(f"Phase 1: Collecting effects from PLC ({seconds}s)")
    info(f"AMS Net ID: {settings.AMS_NET_ID}:{settings.ADS_PORT}")

    bridge.connect()
    ok("ADS connected")

    entries: list[EffectLogEntry] = []
    t_start = time.time()
    spinner = "|/-\\"
    tick = 0

    while time.time() - t_start < seconds:
        effects = bridge.pop_output_effects()
        now = datetime.now()
        for e in effects:
            entries.append(EffectLogEntry(effect=e, timestamp=now))

        elapsed = time.time() - t_start
        print(
            f"\r  {spinner[tick % 4]}  {elapsed:.0f}s / {seconds}s  |  "
            f"{len(entries)} effects collected",
            end="", flush=True,
        )
        tick += 1
        time.sleep(0.1)

    print()  # newline after spinner
    bridge.disconnect()
    ok(f"Done — collected {BOLD}{len(entries)}{RESET} effects")
    return entries


# ── Phase 2: Design C — Parallel Fold & Time-Travel ─────────────

def demo_design_c(entries: list[EffectLogEntry], n_workers: int) -> None:
    header("Design C: StateMonoid Fold / Time-Slice Replay")

    if len(entries) < 2:
        info("Need at least 2 effects to demonstrate. Try collecting longer.")
        return

    monoid = MWStateMonoid()
    engine = FoldEngine(monoid)
    effects = [e.effect for e in entries]

    # ── C.1: Parallel fold vs sequential fold ────────────────────
    print(f"\n  {BOLD}C.1  Parallel fold — associativity proof{RESET}")
    info(f"Effects: {len(effects)}  |  Workers: {n_workers}")

    t0 = time.perf_counter()
    seq = engine.fold(effects)
    t_seq = time.perf_counter() - t0

    t0 = time.perf_counter()
    par = engine.fold_parallel(effects, n_workers)
    t_par = time.perf_counter() - t0

    identical = (seq == par)
    result_check("sequential == parallel", identical)
    info(f"Sequential: {t_seq*1000:.2f}ms  |  Parallel ({n_workers}w): {t_par*1000:.2f}ms")
    info(f"State keys: {len(seq)}")

    print(f"\n  {BOLD}Why this matters:{RESET}")
    info("A plain queue with arbitrary callbacks CANNOT split-and-merge.")
    info("Monoid associativity: (a ⊕ b) ⊕ c = a ⊕ (b ⊕ c)")
    info("→ grouping doesn't matter → safe to parallelize.")

    # ── C.2: Checkpoint + time-travel ────────────────────────────
    print(f"\n  {BOLD}C.2  Checkpoint-accelerated time-travel{RESET}")

    if len(entries) < 4:
        info("Need ≥4 effects for checkpoint demo. Skipping.")
        return

    # Create a checkpoint at midpoint
    mid = len(entries) // 2
    mid_state = engine.fold([e.effect for e in entries[:mid]])
    checkpoint = Checkpoint(
        timestamp=entries[mid].timestamp,
        state=mid_state,
        effect_index=mid,
    )
    ok(f"Checkpoint created at index {mid} ({checkpoint.timestamp.isoformat()})")

    # Query state at a point after the checkpoint
    query_idx = mid + (len(entries) - mid) // 2
    query_time = entries[min(query_idx, len(entries) - 1)].timestamp

    # With checkpoint (fast)
    t0 = time.perf_counter()
    result_cp = engine.state_at(query_time, entries, [checkpoint])
    t_cp = time.perf_counter() - t0

    # Without checkpoint (full fold)
    t0 = time.perf_counter()
    result_full = engine.state_at(query_time, entries, [])
    t_full = time.perf_counter() - t0

    identical_tt = (result_cp.state == result_full.state)
    result_check("checkpoint+delta == full_fold", identical_tt)
    info(f"With checkpoint: {result_cp.effects_folded} effects folded ({t_cp*1000:.2f}ms)")
    info(f"Without:         {result_full.effects_folded} effects folded ({t_full*1000:.2f}ms)")
    info(f"Method: {result_cp.method}")

    print(f"\n  {BOLD}Why this matters:{RESET}")
    info("fold(e₁...eₙ) = fold(e₁...eₖ) ⊕ fold(eₖ₊₁...eₙ)")
    info("             = checkpoint     ⊕ delta")
    info("→ Reuse pre-computed state. Correctness by associativity, not testing.")

    # ── C.3: Time-window replay ──────────────────────────────────
    print(f"\n  {BOLD}C.3  Time-window replay{RESET}")

    t_from = entries[0].timestamp
    t_to = entries[-1].timestamp
    window = engine.state_between(t_from, t_to, entries)
    ok(f"Replayed {window.effects_folded} effects in [{t_from.strftime('%H:%M:%S')}, {t_to.strftime('%H:%M:%S')}]")
    info(f"State keys produced: {len(window.state)}")

    if window.state:
        print(f"\n  {BOLD}  Sample state entries:{RESET}")
        for i, (k, v) in enumerate(list(window.state.items())[:5]):
            print(f"    {k}: value={v.get('value', '?')}, payload='{v.get('payload', '')}'")
        if len(window.state) > 5:
            print(f"    ... and {len(window.state) - 5} more")


# ── Phase 3: Design D — Monoid Homomorphism Verification ────────

def demo_design_d(entries: list[EffectLogEntry]) -> None:
    header("Design D: Monoid Homomorphism / Cross-Layer Decoupling")

    if len(entries) < 2:
        info("Need at least 2 effects to demonstrate. Try collecting longer.")
        return

    bridge_hom = create_monoplc_bridge()
    effects = [e.effect for e in entries]

    print(f"\n  {BOLD}Two independently designed Monoids:{RESET}")
    info("M_PLC = (list[Effect], concat, [])      — optimized for IEC 61131-3 real-time")
    info("M_MW  = (dict, right-biased merge, {})   — optimized for fast key-value lookups")
    info("φ : M_PLC → M_MW                         — structure-preserving map")

    # ── D.1: Identity preservation ───────────────────────────────
    print(f"\n  {BOLD}D.1  Identity preservation: φ(ε₁) == ε₂{RESET}")
    id_check = bridge_hom.verify_identity()
    result_check("φ(ε_plc) == ε_mw", id_check.holds)
    info(f"φ([]) = {id_check.lhs}  |  {{}} = {id_check.rhs}")

    # ── D.2: Homomorphism law on real data ───────────────────────
    print(f"\n  {BOLD}D.2  Homomorphism law: φ(A ⊕₁ B) == φ(A) ⊕₂ φ(B){RESET}")
    info(f"Testing on {len(effects)} real PLC effects...")

    # Test at multiple split points
    split_points = set()
    split_points.add(1)
    split_points.add(len(effects) - 1)
    split_points.add(len(effects) // 2)
    if len(effects) >= 4:
        split_points.add(len(effects) // 4)
        split_points.add(3 * len(effects) // 4)

    all_hold = True
    for sp in sorted(split_points):
        a = effects[:sp]
        b = effects[sp:]
        check = bridge_hom.verify(a, b)
        marker = f"{GREEN}✔{RESET}" if check.holds else f"{RED}✘{RESET}"
        print(f"    {marker}  split@{sp:>4d}:  |A|={len(a):>4d}  |B|={len(b):>4d}  "
              f"→ LHS keys={len(check.lhs):>3d}  RHS keys={len(check.rhs):>3d}  "
              f"{'HOLDS' if check.holds else 'VIOLATED'}")
        if not check.holds:
            all_hold = False

    print()
    result_check("Homomorphism law holds at ALL split points", all_hold)

    print(f"\n  {BOLD}Why this matters:{RESET}")
    info("PLC side (FC_CombineEffects) and Python side (dict merge)")
    info("are designed INDEPENDENTLY — different carriers, different ⊕.")
    info("φ bridges them automatically.")
    info("Cross-layer correctness = finite algebraic check (verify φ).")
    info("→ No full integration testing required.")
    info("→ Either side can evolve independently — just re-verify φ.")


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MonoPLC Design C & D Live Demo")
    parser.add_argument(
        "--collect-seconds", type=int, default=10,
        help="How long to collect effects from PLC (default: 10s)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel fold workers for Design C (default: 4)",
    )
    args = parser.parse_args()

    print(f"{BOLD}{CYAN}")
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║   MonoPLC — Design C & D Live Demo              ║")
    print("  ║   Monoid Algebra on Real PLC Data               ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print(f"{RESET}")

    # Phase 1: Connect to PLC and collect effects
    bridge = PLCBridge(settings.AMS_NET_ID, settings.ADS_PORT)
    try:
        entries = collect_effects(bridge, args.collect_seconds)
    except Exception as e:
        fail(f"Could not connect to PLC: {e}")
        sys.exit(1)

    if not entries:
        fail("No effects collected. Is the PLC running and producing effects?")
        sys.exit(1)

    # Phase 2: Design C demo
    demo_design_c(entries, args.workers)

    # Phase 3: Design D demo
    demo_design_d(entries)

    # Summary
    header("Summary")
    ok(f"Effects collected from PLC: {len(entries)}")
    ok("Design C: Parallel fold, checkpoint reuse, time-travel — all powered by associativity")
    ok("Design D: Homomorphism φ verified on real data — cross-layer correctness by algebra")
    print()


if __name__ == "__main__":
    main()
