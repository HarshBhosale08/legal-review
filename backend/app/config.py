"""Application settings loaded from environment variables / .env (never committed)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://legal:change-me@localhost:5432/legal"

    # Auth
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60
    algorithm: str = "HS256"

    # Uploads
    upload_dir: str = "./uploads"
    max_upload_mb: int = 20

    # Engine integration (stage 1 uses the stub; Module 1 owns the real engine)
    engine_mode: str = "stub"
    fake_analysis: bool = False
    # Stage 2: run analysis as a background task after the 202 response
    # instead of inline (tests keep the inline default).
    analysis_async: bool = False
    legal_engine_model_dir: str = ""
    tesseract_cmd: str = ""
    # Optional override for the fake-mode analysis fixture (JSON file).
    fake_fixture_path: str = ""

    # CORS: comma-separated list of allowed origins
    cors_origins: str = "http://localhost:5173"

    # Optional admin seed account (created on startup when both are set)
    admin_email: str = ""
    admin_password: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
