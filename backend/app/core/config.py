"""Environment-based settings. No secrets are hard-coded."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://marginmind:marginmind@localhost:5432/marginmind"
    llm_provider: str = "stub"
    gemini_api_key: str = ""

    payment_provider: str = "stub"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    checkout_display_name: str = "Lumen & Thread"


@lru_cache
def get_settings() -> Settings:
    return Settings()
