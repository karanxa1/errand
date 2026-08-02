"""App configuration — loads secrets from the repo-root errand/.env.

Secrets never reach the client. The frontend only receives the Prava
publishable key (client-safe) and a short-lived Deepgram token (minted here).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# errand/.env lives one level up from backend/
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


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
