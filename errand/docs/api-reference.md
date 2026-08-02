# Errand — Verified Provider API Reference

Every endpoint/shape below was confirmed against the live API on 2026-08-02,
or taken verbatim from official docs. Build to THIS. Do not guess.

Keys live in `errand/.env` (gitignored). Backend loads them via env.

---

## Prava (payments) — VERIFIED LIVE
- Base (sandbox): `https://sandbox.api.prava.space`  (prod: `https://api.prava.space`)
- Auth: `Authorization: Bearer <PRAVA_SECRET_KEY>` (sk_test_…), server-side ONLY.
- Publishable key (pk_test_…) is client-safe for the iframe SDK.

### POST /v1/sessions  → create session (pins merchant + amount)
Body:
```json
{
  "user_id": "u_demo",
  "user_email": "agent@inbox...",
  "total_amount": "63.00",
  "currency": "USD",
  "description": "...",
  "purchase_context": [{
    "merchant_details": { "name": "Demo Pantry Co", "url": "https://...", "country_code_iso2": "US" },
    "product_details": [{ "description": "...", "unit_price": "18.00", "quantity": 2 }],
    "effective_until_minutes": 15
  }]
}
```
Response 201: `{ session_id, session_token, iframe_url, order_id?, expires_at }`
- `session_id` (sess_… / ses_…) → used for polling + report-status.
- `iframe_url` → open/embed for card entry + passkey.

### GET /v1/sessions/{session_id}/payment-result  → poll (every 3s)
Auth: secret key. Response `{ status, transactions: [...] }`.
- status: "pending" | "awaiting_result" | "completed" | "failed"
- On completed: credential at `transactions[0].line_items[0]`:
  `{ txn_ref_id, token (16-digit network token), dynamic_cvv, expiry_month "MM", expiry_year "YYYY" }`
- On failed: `transactions[0].error = { code, message }`

### POST /v1/sessions/{session_id}/report-status  → REQUIRED after use
Auth: secret key. Body: `{ txn_ref_id, txn_status: "APPROVED"|"DECLINED" }`

### GET /health → `{ status: "ok" }`

---

## Senso (context) — VERIFIED LIVE
- Base: `https://apiv2.senso.ai/api/v1`
- Auth header: `X-API-Key: <SENSO_API_KEY>` (tgr_…)   ← NOT Bearer
- Org: "callmissed". KB already seeded with 2 processed docs
  (business-procurement-policy.md, personal-preferences.md).

### POST /org/search  → AI answer + chunks (THE query endpoint; verified)
Body: `{ "query": "<text>", "max_results": 3, "content_ids"?: [...], "require_scoped_ids"?: bool }`
Response: `{ query, answer, results: [...], total_results, max_results, processing_time_ms }`
- `answer`: synthesized grounded answer (string, markdown).
- `results[]`: `{ content_id, version_id, chunk_index, chunk_text, score, rank, title, vector_id, source_type, content_type }`
- Use `answer` for summary + regex extraction of budget/merchant; use
  `results[].{title,chunk_text}` as citations {source: title, snippet: chunk_text}.

Other search variants (same body): `/org/search/context` (chunks only, no answer),
`/org/search/content` (ids+titles), `/org/search/full` (alias of /org/search),
`/org/search/stream` (SSE tokens).

Verified answers:
- business query "pantry restock budget cap and approved brands" →
  "$200 USD per order … Demo Pantry Co … Blue Bottle/Clif/LaCroix … no energy drinks"
- personal query "weekly grocery budget and favourite items" →
  "$60 … oat milk, dark roast coffee, sparkling water … low sugar"

---

## AgentMail (email) — official docs (Python SDK installed for FE only; backend uses Python SDK too)
- Python: `pip install agentmail` → `from agentmail import AgentMail`
- Auth: `AgentMail(api_key=os.getenv("AGENTMAIL_API_KEY"))` (am_…)
- REST base: `https://api.agentmail.to`

