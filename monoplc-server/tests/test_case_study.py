"""
Experiment #5: Case Study — System Runtime Data Collection.

Runs the system for a measurement period, collecting:
  - PLC scan cycle timing
  - ADS read/write latency
  - Effect type distribution
  - Total effects processed
"""

import sys
import time
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import pyads

from config import settings
from models import Effect, EffectType
from plc_bridge import PLCBridge


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


class TestCaseStudyMetrics:
    """Collect runtime metrics over a measurement window."""

    MEASUREMENT_SECONDS = 30  # Run for 30 seconds
    POLL_INTERVAL = 0.1       # Poll every 100ms (matches config)

    def test_collect_runtime_data(self, bridge, plc):
        """Collect effects and timing data over the measurement period."""
        print(f"\n{'='*60}")
        print(f"Case Study: Collecting data for {self.MEASUREMENT_SECONDS} seconds...")
        print(f"{'='*60}")

        all_effects: list[Effect] = []
        ads_read_times: list[float] = []
        ads_write_times: list[float] = []
        poll_count = 0
        start_time = time.time()

        while time.time() - start_time < self.MEASUREMENT_SECONDS:
            # Measure ADS read latency (pop_output_effects)
            t0 = time.perf_counter()
            effects = bridge.pop_output_effects()
            t1 = time.perf_counter()
            ads_read_times.append((t1 - t0) * 1000)  # ms

            all_effects.extend(effects)
            poll_count += 1

            # Periodically push a test effect to measure write latency
            if poll_count % 50 == 0:
                test_effect = Effect(
                    e_type=EffectType.EFF_SYSTEM_TICK,
                    target="BENCHMARK",
                    payload="",
                    value=0.0,
                )
                t0 = time.perf_counter()
                bridge.push_input_effect(test_effect)
                t1 = time.perf_counter()
                ads_write_times.append((t1 - t0) * 1000)

            time.sleep(self.POLL_INTERVAL)

        elapsed = time.time() - start_time

        # --- Effect Statistics ---
        type_counter = Counter(e.e_type.name for e in all_effects)
        total = len(all_effects)

        print(f"\n--- Runtime Summary ---")
        print(f"Measurement duration: {elapsed:.1f} seconds")
        print(f"Poll cycles: {poll_count}")
        print(f"Total effects collected: {total}")

        print(f"\n--- Effect Type Distribution ---")
        for etype, count in type_counter.most_common():
            pct = count / total * 100 if total > 0 else 0
            print(f"  {etype:30s}: {count:5d} ({pct:5.1f}%)")

        # --- ADS Latency ---
        if ads_read_times:
            avg_read = sum(ads_read_times) / len(ads_read_times)
            max_read = max(ads_read_times)
            min_read = min(ads_read_times)
            print(f"\n--- ADS Read Latency (pop_output_effects) ---")
            print(f"  Average: {avg_read:.2f} ms")
            print(f"  Min:     {min_read:.2f} ms")
            print(f"  Max:     {max_read:.2f} ms")
            print(f"  Samples: {len(ads_read_times)}")

        if ads_write_times:
            avg_write = sum(ads_write_times) / len(ads_write_times)
            max_write = max(ads_write_times)
            min_write = min(ads_write_times)
            print(f"\n--- ADS Write Latency (push_input_effect) ---")
            print(f"  Average: {avg_write:.2f} ms")
            print(f"  Min:     {min_write:.2f} ms")
            print(f"  Max:     {max_write:.2f} ms")
            print(f"  Samples: {len(ads_write_times)}")

        # --- PLC State Snapshot ---
        try:
            humidity = plc.read_by_name(
                "Control_LOOP.Global_Climate.Hum.SprayAmount", pyads.PLCTYPE_REAL
            )
            temp_count = plc.read_by_name(
                "Control_LOOP.Global_Climate.Temp.Count", pyads.PLCTYPE_INT
            )
            print(f"\n--- PLC State Snapshot ---")
            print(f"  Global_Climate.Temp.Count:      {temp_count}")
            print(f"  Global_Climate.Hum.SprayAmount:  {humidity:.2f}")
        except Exception as e:
            print(f"\n  (Could not read PLC state: {e})")

        print(f"\n{'='*60}")

        # Basic assertions
        assert total > 0, "Should have received at least some effects"
        assert poll_count > 0
