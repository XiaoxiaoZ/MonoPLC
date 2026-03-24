"""
MonoPLC Bridge Server — PLC ADS Communication Layer (Monoid-Only)

Architecture constraint: Communicate with PLC only via Effect queues.
Prohibits reading/writing GVL variables like Net_Temperature, Net_ValveCmd, HMI_Logs directly.
"""

import logging
from typing import Optional

import pyads

from models import Effect, EffectType

logger = logging.getLogger(__name__)


class PLCBridge:
    """
    Monoid-Only ADS Communication Layer.

    Exposes only two data channels:
      - push_input_effect()  : Server → PLC (Async_Input_Queue)
      - pop_output_effects() : PLC → Server (Async_Effect_Queue)
    """

    # PLC side ring buffer size
    QUEUE_SIZE = 100

    def __init__(self, ams_net_id: str, port: int = 851):
        self._ams_net_id = ams_net_id
        self._port = port
        self._plc: Optional[pyads.Connection] = None

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establishes an ADS connection and syncs both ring buffer queues."""
        self._plc = pyads.Connection(self._ams_net_id, self._port)
        self._plc.open()
        logger.info("ADS connected to %s:%d", self._ams_net_id, self._port)
        self._sync_output_queue()
        self._sync_input_queue()

    def disconnect(self) -> None:
        """Disconnects the ADS connection."""
        if self._plc:
            self._plc.close()
            self._plc = None
            logger.info("ADS disconnected")

    @property
    def is_connected(self) -> bool:
        return self._plc is not None and self._plc.is_open

    # ------------------------------------------------------------------
    # Server → PLC : Writes Effect to Async_Input_Queue
    # ------------------------------------------------------------------

    def push_input_effect(self, effect: Effect) -> bool:
        """
        Writes an Effect to the PLC's Async_Input_Queue.

        Returns True for success, False if queue is full.
        """
        if not self.is_connected:
            logger.warning("push_input_effect: ADS not connected")
            return False

        try:
            head = self._plc.read_by_name("GVL.Async_Input_Head", pyads.PLCTYPE_INT)
            tail = self._plc.read_by_name("GVL.Async_Input_Tail", pyads.PLCTYPE_INT)
            next_head = (head + 1) % self.QUEUE_SIZE

            if next_head == tail:
                logger.warning("push_input_effect: Input queue is full (head=%d, tail=%d)", head, tail)
                return False

            base = f"GVL.Async_Input_Queue[{head}]"
            self._plc.write_by_name(f"{base}.EType", int(effect.e_type), pyads.PLCTYPE_INT)
            self._plc.write_by_name(f"{base}.Target", effect.target, pyads.PLCTYPE_STRING)
            self._plc.write_by_name(f"{base}.Payload", effect.payload, pyads.PLCTYPE_STRING)
            self._plc.write_by_name(f"{base}.Value", effect.value, pyads.PLCTYPE_REAL)

            # Finally update Head pointer (atomic commit)
            self._plc.write_by_name("GVL.Async_Input_Head", next_head, pyads.PLCTYPE_INT)

            logger.debug(
                "push_input_effect: [%d] type=%s target=%s",
                head, effect.e_type.name, effect.target
            )
            return True
        except Exception as e:
            logger.error("push_input_effect failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # PLC → Server : Pops executed Effects from Async_Effect_Queue
    # ------------------------------------------------------------------

    def pop_output_effects(self) -> list[Effect]:
        """
        Pops all pending Effects from PLC's Async_Effect_Queue (ring buffer).

        Ring buffer overflow protection:
        - On first connect / reconnect: syncs Tail = Head (start fresh)
        - During operation: detects if producer lapped consumer and skips ahead
        - Caps reads per cycle to QUEUE_SIZE-1 to prevent infinite loops

        Updates Tail pointer after consumption.
        """
        if not self.is_connected:
            # Try to reconnect automatically
            try:
                self.connect()
                logger.info("ADS auto-reconnected successfully")
            except Exception:
                return []

        try:
            head = self._plc.read_by_name("GVL.Async_Queue_Head", pyads.PLCTYPE_INT)
            tail = self._plc.read_by_name("GVL.Async_Queue_Tail", pyads.PLCTYPE_INT)

            # Calculate ring distance (how many items to read)
            ring_distance = (head - tail) % self.QUEUE_SIZE

            # Overflow detection: if distance >= QUEUE_SIZE-1, producer has lapped consumer
            if ring_distance >= self.QUEUE_SIZE - 1:
                logger.warning(
                    "Ring buffer OVERFLOW detected! head=%d, tail=%d, distance=%d. "
                    "Fast-forwarding Tail to Head (dropping %d overwritten effects).",
                    head, tail, ring_distance, ring_distance
                )
                self._plc.write_by_name("GVL.Async_Queue_Tail", head, pyads.PLCTYPE_INT)
                return []

            effects: list[Effect] = []
            reads = 0

            while tail != head and reads < self.QUEUE_SIZE - 1:
                base = f"GVL.Async_Effect_Queue[{tail}]"
                e_type_val = self._plc.read_by_name(f"{base}.EType", pyads.PLCTYPE_INT)
                target = self._plc.read_by_name(f"{base}.Target", pyads.PLCTYPE_STRING)
                payload = self._plc.read_by_name(f"{base}.Payload", pyads.PLCTYPE_STRING)
                value = self._plc.read_by_name(f"{base}.Value", pyads.PLCTYPE_REAL)

                try:
                    e_type = EffectType(e_type_val)
                except ValueError:
                    e_type = EffectType.EFF_NONE
                    logger.warning("Unknown e_type value: %d", e_type_val)

                effects.append(Effect(
                    e_type=e_type,
                    target=target,
                    payload=payload,
                    value=value,
                ))

                tail = (tail + 1) % self.QUEUE_SIZE
                reads += 1

            # Batch update Tail pointer
            if effects:
                self._plc.write_by_name("GVL.Async_Queue_Tail", tail, pyads.PLCTYPE_INT)
                logger.debug("pop_output_effects: popped %d effects", len(effects))

            return effects
        except Exception as e:
            logger.warning("pop_output_effects error: %s", e)
            return []

    def _sync_output_queue(self) -> None:
        """
        Syncs Tail = Head on the output queue (PLC → Server).
        Discards stale/overwritten data and starts fresh.
        """
        try:
            head = self._plc.read_by_name("GVL.Async_Queue_Head", pyads.PLCTYPE_INT)
            tail = self._plc.read_by_name("GVL.Async_Queue_Tail", pyads.PLCTYPE_INT)
            if head != tail:
                skipped = (head - tail) % self.QUEUE_SIZE
                logger.warning(
                    "Output queue sync: skipping %d stale effects (head=%d, tail=%d)",
                    skipped, head, tail
                )
                self._plc.write_by_name("GVL.Async_Queue_Tail", head, pyads.PLCTYPE_INT)
            else:
                logger.info("Output queue sync: queue is empty, ready")
        except Exception as e:
            logger.error("Output queue sync failed: %s", e)

    def _sync_input_queue(self) -> None:
        """
        Syncs Head = Tail on the input queue (Server → PLC).
        Ensures we don't re-push stale commands after reconnect.
        """
        try:
            head = self._plc.read_by_name("GVL.Async_Input_Head", pyads.PLCTYPE_INT)
            tail = self._plc.read_by_name("GVL.Async_Input_Tail", pyads.PLCTYPE_INT)
            if head != tail:
                skipped = (head - tail) % self.QUEUE_SIZE
                logger.warning(
                    "Input queue sync: %d unprocessed commands (head=%d, tail=%d), resetting Head=Tail",
                    skipped, head, tail
                )
                self._plc.write_by_name("GVL.Async_Input_Head", tail, pyads.PLCTYPE_INT)
            else:
                logger.info("Input queue sync: queue is empty, ready")
        except Exception as e:
            logger.error("Input queue sync failed: %s", e)

    # ------------------------------------------------------------------
    # Phase 3: Visualization & Demostration Exception
    # ------------------------------------------------------------------

    def read_plc_humidity(self) -> float:
        """
        Reads the purely PLC-side Product Monoid accumulation for Humidity.
        This breaks the "Event-Only" constraint purely for UI visualization
        to prove that the PLC is correctly accumulating the Sum Monoid natively.
        """
        if not self.is_connected:
            return 0.0

        try:
            val = self._plc.read_by_name("Control_LOOP.Global_Climate.Hum.SprayAmount", pyads.PLCTYPE_REAL)
            return float(val)
        except Exception as e:
            logger.warning("read_plc_humidity failed: %s. Is the new PLC code compiled?", e)
            return 0.0

    def read_plc_peak_pressure(self) -> float:
        """
        Reads the PLC-side Product Monoid accumulation for Pressure (Max Monoid).
        Experiment #8: vertical extensibility — proves PLC-side Max Monoid works.
        """
        if not self.is_connected:
            return 0.0

        try:
            val = self._plc.read_by_name("Control_LOOP.Global_Climate.Pressure.PeakPressure", pyads.PLCTYPE_REAL)
            return float(val)
        except Exception as e:
            logger.warning("read_plc_peak_pressure failed: %s. Is the new PLC code compiled?", e)
            return 0.0
