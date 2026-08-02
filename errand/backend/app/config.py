"""App configuration — loads secrets from the repo-root errand/.env.

Secrets never reach the client. The frontend only receives the Prava
publishable key (client-safe) and a short-lived Deepgram token (minted here).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# errand/.env lives one level up from backend/
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# The dev-only JWT signing secret. Startup refuses to serve a non-dev
# environment with this value (see Settings.jwt_secret_problem).
INSECURE_JWT_SECRET_DEFAULT = "dev-only-insecure-change-me"

# Shortest JWT secret considered non-trivial to brute-force offline.
MIN_JWT_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_PATH, extra="ignore")

    # Prava
    prava_publishable_key: str = ""
    prava_secret_key: str = ""
    prava_api_base: str = "https://sandbox.api.prava.space"

    # Senso
    senso_api_key: str = ""
    senso_api_base: str = "https://apiv2.senso.ai/api/v1"

    # Deepgram
    deepgram_api_key: str = ""

    # AgentMail
    agentmail_api_key: str = ""

    # Cloudflare
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""

    # OpenAI
    openai_api_key: str = ""

    # Linkup (web search)
    linkup_api_key: str = ""
    linkup_api_base: str = "https://api.linkup.so/v1"

    # Feature flags: use mock brokers until real ones verified per-broker.
    # Context + Payment are verified real (Senso, Prava). Shopper + Mail stay
    # mock until their parallel-agent implementations land.
    use_mock_shopper: bool = False
    use_mock_mail: bool = False
    use_mock_context: bool = False
    use_mock_payment: bool = False

    # CORS: comma-separated list of allowed browser origins for the HTTP/SSE
    # API. Local dev defaults are always included; in production set
    # ALLOWED_ORIGINS to the deployed frontend origin(s), e.g.
    # "https://errand-frontend.<subdomain>.workers.dev". (WebSocket connections
    # are not subject to CORS, so the voice relay works cross-origin regardless.)
    allowed_origins: str = ""

    # Database. Local dev defaults to a file-backed SQLite DB (async via
    # aiosqlite); production sets DATABASE_URL to the Postgres server. Both use
    # the same SQLAlchemy models, so the app is Postgres-compatible everywhere.
    # A postgres:// or postgresql:// URL is normalized to the asyncpg driver.
    database_url: str = "sqlite+aiosqlite:///./errand.db"

    # Auth. JWT signing secret + token lifetime. MUST be overridden in prod via
    # the JWT_SECRET env var (a long random string); the default is dev-only.
    jwt_secret: str = INSECURE_JWT_SECRET_DEFAULT
    jwt_alg: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Deployment environment. Anything other than "dev" is treated as a real
    # deployment and must supply its own JWT_SECRET (see jwt_secret_problem).
    environment: str = "dev"

    @property
    def is_dev(self) -> bool:
        return self.environment.strip().lower() in ("dev", "development", "local", "test")

    @property
    def jwt_secret_problem(self) -> str | None:
        """Why the configured JWT secret is unsafe, or None if it's acceptable.

        The signing secret is the ONLY thing standing between an attacker and a
        forged bearer token for any user id. The dev default is published in this
        repo, so serving with it means anyone can mint an admin-equivalent token.
        A too-short secret is brute-forceable offline against any issued token.
        """
        if self.jwt_secret == INSECURE_JWT_SECRET_DEFAULT:
            return (
                "JWT_SECRET is still the built-in dev default, which is public in "
                "the source tree — anyone could forge a token for any user."
            )
        if len(self.jwt_secret) < MIN_JWT_SECRET_LEN:
            return (
                f"JWT_SECRET is only {len(self.jwt_secret)} characters; use at "
                f"least {MIN_JWT_SECRET_LEN} random characters."
            )
        return None

    @property
    def sqlalchemy_url(self) -> str:
        """Normalize common Postgres URL forms to the async asyncpg driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @property
    def cors_origins(self) -> list[str]:
        defaults = ["http://localhost:3000", "http://127.0.0.1:3000"]
        extra = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        # Preserve order, drop dups.
        seen: dict[str, None] = {}
        for o in [*defaults, *extra]:
            seen.setdefault(o, None)
        return list(seen.keys())


settings = Settings()
