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
        else PravaPaymentBroker(settings.prava_secret_key, settings.prava_api_base)
    )
    # Shopper + Mail: real implementations pending (parallel agents).
    shopper: ShopperBroker = MockShopperBroker()
    mail: MailBroker = MockMailBroker()
    if settings.use_mock_shopper is False:
        from app.brokers.shopper import CloudflareShopperBroker  # type: ignore

        shopper = CloudflareShopperBroker()
    if settings.use_mock_mail is False:
        from app.brokers.mail import AgentMailBroker  # type: ignore

        mail = AgentMailBroker(settings.agentmail_api_key)

    return Brokers(context=context, shopper=shopper, payment=payment, mail=mail)
