"""Broker registry — wires the orchestrator to real or mock implementations.

Real brokers drop in per-flag as they are verified; until then the mock keeps
the whole flow runnable. Verified real brokers: Prava, Senso.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.contracts import ContextBroker, MailBroker, PaymentBroker, ShopperBroker
from app.brokers.mock import (
    MockContextBroker,
    MockMailBroker,
    MockPaymentBroker,
    MockShopperBroker,
)
from app.brokers.prava import PravaPaymentBroker
from app.brokers.senso import SensoContextBroker


@dataclass
class Brokers:
    context: ContextBroker
    shopper: ShopperBroker
    payment: PaymentBroker
    mail: MailBroker


def build_brokers() -> Brokers:
    context: ContextBroker = (
        MockContextBroker()
        if settings.use_mock_context
        else SensoContextBroker(settings.senso_api_key, settings.senso_api_base)
    )
    payment: PaymentBroker = (
        MockPaymentBroker()
        if settings.use_mock_payment
        else PravaPaymentBroker(
            settings.prava_secret_key,
            settings.prava_api_base,
            callback_url=settings.prava_callback_url,
            user_country=settings.prava_user_country,
            merchant_category_code=settings.prava_merchant_category_code,
            merchant_category=settings.prava_merchant_category,
            verify_merchant_dns=settings.prava_verify_merchant_dns,
        )
    )
    # Shopper. Three implementations, chosen most-real-first:
    #
    #   1. PravaShopBroker — REAL merchants through the Prava wallet's UCP
    #      catalog. Needs an agent the user approved on their wallet, and spends
    #      a real card, so it is opt-in (USE_PRAVA_SHOP=true) and only engages
    #      once an identity is actually configured. Production-only: the wallet
    #      API has no sandbox host.
    #   2. CloudflareShopperBroker — a real headless browser against a real
    #      storefront. In sandbox that storefront is the demonstration one, so
    #      the sandbox card has somewhere it can actually be presented.
    #   3. MockShopperBroker — no network at all.
    shopper: ShopperBroker = MockShopperBroker()
    mail: MailBroker = MockMailBroker()
    if settings.use_mock_shopper is False:
        if settings.prava_shop_ready:
            from app.brokers.prava_shop import PravaShopBroker  # type: ignore
            from app.prava.wallet import WalletClient

            shopper = PravaShopBroker(
                WalletClient(
                    settings.prava_agent_id,
                    settings.prava_agent_private_key,
                    base_url=settings.prava_wallet_api_base,
                ),
                ships_to=settings.prava_ships_to,
            )
        else:
            from app.brokers.shopper import CloudflareShopperBroker  # type: ignore

            shopper = CloudflareShopperBroker()
    if settings.use_mock_mail is False:
        from app.brokers.mail import AgentMailBroker  # type: ignore

        mail = AgentMailBroker(settings.agentmail_api_key)

    return Brokers(context=context, shopper=shopper, payment=payment, mail=mail)
