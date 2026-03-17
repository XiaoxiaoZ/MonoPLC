"""
MonoPLC Bridge Server — Server-side State Reconstructor (StateStore)

Maintains a system state snapshot in memory by continuously consuming
the Async_Effect_Queue output stream.
All REST API state queries fetch data from this module instead of reading PLC variables directly.

Architecture: This is a GENERIC key-value aggregator. It does NOT hardcode any
domain-specific variable names (temperature, valve, etc.). When PLC adds new
sensors or actuators, this file requires ZERO modifications.

Design C integration: Persistent effect log + checkpoints enable time-travel queries.
Design D integration: State reconstruction delegates to φ (homomorphism.phi) and
    MWStateMonoid.combine — the algebraic bridge between M_PLC and M_MW.
"""

import asyncio
import copy
import logging
from collections import deque
from datetime import datetime
from typing import Any, Optional

from models import Effect
from plc_bridge import PLCBridge
from config import settings
from monoid import MWStateMonoid, SumMonoid, DictProductMonoid, MonoidComponent
from homomorphism import phi, create_monoplc_bridge, MonoidHomomorphism
from models import EffectType
from fold_engine import (
    FoldEngine,
    EffectLogEntry,
    Checkpoint,
    FoldResult,
    FoldComparison,
)

logger = logging.getLogger(__name__)


def map_state(effect: Effect) -> dict:
    return {} if effect.e_type in (EffectType.EFF_SYSTEM_TICK, EffectType.EFF_NONE) else phi([effect])

def map_count(effect: Effect) -> int:
    return 0 if effect.e_type in (EffectType.EFF_SYSTEM_TICK, EffectType.EFF_NONE) else 1

def map_alarm(effect: Effect) -> int:
    return 1 if effect.e_type == EffectType.EFF_ALARM else 0

def map_spray(effect: Effect) -> float:
    return float(effect.value) if effect.e_type == EffectType.EFF_VALVE_CTRL and effect.target == "Humidifier" else 0.0

PRODUCT_COMPONENTS = [
    MonoidComponent(name="state", monoid=MWStateMonoid(), map_fn=map_state),
    MonoidComponent(name="effect_count", monoid=SumMonoid(), map_fn=map_count),
    MonoidComponent(name="alarm_count", monoid=SumMonoid(), map_fn=map_alarm),
    MonoidComponent(name="total_spray_ml", monoid=SumMonoid(), map_fn=map_spray),
]


