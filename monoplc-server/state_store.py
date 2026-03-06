"""
MonoPLC Bridge Server — Server-side State Reconstructor (StateStore)

Maintains a system state snapshot in memory by continuously consuming
the Async_Effect_Queue output stream.
All REST API state queries fetch data from this module instead of reading PLC variables directly.

Architecture: This is a GENERIC key-value aggregator. It does NOT hardcode any
domain-specific variable names (temperature, valve, etc.). When PLC adds new
sensors or actuators, this file requires ZERO modifications.
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from models import Effect, EffectType
from plc_bridge import PLCBridge
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class EffectLogEntry:
    """Effect log entry with a timestamp."""
    effect: Effect
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class SystemState:
    """
    Generic system state snapshot — NO hardcoded domain fields.

    All state is stored in a dynamic dictionary keyed by (e_type, target).
    Example after consuming a few Effects:
      data = {
          "EFF_IOT_PUB.system/status": {"value": 0.0, "payload": "[...] T=75.3 ..."},
          "EFF_VALVE_CTRL.CoolingValve": {"value": 1.0, "payload": ""},
          "EFF_ALARM.OverTemp": {"value": 1.0, "payload": "[...] STILL TOO HOT!"},
          "EFF_SETPOINT_CHANGE.TempHighLimit": {"value": 80.0, "payload": ""},
      }
    """
    data: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_effects_consumed: int = 0


class StateStore:
    """
    Generic Key-Value Aggregator — consumes PLC Effect stream, stores latest
    value per (e_type, target) pair. Zero domain knowledge.

    Background asyncio task polls plc_bridge.pop_output_effects(),
    updating the in-memory state snapshot based on EffectType.
    """

    def __init__(self, plc_bridge: PLCBridge):
        self._bridge = plc_bridge
        self._state = SystemState()
        self._log: deque[EffectLogEntry] = deque(maxlen=settings.EFFECT_LOG_SIZE)
        self._poll_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # State Query Interfaces
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """
        Returns the currently reconstructed system state snapshot.
        The returned dict is a flat view suitable for REST API and LLM consumption.
        """
        flat: dict[str, Any] = {}

        for composite_key, entry in self._state.data.items():
            # Use the composite key directly as the flat key
            flat[composite_key] = entry

        flat["total_effects_consumed"] = self._state.total_effects_consumed
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
    # Effect Consumption — GENERIC, no domain logic
    # ------------------------------------------------------------------

    def _consume_effect(self, effect: Effect) -> None:
        """
        Generic Effect consumer: stores the latest value for each (e_type, target) pair.
        No hardcoded variable names — works with ANY future PLC extension.
        """
        # Skip system ticks from state (they are noise), but still log them
        entry = EffectLogEntry(effect=effect)
        self._log.append(entry)
        self._state.total_effects_consumed += 1

        if effect.e_type == EffectType.EFF_SYSTEM_TICK:
            return  # Don't pollute state dict with ticks

        if effect.e_type == EffectType.EFF_NONE:
            return  # Skip no-ops

        # Build composite key: "EFF_VALVE_CTRL.CoolingValve"
        composite_key = f"{effect.e_type.name}.{effect.target}" if effect.target else effect.e_type.name

        # Store latest value and payload for this key
        self._state.data[composite_key] = {
            "value": effect.value,
            "payload": effect.payload,
        }

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
                if consecutive_errors % 50 == 0:  # Only print log every 50 loops (approx. every 5 seconds)
                    logger.error("StateStore poll error: %s (suppressing further logs for 5s)", exc)
                consecutive_errors += 1
            await asyncio.sleep(interval)
