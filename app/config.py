"""Process-level configuration loaded from the environment / .env.

Only secrets and deployment details live here. Anything the user tunes while
using the app (thresholds, target companies, weights) lives in the `settings`
table, see app/services/settings.py.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str = "http://127.0.0.1:8000"

    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Host headers the app answers to. Add a name here if you reach the app
    # through something other than 127.0.0.1 / localhost.
    allowed_hosts: str = "127.0.0.1,localhost"

    max_upload_mb: int = 10

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir.is_absolute():
            return self.data_dir
        return PROJECT_ROOT / self.data_dir

    @property
    def db_path(self) -> Path:
        return self.resolved_data_dir / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def cv_dir(self) -> Path:
        return self.resolved_data_dir / "cvs"

    def ensure_dirs(self) -> None:
        self.cv_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_config() -> Config:
    return Config()
