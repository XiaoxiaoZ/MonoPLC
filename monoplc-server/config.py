"""
MonoPLC Bridge Server — Centralized Configuration
Uses pydantic-settings to support environment variable overrides.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All settings, can be overridden by environment variables (prefix MONOPLC_)."""

    # --- ADS / PLC ---
    AMS_NET_ID: str = "199.4.42.250.1.1"
    ADS_PORT: int = 851

    # --- Ollama LLM ---
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"

    # --- Server ---
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SYS_DOC_URL: str = "http://127.0.0.1:8000/gui/system_doc.md"

    # --- State Store ---
    EFFECT_LOG_SIZE: int = 50  # Size of Effect log retained by StateStore
    POLL_INTERVAL_MS: int = 100  # Polling interval for StateStore checking PLC output queue (ms)

    # --- Design C: StateMonoid Fold / Time-Slice Replay ---
    PERSISTENT_LOG_SIZE: int = 10000  # Max effects in persistent log for time-travel queries
    CHECKPOINT_INTERVAL: int = 100    # Create a state checkpoint every N consumed effects
    MAX_PARALLEL_WORKERS: int = 4     # Worker count for parallel fold (Design C)

    model_config = {"env_prefix": "MONOPLC_"}


settings = Settings()
