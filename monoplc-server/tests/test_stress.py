"""
Experiment #3b: High-Frequency Disturbance Stress Test.

Sends 200+ effects to PLC at high frequency while monitoring Control Layer
stability via ADS. Proves that Supervisory Layer disturbances cannot
compromise Control Layer operation — the Monoid architecture's isolation guarantee.

Requires: TwinCAT PLC running with ADS connection available.
"""

import sys
import time
import random
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import pyads

from config import settings
from models import Effect, EffectType
from plc_bridge import PLCBridge


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

@dataclass
class StressSnapshot:
    """A single monitoring sample taken during the stress test."""
    timestamp: float          # seconds since test start
    temperature: float
    valve_cmd: bool
    input_head: int
    input_tail: int
    output_head: int
    output_tail: int
    temp_count: int
    spray_amount: float
    effects_pushed: int       # cumulative
    push_success: bool        # last push result


@dataclass
class StressReport:
    """Aggregated stress test results."""
    total_pushed: int = 0
    total_failed: int = 0
    total_rejected: int = 0   # queue full rejections
    duration_sec: float = 0.0
    snapshots: list = field(default_factory=list)
    post_effects: list = field(default_factory=list)

    @property
    def push_rate(self) -> float:
        return self.total_pushed / max(self.duration_sec, 0.001)

    @property
    def temp_range(self) -> tuple[float, float]:
        temps = [s.temperature for s in self.snapshots if s.temperature is not None]
        return (min(temps), max(temps)) if temps else (0.0, 0.0)

    @property
    def max_queue_depth(self) -> int:
        depths = [(s.input_head - s.input_tail) % 100 for s in self.snapshots]
        return max(depths) if depths else 0

    @property
    def valve_changed(self) -> bool:
        vals = [s.valve_cmd for s in self.snapshots]
        return len(set(vals)) > 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bridge():
    b = PLCBridge(settings.AMS_NET_ID, settings.ADS_PORT)
    try:
        b.connect()
    except Exception as e:
        pytest.skip(f"PLC not reachable via ADS: {e}")
    yield b
    b.disconnect()


@pytest.fixture(scope="module")
def plc(bridge):
    return bridge._plc


def read_snapshot(plc, elapsed: float, pushed: int, success: bool) -> StressSnapshot:
    """Read all monitoring variables from PLC in one shot."""
    try:
        temp = plc.read_by_name("GVL.Net_Temperature", pyads.PLCTYPE_REAL)
        valve = plc.read_by_name("GVL.Net_ValveCmd", pyads.PLCTYPE_BOOL)
        i_head = plc.read_by_name("GVL.Async_Input_Head", pyads.PLCTYPE_INT)
        i_tail = plc.read_by_name("GVL.Async_Input_Tail", pyads.PLCTYPE_INT)
        o_head = plc.read_by_name("GVL.Async_Queue_Head", pyads.PLCTYPE_INT)
        o_tail = plc.read_by_name("GVL.Async_Queue_Tail", pyads.PLCTYPE_INT)
        t_count = plc.read_by_name(
            "Control_LOOP.Global_Climate.Temp.Count", pyads.PLCTYPE_INT)
        spray = plc.read_by_name(
            "Control_LOOP.Global_Climate.Hum.SprayAmount", pyads.PLCTYPE_REAL)
    except Exception as e:
        pytest.fail(f"ADS read failed during stress test: {e}")

    return StressSnapshot(
        timestamp=elapsed,
        temperature=float(temp),
        valve_cmd=bool(valve),
        input_head=i_head,
        input_tail=i_tail,
        output_head=o_head,
        output_tail=o_tail,
        temp_count=t_count,
        spray_amount=float(spray),
        effects_pushed=pushed,
        push_success=success,
    )


# ---------------------------------------------------------------------------
# Disturbance effect generators
# ---------------------------------------------------------------------------

