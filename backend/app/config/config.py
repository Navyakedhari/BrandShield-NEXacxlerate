"""Application settings. Every value comes from environment variables (or server/.env)."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(SERVER_DIR / ".env", SERVER_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # `NAME=` left blank (as in .env.example) means "not set"
    )

    app_env: str = "local"  # local | test | production
    log_level: str = "INFO"
    database_url: str = f"sqlite:///{(SERVER_DIR / 'data' / 'scammind.db').as_posix()}"
    data_dir: Path = SERVER_DIR / "data"
    cors_origins: str = "http://localhost:5173"  # comma separated

    jwt_secret: str = Field(min_length=16)  # required, no default
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    admin_email: str | None = None
    admin_password: str | None = None
    admin_name: str = "Administrator"

    login_max_failures: int = 5
    login_window_seconds: int = 900

    logo_max_bytes: int = 2 * 1024 * 1024
    url_check_timeout_seconds: float = 5.0
    url_check_max_redirects: int = 3
    url_check_max_bytes: int = 65536

    # Master switch for hosted text analysis (OFF by default: no candidate text leaves the server). `scam_llm_provider` is the default
    # active provider (gemini | huggingface | local | off); an admin can change it in Settings (stored in the database).
    scam_llm_enabled: bool = False
    scam_llm_provider: str = "gemini"
    # Google Gemini via the official google-genai SDK. Key ONLY from GEMINI_API_KEY. The model id is configuration, not code.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    # Hugging Face Inference Providers (optional). No default model: unavailable until you choose one a provider serves.
    huggingface_api_token: SecretStr | None = None
    huggingface_model: str | None = None
    # Embeddings are OFF; the Gemini embedding model id is unverified (docs/EXTENSION_PLAN.md), so there is no default.
    text_embeddings_enabled: bool = False
    gemini_embedding_model: str | None = None
    # Logo / icon scoring backend: hash (default, the original pHash+dHash) | multihash (image-2, pHash+dHash+wHash) | imagehash (needs the ImageHash package)
    image_similarity_backend: str = "hash"
    candidate_image_max_bytes: int = 2 * 1024 * 1024
    import_max_bytes: int = 1024 * 1024
    import_max_rows: int = 500
    text_provider_timeout_seconds: float = 10.0
    text_provider_max_retries: int = 2  # retries after the first attempt (5xx / timeout only; 429 and bad keys are never retried)

    # Collectors (Phase 3). The Tavily key comes ONLY from the environment (TAVILY_API_KEY); never log it.
    tavily_api_key: SecretStr | None = None
    tavily_monthly_budget: int = 900  # credits per calendar month (UTC); the free plan is 1000
    tavily_max_queries_per_scan: int = 12
    tavily_search_depth: str = "basic"  # basic = 1 credit per call, advanced = 2
    tavily_max_results: int = 10
    searxng_base_url: str | None = None
    searxng_allow_private_hosts: bool = False  # operator opt-in: the instance is usually on a private network
    searxng_max_queries_per_scan: int = 12
    itunes_country: str = "in"
    itunes_calls_per_minute: int = 20
    itunes_cache_ttl_seconds: int = 3600
    itunes_max_queries_per_scan: int = 8
    google_play_country: str = "in"
    google_play_max_queries_per_scan: int = 6
    source_slow_threshold_ms: int = 4000
    collector_timeout_seconds: float = 15.0
    reachability_checks_per_scan: int = 0  # >0: SSRF-safe reachability check of this many result URLs (off by default)

    # Scan pipeline, jobs and scheduler (Phase 4)
    redis_url: SecretStr | None = None  # unset -> in-process runner (thread). May contain a password: treated as a secret.
    scan_executor: str = "auto"  # auto (rq if REDIS_URL else thread) | rq | thread | inline (tests)
    scan_queue_name: str = "scans"
    scan_job_timeout_seconds: int = 900
    scan_stale_minutes: int = 30  # a queued/running scan older than this is treated as dead and failed
    scan_prefilter: bool = True  # skip logo/LLM steps for candidates with no name signal and no keyword hit
    scan_logo_fetch: bool = True  # SSRF-safe download of candidate logos / icons (live data only, never sample)
    scan_logo_max_bytes: int = 2 * 1024 * 1024
    scan_logo_timeout_seconds: float = 5.0
    scan_max_candidates: int = 500
    scan_max_logo_fetches: int = 25  # per scan
    screenshots_enabled: bool = False  # Playwright hook, OFF by default (package not installed)
    scheduler_enabled: bool = True  # in-process scheduler loop (never started when APP_ENV=test)
    scan_interval_minutes: int | None = None  # env SCAN_INTERVAL_MINUTES; overrides the Settings page; 0 = off
    scheduler_poll_seconds: int = 60
    scheduler_max_queries_per_scan: int | None = None  # credit estimate; default = tavily_max_queries_per_scan

    # URL reputation: Google Safe Browsing Lookup API v4. The key comes ONLY from GOOGLE_SAFE_BROWSING_API_KEY
    # (optional: no key = reputation is "unavailable"). Non-commercial use only; see docs/DECISIONS.md.
    # The daily budget is our own limit (requests per UTC day).
    google_safe_browsing_api_key: SecretStr | None = None
    safe_browsing_timeout_seconds: float = 5.0
    safe_browsing_daily_budget: int = 5000
    safe_browsing_max_urls_per_scan: int = 100
    safe_browsing_cache_ttl_seconds: int = 3600

    # General API rate limit (requests per minute, per client IP and per login token). 0 = off. In-memory, single process only.
    api_rate_limit_per_minute: int = 600

    # Risk engine severity cutoffs (LOW below `medium`; HIGH and CRITICAL start where the old medium and high bands start).
    severity_medium_min: int = 40
    severity_high_min: int = 70
    severity_critical_min: int = 85

    # Optional Sentry error reporting (Phase 6). No DSN = off. The DSN is a secret.
    sentry_dsn: SecretStr | None = None
    sentry_environment: str | None = None  # default: APP_ENV
    sentry_traces_sample_rate: float = 0.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def secret_values(self) -> list[str]:
        key = self.tavily_api_key.get_secret_value() if self.tavily_api_key else None
        secrets = (self.redis_url, self.sentry_dsn, self.google_safe_browsing_api_key, self.gemini_api_key,
                   self.huggingface_api_token)
        extra = [x.get_secret_value() for x in secrets if x]
        return [v for v in (self.jwt_secret, self.admin_password, key, *extra) if v]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class DatabaseSettings(BaseSettings):
    """Only the DB URL: lets Alembic run without needing JWT_SECRET etc."""

    model_config = SettingsConfigDict(
        env_file=(SERVER_DIR / ".env", SERVER_DIR.parent / ".env"), extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    database_url: str = f"sqlite:///{(SERVER_DIR / 'data' / 'scammind.db').as_posix()}"
