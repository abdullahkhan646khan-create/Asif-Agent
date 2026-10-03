from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_secret: str = "change-me"
    dashboard_password: str = "admin"
    public_base_url: str = "http://localhost:8000"
    brand_id: str = "amanasoft"
    data_dir: Path = ROOT / "data"
    # Keep-awake: the app visits its own /health this often (minutes) so Render's free plan never sleeps it.
    # Only runs when PUBLIC_BASE_URL is an https address (i.e. on Render, not on your computer). 0 = off.
    heartbeat_minutes: float = 10

    # Text AI: Groq first, OpenRouter as backup
    groq_api_key: str = ""
    groq_models: str = "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"
    openrouter_api_key: str = ""
    openrouter_models: str = "openrouter/free,google/gemma-4-31b-it:free"

    # Gemini (browser cookies). Slot A is tried first, then slot B.
    gemini_a_1psid: str = ""
    gemini_a_1psidts: str = ""
    gemini_b_1psid: str = ""
    gemini_b_1psidts: str = ""
    gemini_model: str = ""  # empty = account default
    gemini_proxy: str = ""  # optional, e.g. http://user:pass@host:port if Google blocks the server's address
    gemini_health_check_hours: float = 6

    # Email
    email_mode: str = "apps_script"  # apps_script | smtp | gmail_api
    email_relay_url: str = ""
    email_relay_secret: str = ""
    gmail_sender: str = ""
    gmail_app_password: str = ""
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    review_email_to: str = ""
    alert_email_to: str = ""

    # Supabase (optional locally; required on Render)
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "posts"

    # Buffer
    buffer_api_key: str = ""
    buffer_channel_ids: str = ""  # optional; empty = every Facebook, X and Threads channel in Buffer
    publish_mode: str = "shareNow"  # shareNow | addToQueue
    dry_run_publish: bool = False  # true = approve does everything except posting

    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, value):
        """Forgive copy-paste slips in hosting dashboards: spaces, quotes and line breaks around a value."""
        if isinstance(value, str):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
        return value

    @property
    def groq_model_list(self) -> list[str]:
        return [m.strip() for m in self.groq_models.split(",") if m.strip()]

    @property
    def openrouter_model_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_models.split(",") if m.strip()]

    @property
    def buffer_channel_id_list(self) -> list[str]:
        return [c.strip() for c in self.buffer_channel_ids.split(",") if c.strip()]

    @property
    def review_recipients(self) -> list[str]:
        return [e.strip() for e in self.review_email_to.split(",") if e.strip()]

    @property
    def alert_recipients(self) -> list[str]:
        raw = self.alert_email_to or self.review_email_to
        return [e.strip() for e in raw.split(",") if e.strip()]

    @property
    def use_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
