"""
MonoPLC Bridge Server — Pydantic Data Models
Exactly mirrors the PLC-side DUT_Effect / DUT_Effect_Monoid / ENUM_Effect_Type.
"""

from enum import IntEnum
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ENUM — Exactly mirrors PLC's ENUM_Effect_Type
# ---------------------------------------------------------------------------

class EffectType(IntEnum):
    """Mirrors PLC's ENUM_Effect_Type (DUTs/ENUM_Effect_Type.TcDUT)."""
    EFF_NONE            = 0
    EFF_IOT_PUB         = 1
    EFF_FILE_LOG        = 2
    EFF_VALVE_CTRL      = 3   # ⚠ PLC Control Layer only, Supervisory Layer prohibited
    EFF_ALARM           = 4   # ⚠ PLC Control Layer only, Supervisory Layer prohibited
    EFF_IOT_CMD_STOP    = 5
    EFF_IOT_CMD_START   = 6
    EFF_IOT_CMD_RESET   = 7
    EFF_SYSTEM_TICK     = 8   # ⚠ PLC Internal Clock, external prohibited
    # --- Phase 10 Extensions ---
    EFF_SETPOINT_CHANGE = 9
    EFF_LLM_DECISION    = 10


# ---------------------------------------------------------------------------
# LLM Whitelist — EffectTypes allowed for the Supervisory Layer
# ---------------------------------------------------------------------------

LLM_ALLOWED_EFFECTS: frozenset[int] = frozenset({
    EffectType.EFF_NONE,
    EffectType.EFF_IOT_PUB,
    EffectType.EFF_FILE_LOG,
    EffectType.EFF_IOT_CMD_STOP,
    EffectType.EFF_IOT_CMD_START,
    EffectType.EFF_IOT_CMD_RESET,
    EffectType.EFF_SETPOINT_CHANGE,  # Enabled in Phase 10
})


# ---------------------------------------------------------------------------
# Effect — Mirrors PLC's DUT_Effect
# ---------------------------------------------------------------------------

class Effect(BaseModel):
    """Mirrors PLC's DUT_Effect struct."""
    e_type: EffectType = Field(
        default=EffectType.EFF_NONE,
        description="Effect type, corresponds to ENUM_Effect_Type"
    )
    target: str = Field(
        default="",
        max_length=32,
        description="Target: Pin name, file name or Topic (max 32 chars)"
    )
    payload: str = Field(
        default="",
        max_length=64,
        description="String payload (max 64 chars)"
    )
    value: float = Field(
        default=0.0,
        description="Numeric payload"
    )


# ---------------------------------------------------------------------------
# EffectMonoid — Mirrors PLC's DUT_Effect_Monoid
# ---------------------------------------------------------------------------

class EffectMonoid(BaseModel):
    """Mirrors PLC's DUT_Effect_Monoid."""
    count: int = Field(default=0, description="Number of valid elements in Effects array")
    effects: list[Effect] = Field(default_factory=list, description="List of Effects")


# ---------------------------------------------------------------------------
# API Request/Response Models
# ---------------------------------------------------------------------------

class LLMChatRequest(BaseModel):
    """POST /api/llm/chat request body."""
    user_input: str = Field(
        ...,
        description="User natural language command, e.g., 'Stop' or 'Change upper temp limit to 75'"
    )


class LLMActionResponse(BaseModel):
    """
    Expected JSON format of raw LLM output.
    Since LLM outputs this format directly, we use it to generate the JSON Schema for the system prompt.
    """
    e_type: int = Field(default=0, description="Integer value of Effect type")
    target: str = Field(default="", max_length=32, description="Target variable name")
    payload: str = Field(default="", max_length=64, description="String payload")
    value: float = Field(default=0.0, description="Numeric parameter (e.g., temperature adjustment value)")
    reasoning: str = Field(default="", description="Brief explanation of the decision")


class LLMDecisionResponse(BaseModel):
    """LLM decision response."""
    effect: Effect = Field(description="The Effect decided by LLM")
    reasoning: str = Field(default="", description="LLM's reasoning")
    allowed: bool = Field(default=True, description="Whether it passed whitelist validation")
    message: str = Field(default="", description="Message prompt to the user")

class StatusCard(BaseModel):
    """A single status item decided by the LLM for display on the dashboard."""
    label: str = Field(description="Human-readable label for this status item, e.g. 'Temperature', 'Cooling Valve'")
    value: str = Field(description="Display value, e.g. '75.3°C', 'OPEN', 'ACTIVE'")
    status: str = Field(description="One of: 'normal', 'warning', 'critical', 'active', 'inactive'")


class LLMInterpretationResponse(BaseModel):
    """
    LLM interpretation statement on current system state and logs.
    The LLM decides both the narrative AND what status cards to show.
    """
    summary: str = Field(description="Concise one-sentence summary of the current system health")
    details: str = Field(description="Detailed interpretation based on the latest Monoid event stream, reported to the operator in easy-to-understand natural language")
    status_cards: list[StatusCard] = Field(default_factory=list, description="List of status items the LLM decides are worth highlighting on the dashboard")

