# Errand — SDD progress ledger (Python backend + Next frontend)

Architecture: standalone Python FastAPI backend (NOT serverless) + Next.js frontend.
All provider contracts VERIFIED live (see errand/docs/api-reference.md).

## Backend (Python/FastAPI) — DONE this wave
- [x] Pydantic contracts (app/contracts.py)
- [x] Config from errand/.env (app/config.py)
- [x] Prava PaymentBroker — VERIFIED live sandbox (real session created)
- [x] Senso ContextBroker — VERIFIED live (budget+merchant+citations, both profiles)
- [x] Mock shopper/mail/payment/context (app/brokers/mock.py)
- [x] Orchestrator run_errand (async, emits audit events)
- [x] SSE streaming + approval gate (app/main.py, app/orchestrator/stream.py)
- [x] Full SSE flow verified end-to-end (real Senso + approval + stream + completion)

## Pending (parallel agents, disjoint files)
- [ ] AgentMail broker (app/brokers/mail.py) — docs verified, Python SDK installed
- [ ] Cloudflare shopper broker (app/brokers/shopper.py) — Playwright/CDP over Browser Rendering
- [ ] Deepgram token minting decision (grant scope vs backend WS relay)
- [ ] Frontend (Next.js): Deepgram UI kit, model selector SVGs, cart/approval + Prava iframe, audit log, SSE consumer

Base commit for this wave: 96cc29e
