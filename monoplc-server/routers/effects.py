"""
MonoPLC Bridge Server — Effect CRUD Endpoints
"""

from fastapi import APIRouter, HTTPException

from models import Effect

router = APIRouter(prefix="/api/effects", tags=["Effects"])


@router.post("", summary="Submit an Effect to PLC input queue")
async def submit_effect(effect: Effect):
    """
    Write Effect to PLC's Async_Input_Queue.
    Note: This is a general injection port, bypassing whitelist validation (frontend/operator responsibility).
    """
    from main import app_state

    success = app_state.plc_bridge.push_input_effect(effect)
    if not success:
        raise HTTPException(status_code=503, detail="PLC input queue is full or ADS not connected")
    return {"status": "ok", "effect": effect.model_dump()}


@router.get("/stream", summary="Get recent consumed Effect history")
async def get_effect_stream(n: int = 20):
    """Return last n consumed Effect logs from StateStore."""
    from main import app_state

    return {"logs": app_state.state_store.get_logs(n)}
