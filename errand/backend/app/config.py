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

    # Feature flags: use mock brokers until real ones verified per-broker.
    # Context + Payment are verified real (Senso, Prava). Shopper + Mail stay
    # mock until their parallel-agent implementations land.
    use_mock_shopper: bool = True
    use_mock_mail: bool = True
    use_mock_context: bool = False
    use_mock_payment: bool = False


settings = Settings()
