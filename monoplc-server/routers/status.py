"""
MonoPLC Bridge Server — System Status Query Endpoints
All status fetched from StateStore (reconstructed by Effect stream), not directly reading PLC variables.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["Status"])


@router.get("/status", summary="Get system state (reconstructed from Effect stream)")
async def get_status():
    """Return system state snapshot reconstructed from Async_Effect_Queue output stream."""
    from main import app_state

    return app_state.state_store.get_state()


@router.get("/logs", summary="Get recent Effect log stream")
async def get_logs(n: int = 20):
    """Return latest n Effect logs."""
    from main import app_state

    return {"logs": app_state.state_store.get_logs(n)}
