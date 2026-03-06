"""
MonoPLC Bridge Server — Soft Button Endpoints
Directly construct corresponding Effect and inject to Async_Input_Queue, no longer using Inbox_IoT_Soft* booleans.
"""

from fastapi import APIRouter, HTTPException

from models import Effect, EffectType

router = APIRouter(prefix="/api/buttons", tags=["Buttons"])


def _push_button_effect(e_type: EffectType, button_name: str) -> dict:
    """Construct button Effect and inject to PLC input queue."""
    from main import app_state

    effect = Effect(
        e_type=e_type,
        target="REST",
        payload=f"Button:{button_name}",
    )
    success = app_state.plc_bridge.push_input_effect(effect)
    if not success:
        raise HTTPException(status_code=503, detail="PLC input queue is full or ADS not connected")
    return {"status": "ok", "button": button_name, "effect": effect.model_dump()}


@router.post("/stop", summary="Press Stop Button")
async def button_stop():
    """Construct EFF_IOT_CMD_STOP Effect and write to Async_Input_Queue."""
    return _push_button_effect(EffectType.EFF_IOT_CMD_STOP, "Stop")


@router.post("/start", summary="Press Start Button")
async def button_start():
    """Construct EFF_IOT_CMD_START Effect and write to Async_Input_Queue."""
    return _push_button_effect(EffectType.EFF_IOT_CMD_START, "Start")


@router.post("/reset", summary="Press Reset Button")
async def button_reset():
    """Construct EFF_IOT_CMD_RESET Effect and write to Async_Input_Queue."""
    return _push_button_effect(EffectType.EFF_IOT_CMD_RESET, "Reset")
