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

    # ── Prava, part 1: the MERCHANT API (we are the merchant) ────────────────
    # sandbox.api.prava.space with sk_test_ / pk_test_; api.prava.space with
    # sk_live_ / pk_live_. Keys and host must match — an sk_test_ key against
    # production is rejected, and vice versa. This side issues the scoped Visa
    # network token after the user enters a card in Prava's PCI iframe. It has no
    # catalog: there is nothing to BUY through it.
    prava_publishable_key: str = ""
    prava_secret_key: str = ""
    prava_api_base: str = "https://sandbox.api.prava.space"

    # Optional session fields the merchant API accepts (all documented optional).
    # A callback URL must be HTTPS; empty means Prava shows its own completion
    # screen instead of returning the user to us.
    prava_callback_url: str = ""
    prava_user_country: str = "US"
    # MCC + human category for the destination merchant. Sent when set; Visa uses
    # the MCC to scope the token, so a wrong code is worse than none.
    prava_merchant_category_code: str = ""
    prava_merchant_category: str = ""
    # Opt-in DNS check on the merchant host before creating a session. The
    # reserved-TLD list in app/prava/validate.py is enforced always and offline;
    # this catches the rest — an invented TLD like `.nep`, or a domain that was
    # simply never delegated — which no offline list can know. Fails CLOSED, so
    # switch it on only where the resolver is dependable.
    prava_verify_merchant_dns: bool = False

    @property
    def prava_is_sandbox(self) -> bool:
        return "sandbox" in self.prava_api_base or self.prava_secret_key.startswith(
            "sk_test_"
        )

    @property
    def prava_key_environment_problem(self) -> str | None:
        """Why the configured key and host disagree, or None if they match.

        A test key against production (or a live key against sandbox) fails at
        session creation with AUTH_1001 and nothing else to go on. Catching it
        here turns a confusing 401 mid-errand into a startup-time sentence.
        """
        key, base = self.prava_secret_key, self.prava_api_base
        if not key:
            return None
        host_is_sandbox = "sandbox" in base
        if key.startswith("sk_test_") and not host_is_sandbox:
            return (
                f"PRAVA_SECRET_KEY is a sandbox key (sk_test_) but PRAVA_API_BASE "
                f"is {base}. Point it at https://sandbox.api.prava.space."
            )
        if key.startswith("sk_live_") and host_is_sandbox:
            return (
                f"PRAVA_SECRET_KEY is a live key (sk_live_) but PRAVA_API_BASE is "
                f"{base}. Point it at https://api.prava.space."
            )
        return None

    # ── Prava, part 2: the WALLET / AGENT API (the user's own card) ───────────
    # pay-api.prava.space, authenticated by an Ed25519 keypair the USER approved
    # in their Prava wallet (see scripts/prava_link.py). This is the only Prava
    # surface with a real catalog: /v1/wallet/shop/{search,product,quote,checkout}
    # reaches UCP-indexed merchants and drives their checkout.
    #
    # IT IS PRODUCTION-ONLY. There is no sandbox wallet host — sandbox.pay-api,
    # pay-api.sandbox and sandbox.pay do not resolve. So a linked agent shops a
    # REAL merchant with a REAL card, which is why `use_prava_shop` defaults to
    # false and the sandbox demo runs the storefront shopper below instead.
    prava_wallet_api_base: str = "https://pay-api.prava.space"
    prava_link_api_base: str = "https://api.prava.space"
    prava_dashboard_base: str = "https://pay.prava.space"
    prava_agent_id: str = ""
    prava_agent_private_key: str = ""
    prava_ships_to: str = "US"
    use_prava_shop: bool = False

    # ── What to do when the approved merchant does not stock the item ────────
    # The policy's approved vendors are a LIST, and every one of them is tried
    # before this setting matters at all. It governs only the last resort:
    # widening the search to Prava's whole catalog and buying from a merchant
    # the policy never named.
    #
    #   "off"      — never. An out-of-stock errand stops and says so.
    #   "personal" — only on the personal profile.
    #   "always"   — both profiles (DEFAULT, by explicit product decision: the
    #                user asked for the whole catalog so that anything can be
    #                bought, not just what one vendor happens to stock).
    #
    # Worth being clear about what "always" costs, since it is now the default: a
    # business policy that says "avoid non-approved vendors" WILL be widened past
    # its vendor list when none of those vendors stock the item. That is a
    # deliberate choice, not an oversight. Set MERCHANT_DISCOVERY=personal to get
    # the strict reading back.
    #
    # In every case the chosen merchant is named in the audit trail and shown on
    # the approval screen before a cent moves — discovery widens what we can
    # offer, never what we can spend without being asked. The approval gate is
    # what keeps "always" safe rather than merely permissive.
    merchant_discovery: str = "always"

    # Hard cap on merchants tried in one errand (approved + discovered). Each
    # attempt on the wallet path spins a real browser for the quote (20-40s), so
    # this bounds wall-clock as much as it bounds breadth.
    max_merchant_attempts: int = 4

    def discovery_allowed(self, profile: str) -> bool:
        mode = self.merchant_discovery.strip().lower()
        if mode == "always":
            return True
        if mode == "personal":
            return profile == "personal"
        return False

    @property
    def prava_shop_ready(self) -> bool:
        """True when the wallet shopper has an approved agent identity to use."""
        return bool(
            self.use_prava_shop and self.prava_agent_id and self.prava_agent_private_key
        )

    # Senso
    senso_api_key: str = ""
    senso_api_base: str = "https://apiv2.senso.ai/api/v1"

    # Unroutable-merchant resolution.
    #
    # The seeded Senso policy names its approved vendor as
    # https://demo-pantry.example.com. `example.com` is RESERVED by IANA
    # (RFC 2606 / RFC 6761) for documentation and can never host a real store, so
    # handing it to the shopper meant build_cart found no products and NO errand
    # could complete.
    #
    # Prava DOES have a real catalog — the wallet API's UCP endpoints, wired up in
    # app/brokers/prava_shop.py — but it is production-only and spends a real
    # card, so it cannot stand in for a sandbox storefront. When that path is
    # live (`prava_shop_ready`) this substitution is skipped entirely and an
    # unroutable policy vendor aborts the run instead: see resolve_merchant.
    #
    # So a policy host listed here is resolved to `demo_store_url` — the
    # demonstration storefront served from the frontend Worker, whose DOM matches
    # the shopper's contract. This is deliberately NARROW: only these exact hosts
    # are ever rewritten, Senso remains the source of truth for the merchant's
    # NAME, budget and rules, and every substitution emits a
    # `context.merchant_resolved` audit event so the record never implies Senso
    # named a URL it did not. Clear `unroutable_merchant_hosts` to disable.
    demo_store_url: str = (
        "https://errand-frontend.rough-cell-383c.workers.dev/store/index.html"
    )
    unroutable_merchant_hosts: str = "demo-pantry.example.com"

    @property
    def unroutable_hosts(self) -> set[str]:
        return {
            h.strip().lower()
            for h in self.unroutable_merchant_hosts.split(",")
            if h.strip()
        }

    # Deepgram
    deepgram_api_key: str = ""

    # AgentMail
    agentmail_api_key: str = ""

    # Cloudflare
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""

    # OpenAI
    openai_api_key: str = ""
    # Override for the OpenAI-compatible endpoint the chat path talks to. None
    # keeps the SDK's own default (api.openai.com), which is what this deployment
    # resolves to today, so an unset value moves nothing. Note the client does
    # NOT pick this up implicitly — AsyncOpenAI(api_key=...) ignores it, and a
    # caller that should honour the override has to pass
    # base_url=settings.openai_api_base itself.
    openai_api_base: str | None = None

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

    # Live browser handoff: the agent shops in a real Cloudflare browser and hands
    # the LIVE, interactive view to the human to log in / pay themselves (the
    # agent enters no card on this path). Off by default — it needs Cloudflare
    # Browser Run creds, and it drives real merchants. See
    # docs/superpowers/specs/2026-08-03-live-view-handoff-design.md.
    use_live_handoff: bool = False

    @property
    def live_handoff_ready(self) -> bool:
        """True only when live handoff is enabled AND the Cloudflare browser it
        requires is actually configured — Live View has no local equivalent."""
        return bool(
            self.use_live_handoff
            and self.cloudflare_account_id
            and self.cloudflare_api_token
        )

    # ── Custom MCP servers (the user's own tool providers) ───────────────────
    # A user registers an MCP server and its tools become callable by the agent
    # on both the chat and voice surfaces. See app/mcp/ for the whole feature.
    mcp_enabled: bool = True

    # Ceiling on servers one user may register. Each enabled server's cached tool
    # catalogue is loaded on every turn and its tools cost input tokens on every
    # pass of the tool loop, so this bounds cost as much as it bounds abuse.
    mcp_max_servers_per_user: int = 12

    # ⚠️ ARBITRARY CODE EXECUTION. A stdio MCP server is a command this backend
    # SPAWNS. Registering one is equivalent to shell access to the container that
    # holds the OpenAI, Cloudflare, Prava, Deepgram and AgentMail keys — so with
    # more than one user account, leaving this on hands every one of them that
    # access. OFF unless you are the sole operator of the instance.
    #
    # better-chatbot allows stdio and disables it only on Vercel
    # (IS_MCP_SERVER_REMOTE_ONLY), which is the right default for a self-hosted
    # single-operator app and the wrong one here; the polarity is inverted
    # deliberately. Enforced in app/mcp/config.validate_stdio.
    mcp_allow_stdio: bool = False

    # Local development only: permit http:// MCP URLs and skip the private-range
    # checks, so a server on the developer's own machine can be used. In a
    # deployment this must stay false — it is what stops a user-supplied URL from
    # reaching the cloud metadata endpoint or anything else inside the VNet
    # (app/mcp/config.validate_remote_url).
    mcp_allow_insecure_http: bool = False

    # Encryption key for stored MCP credentials — static header secrets and OAuth
    # token sets, which normally include a refresh token. A urlsafe-base64 32-byte
    # Fernet key:
    #   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    # When empty, one is derived from JWT_SECRET via HKDF. That works and inherits
    # JWT_SECRET's enforced entropy, but it ties the two lifecycles together:
    # rotating JWT_SECRET orphans every stored credential (recoverable by
    # re-authorizing, but not silent). Set this explicitly if you rotate.
    mcp_encryption_key: str = ""

    # Public origin of THIS BACKEND, used to build the OAuth redirect URI
    # (<base>/api/mcp/oauth/callback). It must be the address the user's browser
    # can reach, because the authorization server sends the browser there — and it
    # must match byte-for-byte across the authorization request and the token
    # exchange or the exchange is rejected. Defaults to the local dev backend.
    mcp_oauth_redirect_base: str = "http://localhost:8787"

    # Where the callback page sends the user when it cannot talk to an opener
    # window (a popup blocker, or the flow completed in a plain tab). Empty falls
    # back to a self-closing page with no redirect.
    mcp_oauth_success_redirect: str = ""

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
