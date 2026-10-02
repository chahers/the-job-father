"""Environment settings (.env) + YAML config loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# --------------------------------------------------------------------------- #
# env (.env)
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    database_url: str = "postgresql+psycopg://jobfather:the-great-job-father@127.0.0.1:5432/jobfather"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "jobfather"
    postgres_user: str = "jobfather"
    postgres_password: str = "the-great-job-father"
    postgres_superuser: str = "postgres"
    # Optional override; blank -> auto-discover ../database from the project.
    database_dir: str = ""

    crawler_cdp_url: str = "http://localhost:9222"
    crawler_chrome_user_data_dir: str = ".profiles/jobfather"
    crawler_max_concurrency: int = 8
    crawler_page_timeout_ms: int = 45000
    crawler_headless: bool = False

    staging_dir: str = "data"
    log_level: str = "INFO"

    @property
    def libpq_dsn(self) -> str:
        """DATABASE_URL without the SQLAlchemy driver suffix (`+psycopg`)."""
        return (
            self.database_url.replace("postgresql+psycopg://", "postgresql://")
            .replace("postgresql+psycopg2://", "postgresql://")
        )


# --------------------------------------------------------------------------- #
# config/settings.yaml
# --------------------------------------------------------------------------- #
class RunCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_jobs_total: int = 250
    default_max_concurrency: int = 8
    force_refresh: bool = False
    save_html: str = "failures"


class BrowserCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page_timeout_ms: int = 45000
    wait_until: str = "domcontentloaded"
    cdp_url: str | None = None
    user_data_dir: str = ".profiles/jobfather"
    viewport_width: int = 1366
    viewport_height: int = 900
    locale: str = "en-MY"
    timezone_id: str = "Asia/Kuala_Lumpur"


class RateLimiterCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    base_delay: tuple[float, float] = (1.0, 3.0)
    max_delay: float = 60.0
    max_retries: int = 3
    rate_limit_codes: list[int] = Field(default_factory=lambda: [429, 503])


class DispatcherCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    memory_threshold_percent: float = 75.0
    rate_limiter: RateLimiterCfg = Field(default_factory=RateLimiterCfg)


class RetryCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_attempts: int = 3
    base_delay_seconds: float = 1.5
    backoff_factor: float = 2.0
    max_delay_seconds: float = 30.0


class MarkdownCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content_filter: str = "pruning"
    pruning_threshold: float = 0.48
    excluded_tags: list[str] = Field(
        default_factory=lambda: [
            "nav",
            "aside",
            "footer",
            "script",
            "style",
            "header",
            "noscript",
            "form",
        ]
    )


class DatabaseCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Directory holding the numbered *.sql migrations (shared by all agents).
    dir: str | None = None


class SettingsDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")
    database: DatabaseCfg = Field(default_factory=DatabaseCfg)
    run: RunCfg = Field(default_factory=RunCfg)
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    dispatcher: DispatcherCfg = Field(default_factory=DispatcherCfg)
    retry: RetryCfg = Field(default_factory=RetryCfg)
    markdown: MarkdownCfg = Field(default_factory=MarkdownCfg)


# --------------------------------------------------------------------------- #
# config/filters.yaml
# --------------------------------------------------------------------------- #
class FiltersDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")
    state: str | None = None
    work_arrangement: str | None = None
    expected_salary: dict[str, Any] | None = None
    post_filter: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# loading helpers
# --------------------------------------------------------------------------- #
def project_root(start: Path | None = None) -> Path:
    here = Path(start) if start else Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


def find_database_dir(start: Path | None = None, override: str | Path | None = None) -> Path:
    """Locate the shared ``database/`` directory that holds the migrations.

    Resolution order:
      1. explicit *override* (from env ``DATABASE_DIR`` or settings.yaml)
      2. walk up from *start* looking for a ``database/migrations`` directory
         (i.e. the repo-root ``database/`` shared by every agent)
      3. legacy fallback: ``<project_root>/db``
    """
    if override:
        path = Path(override)
        return path if path.is_absolute() else project_root(start) / path

    here = Path(start) if start else Path(__file__).resolve()
    for candidate in (here, *here.parents):
        candidate_db = candidate / "database"
        if (candidate_db / "migrations").is_dir():
            return candidate_db
    return project_root(here) / "db"


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping at the top level of {path}")
    return data


@dataclass(slots=True)
class Runtime:
    """Everything the crawler needs for one process, already resolved."""

    env: Settings
    doc: SettingsDoc
    filters: FiltersDoc
    sources_raw: dict[str, dict[str, Any]]
    root: Path
    database_override: str | Path | None = None
    config_dir: Path = field(init=False)
    database_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.config_dir = self.root / "config"
        self.database_dir = find_database_dir(self.root, self.database_override)

    @property
    def staging_dir(self) -> Path:
        path = Path(self.env.staging_dir)
        return path if path.is_absolute() else self.root / path

    @property
    def cdp_url(self) -> str | None:
        return self.doc.browser.cdp_url or self.env.crawler_cdp_url or None

    @property
    def user_data_dir(self) -> Path:
        configured = Path(self.env.crawler_chrome_user_data_dir or self.doc.browser.user_data_dir)
        return configured if configured.is_absolute() else self.root / configured

    @property
    def migrations_dir(self) -> Path:
        return self.database_dir / "migrations"

    @property
    def migration_files(self) -> list[Path]:
        """Numbered migration files in apply order (``NNN_*.sql``)."""
        return sorted(self.migrations_dir.glob("*.sql"))


@lru_cache(maxsize=1)
def _load_settings_env() -> Settings:
    return Settings()


def load_runtime(root: Path | None = None, *, env_file: Path | None = None) -> Runtime:
    root = project_root(root)
    env = Settings(_env_file=str(env_file)) if env_file else _load_settings_env()
    config_dir = root / "config"
    doc = SettingsDoc.model_validate(read_yaml(config_dir / "settings.yaml"))
    filters = FiltersDoc.model_validate(read_yaml(config_dir / "filters.yaml"))
    sources_raw = read_yaml(config_dir / "sources.yaml").get("sources", {}) or {}
    if not sources_raw:
        raise ValueError("config/sources.yaml defines no sources")
    override = env.database_dir or doc.database.dir
    return Runtime(
        env=env,
        doc=doc,
        filters=filters,
        sources_raw=sources_raw,
        root=root,
        database_override=override or None,
    )