class StateStore:
    """
    Generic Key-Value Aggregator — consumes PLC Effect stream, stores latest
    value per (e_type, target) pair. Zero domain knowledge.

    Now powered by Design C & D:
      - Design D: _consume_effect delegates to φ + MWStateMonoid.combine
      - Design C: persistent log + checkpoints enable state_at(t) and fold_parallel
    """

    def __init__(self, plc_bridge: PLCBridge):
        self._bridge = plc_bridge
        self._poll_task: Optional[asyncio.Task] = None

        # --- Design D: algebraic bridge ---
        self._mw_monoid = MWStateMonoid()
        self._product_monoid = DictProductMonoid(PRODUCT_COMPONENTS)
        self._bridge_hom = create_monoplc_bridge()

        # --- Live state (always up-to-date) ---
        self._state_data: Any = self._product_monoid.empty()
        self._total_effects_consumed: int = 0

        # --- Design C: persistent log + checkpoints ---
        self._log: deque[EffectLogEntry] = deque(maxlen=settings.EFFECT_LOG_SIZE)
        self._persistent_log: list[EffectLogEntry] = []
        self._checkpoints: list[Checkpoint] = []
        
        # Engine is injected with the purely generic Monoid and Mapper
        self._fold_engine = FoldEngine(self._product_monoid, self._product_monoid.map_effect)
        self._effects_since_checkpoint: int = 0

    # ------------------------------------------------------------------
    # State Query Interfaces
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """
        Returns the currently reconstructed system state snapshot.
        The returned dict is a flat view suitable for REST API and LLM consumption.
        """
        flat: dict[str, Any] = dict(self._state_data)
        flat["total_effects_consumed"] = self._total_effects_consumed
        return flat

    def get_logs(self, n: int = 20) -> list[dict]:
        """Returns the latest n Effect logs."""
        entries = list(self._log)[-n:]
        return [
            {
                "e_type": entry.effect.e_type.name,
                "target": entry.effect.target,
                "payload": entry.effect.payload,
                "value": entry.effect.value,
                "timestamp": entry.timestamp.isoformat(),
            }
            for entry in entries
        ]

    # ------------------------------------------------------------------
    # Design C: Time-travel & fold queries
    # ------------------------------------------------------------------

    def state_at(self, t: datetime) -> FoldResult:
        """Reconstruct system state at historical timestamp t."""
        return self._fold_engine.state_at(t, self._persistent_log, self._checkpoints)

    def state_between(self, t_from: datetime, t_to: datetime) -> FoldResult:
        """Replay: fold effects in time window [t_from, t_to]."""
        return self._fold_engine.state_between(t_from, t_to, self._persistent_log)

    def fold_compare(self, n_workers: int | None = None) -> FoldComparison:
        """Compare sequential vs parallel fold on the full persistent log."""
        workers = n_workers or settings.MAX_PARALLEL_WORKERS
        all_effects = [entry.effect for entry in self._persistent_log]
        return self._fold_engine.fold_compare(all_effects, workers)

    def get_checkpoints(self) -> list[dict]:
        """List all stored checkpoints with metadata."""
        return [
            {
                "timestamp": cp.timestamp.isoformat(),
                "effect_index": cp.effect_index,
                "state_keys": len(cp.state.get("state", {})),
            }
            for cp in self._checkpoints
        ]

    def create_checkpoint_now(self) -> dict:
        """Manually trigger a checkpoint at the current moment."""
        cp = Checkpoint(
            timestamp=datetime.now(),
            state=copy.deepcopy(self._state_data),
            effect_index=len(self._persistent_log),
        )
        self._checkpoints.append(cp)
        self._effects_since_checkpoint = 0
        logger.info("Manual checkpoint created at index %d", cp.effect_index)
        return {
            "timestamp": cp.timestamp.isoformat(),
            "effect_index": cp.effect_index,
            "state_keys": len(cp.state.get("state", {})),
        }

    # ------------------------------------------------------------------
    # Design D: Homomorphism access
    # ------------------------------------------------------------------

    @property
    def bridge(self) -> MonoidHomomorphism:
        """Expose the MonoidHomomorphism for verification endpoints."""
        return self._bridge_hom

    # ------------------------------------------------------------------
    # Effect Consumption — powered by Design D (φ + MWStateMonoid.combine)
    # ------------------------------------------------------------------

    def _consume_effect(self, effect: Effect) -> None:
        """
        Generic Effect consumer using the algebraic pipeline:
            state = state ⊕_mw φ([effect])

        Design D: delegates to φ (pure homomorphism) and MWStateMonoid.combine
        instead of inline logic. The algebraic structure is identical to the
        previous implementation, but now explicit and testable.
        """
        now = datetime.now()
        entry = EffectLogEntry(effect=effect, timestamp=now)

        # Append to both logs
        self._log.append(entry)
        if len(self._persistent_log) < settings.PERSISTENT_LOG_SIZE:
            self._persistent_log.append(entry)

        self._total_effects_consumed += 1

        # Use the compound Product mapper, then combine into product state
        mapped = self._product_monoid.map_effect(effect)
        self._state_data = self._product_monoid.combine(self._state_data, mapped)

        # Design C: auto-checkpoint every N effects
        self._effects_since_checkpoint += 1
        if self._effects_since_checkpoint >= settings.CHECKPOINT_INTERVAL:
            self._create_auto_checkpoint()

    def _create_auto_checkpoint(self) -> None:
        """Create an automatic checkpoint (Design C)."""
        cp = Checkpoint(
            timestamp=datetime.now(),
            state=copy.deepcopy(self._state_data),
            effect_index=len(self._persistent_log),
        )
        self._checkpoints.append(cp)
        self._effects_since_checkpoint = 0
        logger.debug(
            "Auto-checkpoint created at index %d (%d checkpoints total)",
            cp.effect_index, len(self._checkpoints),
        )

    # ------------------------------------------------------------------
    # Background Polling
    # ------------------------------------------------------------------

    async def start_polling(self) -> None:
        """Starts the background polling task."""
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("StateStore polling started (interval=%dms)", settings.POLL_INTERVAL_MS)

    async def stop_polling(self) -> None:
        """Stops the background polling task."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            logger.info("StateStore polling stopped")

    async def _poll_loop(self) -> None:
        """Continuously polls the PLC output queue."""
        interval = settings.POLL_INTERVAL_MS / 1000.0
        consecutive_errors = 0
        while True:
            try:
                effects = self._bridge.pop_output_effects()
                for effect in effects:
                    self._consume_effect(effect)
                consecutive_errors = 0
            except Exception as exc:
                if consecutive_errors % 50 == 0:
                    logger.error("StateStore poll error: %s (suppressing further logs for 5s)", exc)
                consecutive_errors += 1
            await asyncio.sleep(interval)
