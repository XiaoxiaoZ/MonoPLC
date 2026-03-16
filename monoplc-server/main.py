"""
MonoPLC Bridge Server — FastAPI Entrypoint

Initializes PLCBridge / StateStore on startup,
Gracefully disconnects on shutdown.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from config import settings
from plc_bridge import PLCBridge
from state_store import StateStore

from routers import effects, status, buttons, llm

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global State Container
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """Global shared state at runtime."""
    plc_bridge: PLCBridge
    state_store: StateStore


app_state: AppState = None  # type: ignore





# ---------------------------------------------------------------------------
# Lifespan Management
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_state

    logger.info("=" * 60)
    logger.info("MonoPLC Bridge Server starting...")
    logger.info("AMS Net ID: %s:%d", settings.AMS_NET_ID, settings.ADS_PORT)
    logger.info("Ollama: %s (model: %s)", settings.OLLAMA_URL, settings.OLLAMA_MODEL)
    logger.info("=" * 60)

    # 1. Initialize PLC Bridge
    plc_bridge = PLCBridge(settings.AMS_NET_ID, settings.ADS_PORT)
    try:
        plc_bridge.connect()
    except Exception as exc:
        logger.warning("PLC ADS connection failed: %s (server will run without PLC)", exc)

    # 2. Initialize StateStore
    state_store = StateStore(plc_bridge)
    await state_store.start_polling()

    # 3. Set global state
    app_state = AppState(
        plc_bridge=plc_bridge,
        state_store=state_store,
    )

    yield

    # --- Shutdown ---
    logger.info("MonoPLC Bridge Server shutting down...")
    await state_store.stop_polling()
    plc_bridge.disconnect()


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MonoPLC Bridge Server",
    description=(
        "RESTful API + Ollama LLM Middleware, "
        "Communicates with TwinCAT PLC via Monoid (Effect Queue).\n\n"
        "**Architecture Principle**: PLC owns real-time control, this server acts as the supervisory layer."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

from fastapi.staticfiles import StaticFiles

app.include_router(effects.router)
app.include_router(status.router)
app.include_router(buttons.router)
app.include_router(llm.router)

import os
# Mount Web GUI Dashboard
base_dir = os.path.dirname(os.path.abspath(__file__))
app.mount("/gui", StaticFiles(directory=os.path.join(base_dir, "static"), html=True), name="static")

@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "MonoPLC Bridge Server",
        "version": "0.1.0",
        "docs": "/docs",
        "architecture": "Supervisory Layer — PLC owns real-time control",
    }


# ---------------------------------------------------------------------------
# Direct Run: python main.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
