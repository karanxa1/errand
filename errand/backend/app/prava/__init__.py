"""Prava wallet-side integration — agent identity, signing, and the wallet API.

Two DIFFERENT Prava products are in play in this codebase, with different hosts,
different credentials, and different capabilities. Keeping them straight is the
whole reason this package exists:

  * The MERCHANT / SDK API (`app.brokers.prava`) — `sandbox.api.prava.space` with
    an `sk_test_` secret key. WE are the merchant. It mints a scoped Visa network
    token after the user enters a card in Prava's PCI iframe. It has NO catalog
    and NO storefront: there is nothing to buy through it.

  * The WALLET / AGENT API (this package) — `pay-api.prava.space`, authenticated
    by an Ed25519 keypair that the USER approved in their Prava wallet. This is
    the side that can actually shop: `/v1/wallet/shop/{search,product,quote,
    checkout}` reaches real UCP-indexed merchants and drives their checkout.

The wallet API is PRODUCTION ONLY — there is no sandbox host for it (verified:
sandbox.pay-api / pay-api.sandbox / sandbox.pay do not resolve). So a linked
agent spends a REAL card at a REAL merchant. Nothing in this package runs unless
an operator has explicitly linked an agent and set `USE_PRAVA_SHOP=true`.
"""
