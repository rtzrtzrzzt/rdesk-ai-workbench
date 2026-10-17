import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from workbench.infrastructure.access_policy import AccessPolicy
from workbench.infrastructure.platform_paths import application_paths
from workbench.infrastructure.vault.private import read_json

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def default_data_directory() -> Path:
    return Path(
        application_paths(
            system=sys.platform, home=Path.home(), environ=os.environ, cwd=Path.cwd()
        ).data_dir
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKBENCH_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Field(default_factory=default_data_directory)
    database_url: str | None = None
    # Operator-only override for a staged release preview; never set through HTTP.
    frontend_dir: Path | None = None
    session_hours: int = 24 * 7
    secure_cookies: bool = False
    lan_enabled: bool = False
    public_origins: list[str] = Field(default_factory=list)
    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None
    session_idle_seconds: int = Field(default=1800, ge=60, le=86400)
    login_max_failures: int = Field(default=10, ge=3, le=100)
    login_window_seconds: int = Field(default=900, ge=60, le=3600)
    worker_lease_seconds: int = 60
    model_timeout_seconds: int = 180
    max_model_rounds: int = 24
    max_output_tokens: int = 4096

    def access_policy(self) -> AccessPolicy:
        return AccessPolicy.from_values(
            host=self.host,
            port=self.port,
            lan_enabled=self.lan_enabled,
            public_origins=self.public_origins,
            secure_cookies=self.secure_cookies,
            ssl_certfile=self.ssl_certfile,
            ssl_keyfile=self.ssl_keyfile,
        )

    def database_dsn(self) -> str | None:
        if self.database_url:
            return self.database_url
        configuration = self.database_configuration()
        return configuration[1]["database_url"] if configuration else None

    def database_configuration(self) -> tuple[Path, dict] | None:
        # User data survives release-directory switches. The second path only
        # preserves this source checkout's original development cluster.
        for path in (
            self.data_dir / "database.json",
            PROJECT_ROOT / ".local" / "development" / "database.json",
        ):
            if path.is_symlink():
                raise ValueError("数据库配置不能是符号链接。")
            if path.exists():
                config = read_json(path)
                if (
                    not isinstance(config, dict)
                    or not isinstance(config.get("database_url"), str)
                    or not config["database_url"].startswith(
                        ("postgresql://", "postgresql+psycopg://")
                    )
                ):
                    raise ValueError("受保护数据库配置无效。")
                return path, config
        return None

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return self.data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