Client API (verified from docs):
- `client.inboxes.create(username?, domain?, display_name?, client_id?, metadata?)` → inbox; `inbox.inbox_id` is the address.
- `client.inboxes.get(inbox_id)` / `client.inboxes.list(limit?, page_token?)`
- `client.inboxes.messages.list(inbox_id, limit?, page_token?, labels?, before?, after?, from?, subject?)`
  → `.messages[]`, each with `message_id`, `thread_id`, `subject`, `from`, `extracted_text`/`text`, timestamps, `labels`.
- `client.inboxes.messages.get(inbox_id, message_id)`
- `client.inboxes.messages.reply(inbox_id, message_id, text, html?, attachments?, reply_all?)`
- `client.inboxes.messages.send(inbox_id, to, subject, text, html?, ...)`
- `client.inboxes.messages.search(inbox_id, q, limit?, ...)` — full-text.
- Errors raise on 4xx/5xx; 429 has Retry-After. `client_id` on create = idempotent retries.
- Reply/read content: prefer `extracted_text`/`extracted_html` (no quoted history).

---

## Deepgram (voice) — official docs
- Voice Agent WS: `wss://agent.deepgram.com/v1/agent/converse`
- Auth: browser uses a short-lived token from our backend (do NOT ship the raw key).
  Backend mints it; the key we have may need a scope that allows `POST /v1/auth/grant`
  (returned FORBIDDEN on first try — confirm key scope, or proxy the WS via backend).
- First client message = `Settings`:
```json
{ "type": "Settings",
  "audio": { "input": {"encoding":"linear16","sample_rate":48000},
             "output": {"encoding":"linear16","sample_rate":16000,"container":"none"} },
  "agent": {
    "language": "en",
    "listen": { "provider": {"type":"deepgram","model":"nova-3"} },
    "think":  { "provider": {"type":"open_ai","model":"gpt-5.6-sol","temperature":0.7},
                "prompt": "<system prompt>",
                "functions": [ <FUNCTION_DEFINITIONS> ] },
    "speak":  { "provider": {"type":"deepgram","model":"aura-2-thalia-en"} },
    "greeting": "..." } }
```
- Function calling: agent emits `FunctionCallRequest`
  `{ type:"FunctionCallRequest", functions:[{ id, name, arguments (JSON string), client_side }] }`.
  Client replies `{ type:"FunctionCallResponse", id, name, content:"<stringified result>" }`.
- `client_side:true` → our app executes (calls our Python backend tools).
  `client_side:false` + endpoint → Deepgram calls our server directly.
- Server events: `SettingsApplied`, `ConversationText`, `UserStartedSpeaking`,
  `AgentThinking`, `AgentStartedSpeaking`, `AgentAudioDone`, plus audio frames.

### Deepgram token minting — OPEN ITEM
`POST https://api.deepgram.com/v1/auth/grant` (Authorization: Token <key>) returned
FORBIDDEN. Options: (a) enable the scope on the key, or (b) backend holds the WS
connection to Deepgram and relays audio/events to the browser over our own WS.
Decide during backend build; (b) is the no-extra-permission fallback and fits the
"Python backend owns secrets" model.

---

## Cloudflare Browser Rendering (shopper) — VERIFIED token valid
- Account ID: in .env. API token valid (verified). Permissions: Browser Run:Edit + Workers Scripts:Edit.
- Python path: Playwright (Python) over CDP to a Cloudflare browser session, OR
  browser-use. (Stagehand is TS-only — NOT used in the Python backend.)
- Endpoint to open a CDP session (confirm during shopper build):
  Browser Rendering REST/`/browser-rendering` + the CDP websocket URL.

---

## OpenAI (LLM) — VERIFIED LIVE
- Models on this key: `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` (all respond).
- Selector: Sol (flagship, default) / Terra (balanced) / Luna (fast).
- Used both as Deepgram `think` provider model AND for the text chat route.