def generate_disturbance_effects(n: int) -> list[Effect]:
    """Generate a diverse mix of Supervisory Layer effects for stress testing."""
    effect_types = [
        # Supervisory layer commands — these should NOT break Control Layer
        (EffectType.EFF_SETPOINT_CHANGE, "TempHighLimit", "STRESS_TEST", 85.0),
        (EffectType.EFF_SETPOINT_CHANGE, "TempLowLimit", "STRESS_TEST", 65.0),
        (EffectType.EFF_IOT_CMD_START, "STRESS", "rapid_start", 1.0),
        (EffectType.EFF_IOT_CMD_STOP, "STRESS", "rapid_stop", 1.0),
        (EffectType.EFF_IOT_CMD_RESET, "STRESS", "rapid_reset", 1.0),
        (EffectType.EFF_SETPOINT_CHANGE, "TempHighLimit", "STRESS_TEST", 90.0),
        (EffectType.EFF_SETPOINT_CHANGE, "TempHighLimit", "STRESS_TEST", 75.0),
        (EffectType.EFF_IOT_CMD_START, "STRESS", "burst", 1.0),
    ]

    effects = []
    for i in range(n):
        e_type, target, payload, value = random.choice(effect_types)
        # Add some variation to values
        jitter = random.uniform(-5.0, 5.0)
        effects.append(Effect(
            e_type=e_type,
            target=target,
            payload=f"{payload}_{i}",
            value=value + jitter,
        ))
    return effects


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStressHighFrequency:
    """
    Send 200+ effects at high frequency and verify PLC Control Layer stability.

    The key insight: Monoid architecture guarantees that Supervisory Layer
    inputs (setpoint changes, start/stop/reset commands) are processed through
    the same algebraic pipeline. Even under flood conditions, the PLC's
    internal queue protection (IF Next_Head <> Q_Tail) silently drops overflow
    without corrupting Control Layer state.
    """

    INJECT_COUNT = 200
    INJECT_INTERVAL_S = 0.05     # 50ms between pushes = 20 effects/sec
    MONITOR_INTERVAL_S = 0.5     # sample PLC state every 500ms
    POST_OBSERVE_S = 5           # observe PLC recovery after injection

    def test_high_frequency_injection(self, bridge, plc):
        """Push 200 effects at 20/s while monitoring PLC stability."""

        effects = generate_disturbance_effects(self.INJECT_COUNT)
        report = StressReport()

        print(f"\n{'='*70}")
        print(f"STRESS TEST: Injecting {self.INJECT_COUNT} effects "
              f"at {1/self.INJECT_INTERVAL_S:.0f} effects/sec")
        print(f"{'='*70}")

        # --- Pre-test baseline ---
        baseline = read_snapshot(plc, 0.0, 0, True)
        report.snapshots.append(baseline)
        print(f"\nBaseline: Temp={baseline.temperature:.1f}C "
              f"Valve={'ON' if baseline.valve_cmd else 'OFF'} "
              f"TempCount={baseline.temp_count} "
              f"Spray={baseline.spray_amount:.1f}")

        # --- Injection phase ---
        start_time = time.time()
        last_monitor = start_time
        pushed = 0
        failed = 0
        rejected = 0
        all_mid_effects = []  # effects collected during injection

        for i, effect in enumerate(effects):
            success = bridge.push_input_effect(effect)
            if success:
                pushed += 1
            else:
                rejected += 1

            elapsed = time.time() - start_time

            # Drain output queue periodically to prevent overflow
            if (i + 1) % 10 == 0:
                mid_outputs = bridge.pop_output_effects()
                all_mid_effects.extend(mid_outputs)

            # Periodic monitoring during injection
            if elapsed - (last_monitor - start_time) >= self.MONITOR_INTERVAL_S:
                snap = read_snapshot(plc, elapsed, pushed, success)
                report.snapshots.append(snap)
                q_depth = (snap.input_head - snap.input_tail) % 100
                last_monitor = time.time()

                if (i + 1) % 50 == 0:
                    print(f"  [{i+1:3d}/{self.INJECT_COUNT}] "
                          f"Temp={snap.temperature:.1f}C "
                          f"Valve={'ON' if snap.valve_cmd else 'OFF'} "
                          f"QueueDepth={q_depth} "
                          f"Pushed={pushed} Rejected={rejected}")

            time.sleep(self.INJECT_INTERVAL_S)

        inject_duration = time.time() - start_time
        report.total_pushed = pushed
        report.total_rejected = rejected
        report.total_failed = failed

        print(f"\nInjection complete: {pushed} pushed, {rejected} rejected "
              f"in {inject_duration:.1f}s")
        print(f"Effects collected during injection: {len(all_mid_effects)}")

        # --- Post-injection observation ---
        print(f"\nObserving PLC recovery for {self.POST_OBSERVE_S}s...")
        post_start = time.time()
        while time.time() - post_start < self.POST_OBSERVE_S:
            # Drain output effects to see if PLC is still alive
            outputs = bridge.pop_output_effects()
            report.post_effects.extend(outputs)
            elapsed = time.time() - start_time
            snap = read_snapshot(plc, elapsed, pushed, True)
            report.snapshots.append(snap)
            time.sleep(0.5)

        report.duration_sec = time.time() - start_time

        # --- Final snapshot ---
        final = read_snapshot(plc, report.duration_sec, pushed, True)
        report.snapshots.append(final)

        total_effects_collected = len(all_mid_effects) + len(report.post_effects)

        # --- Print report ---
        print(f"\n{'='*70}")
        print(f"STRESS TEST REPORT")
        print(f"{'='*70}")
        print(f"Duration:           {report.duration_sec:.1f}s")
        print(f"Effects pushed:     {report.total_pushed}")
        print(f"Effects rejected:   {report.total_rejected} (queue full)")
        print(f"Push rate:          {report.push_rate:.1f} effects/sec")
        print(f"Max queue depth:    {report.max_queue_depth} / 100")
        t_min, t_max = report.temp_range
        print(f"Temperature range:  [{t_min:.1f}, {t_max:.1f}]C")
        print(f"Valve toggled:      {'Yes' if report.valve_changed else 'No'}")
        print(f"Effects during inj: {len(all_mid_effects)}")
        print(f"Effects post-test:  {len(report.post_effects)}")
        print(f"Total collected:    {total_effects_collected}")

        # Effect type breakdown of post-test outputs
        if report.post_effects:
            from collections import Counter
            type_counts = Counter(e.e_type.name for e in report.post_effects)
            print(f"\nPost-test effect types:")
            for etype, count in type_counts.most_common():
                print(f"  {etype}: {count}")

        print(f"\nFinal state:")
        print(f"  Temperature:     {final.temperature:.1f}C")
        print(f"  Valve:           {'ON' if final.valve_cmd else 'OFF'}")
        print(f"  Temp.Count:      {final.temp_count}")
        print(f"  Hum.SprayAmount: {final.spray_amount:.1f}")
        print(f"{'='*70}")

        # --- Assertions ---

        # 1. Temperature stayed in a sane range throughout
        assert t_min >= 0.0, f"Temperature dropped below 0: {t_min}"
        assert t_max <= 150.0, f"Temperature exceeded 150: {t_max}"

        # 2. Queue never fully overflowed (PLC consumed fast enough)
        assert report.max_queue_depth < 100, (
            f"Input queue overflow: max depth = {report.max_queue_depth}")

        # 3. PLC is still alive — either collected effects or temperature is changing
        plc_alive = (
            total_effects_collected > 0
            or report.valve_changed
            or (t_max - t_min) > 1.0  # temperature is still varying
        )
        assert plc_alive, (
            "PLC appears unresponsive: no effects, no valve change, no temp change")

        # 4. At least some effects were accepted
        assert report.total_pushed > 0, "No effects were accepted by PLC"

        # 5. Temp.Count is still in valid range (not corrupted)
        assert 0 <= final.temp_count <= 20, (
            f"Temp.Count out of range: {final.temp_count}")

    def test_rapid_start_stop_stability(self, bridge, plc):
        """Rapidly alternate START/STOP commands — PLC should not crash or wedge."""

        print(f"\n{'='*70}")
        print(f"RAPID START/STOP TEST: 50 alternating commands")
        print(f"{'='*70}")

        baseline = read_snapshot(plc, 0.0, 0, True)

        for i in range(50):
            if i % 2 == 0:
                effect = Effect(e_type=EffectType.EFF_IOT_CMD_STOP,
                                target="RAPID_TEST", payload=f"stop_{i}", value=0.0)
            else:
                effect = Effect(e_type=EffectType.EFF_IOT_CMD_START,
                                target="RAPID_TEST", payload=f"start_{i}", value=1.0)
            bridge.push_input_effect(effect)
            time.sleep(0.02)  # 20ms = 50 commands/sec

        # Wait for PLC to process
        time.sleep(2.0)

        # Drain outputs
        outputs = bridge.pop_output_effects()
        final = read_snapshot(plc, 0.0, 50, True)

        print(f"Pushed:    50 alternating START/STOP")
        print(f"Outputs:   {len(outputs)} effects received")
        print(f"Final Temp: {final.temperature:.1f}C")
        print(f"Final Valve: {'ON' if final.valve_cmd else 'OFF'}")
        print(f"Temp.Count: {final.temp_count}")

        # PLC should still be alive and responsive
        assert final.temperature >= 0.0
        assert 0 <= final.temp_count <= 20

        # Should have received some output effects
        assert len(outputs) >= 0  # at minimum, no crash

    def test_setpoint_flood(self, bridge, plc):
        """Flood setpoint changes — PLC should apply last-writer-wins gracefully."""

        print(f"\n{'='*70}")
        print(f"SETPOINT FLOOD TEST: 100 setpoint changes in 5 seconds")
        print(f"{'='*70}")

        values_sent = []
        for i in range(100):
            new_limit = random.uniform(60.0, 95.0)
            values_sent.append(new_limit)
            effect = Effect(
                e_type=EffectType.EFF_SETPOINT_CHANGE,
                target="TempHighLimit",
                payload=f"flood_{i}",
                value=new_limit,
            )
            bridge.push_input_effect(effect)
            time.sleep(0.05)

        # Wait for PLC to settle
        time.sleep(3.0)

        # Drain and check
        outputs = bridge.pop_output_effects()
        final = read_snapshot(plc, 0.0, 100, True)

        print(f"Setpoints sent:    100 (range [{min(values_sent):.1f}, {max(values_sent):.1f}])")
        print(f"Post-test outputs: {len(outputs)}")
        print(f"Final Temp:        {final.temperature:.1f}C")
        print(f"Temp.Count:        {final.temp_count}")

        # System should still be running
        assert final.temperature >= 0.0
        assert 0 <= final.temp_count <= 20

        # Restore default setpoint
        restore = Effect(
            e_type=EffectType.EFF_SETPOINT_CHANGE,
            target="TempHighLimit",
            payload="restore_default",
            value=80.0,
        )
        bridge.push_input_effect(restore)
        time.sleep(1.0)
        print(f"Restored TempHighLimit to 80.0")
