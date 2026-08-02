# Live agentic browser shopping — design

Date: 2026-08-03
Status: approved (user), implementing in slices

## Problem

Today the shopper is a FIXED script. `build_cart` reads the policy budget, calls
`_select_items` (a deterministic budget-filler), clicks add-to-cart, reads the
total, and returns. The LLM's `intent` is never used by the shopper, so:

- "make it cheaper" / "under $10" changed nothing (fixed by the `max_cents` cap —
  see `tests/test_spend_cap.py`, already landed in this branch).
- the agent cannot add a specific product, remove one, or react to what is
  actually on the shelf. It runs one predetermined pass.

The user wants the LLM to actually DRIVE the browser — observe the store, add and
remove specific products to satisfy the request, then pay through Prava — and to
**watch the browser live in the chat while it works**. Demo store now; real
merchants behind the existing `USE_PRAVA_SHOP` flag.

## What already exists (verified, do not rebuild)

- Real browser automation over Cloudflare Browser Rendering CDP + a local
  Playwright fallback (`app/brokers/shopper.py`, `_page()` picks the backend per
  URL). Verified against the demo store DOM by `tests/test_demo_store.py`.
- The full money path: `build_cart → create_session (Prava) → human approval +
  passkey → poll_credential → complete_checkout (fills the Prava token on the
  merchant page) → report_status`. Pinned by `tests/test_merchant_fallback.py`.
- SSE transport (`app/orchestrator/stream.py`) and a shared pure reducer
  (`lib/errandReducer.ts`) that renders every frame as a thread card. The voice
  relay reuses both.
- The real-merchant wallet/UCP shopper (`app/brokers/prava_shop.py`), gated by
  `USE_PRAVA_SHOP`, production-only (a real card at a real merchant).

## Design

### 1. Agentic shop loop (replaces the fixed pass, demo store)

A new method on the shopper: `agentic_build_cart(merchant_url, intent, context,
observe→decide loop)`. Rather than the shopper deciding items, it exposes the
shelf + cart state and lets the LLM choose actions, bounded and safe:

- `observe()` — read the catalog (`[data-product-id]` + name/brand/price) and the
  current cart contents + `#cart-total`. Returns a compact JSON the LLM sees.
- `add(product_id, qty)` — click `button[data-add=…]` qty times.
- `remove(product_id, qty)` — the demo store needs a remove hook; add
  `button[data-remove=…]` + a per-line qty readout to the storefront DOM and pin
  it in `test_demo_store.py`.
- `done()` — stop; read the authoritative `#cart-total` and return the CartResult.

The loop runs INSIDE the errand, before the existing approval+Prava steps, so
**every existing money-path invariant is preserved unchanged**: the card still
follows the cart, the re-quote guard, the merchant-mismatch guard, the
budget/`max_cents` ceiling (a disallowed or over-budget add is refused by the
loop, not the human). The LLM chooses WHAT goes in the cart; it cannot loosen any
spend rule.

Bounds: max N actions (step budget), policy `_is_disallowed` refuses banned
adds, running total refused above `min(policy, max_cents)`. The loop is a
sub-agent driven by the same OpenAI-compatible model, with a strict tool schema
(observe/add/remove/done) — NOT free navigation to arbitrary URLs on the demo
path.

### 2. Live browser view in the chat

The shopper captures `page.screenshot()` (JPEG, low quality, downscaled) after
each action and pushes it over SSE as a new frame `browser.frame`
`{ run_id, seq, mime, b64, caption }`. The reducer stores only the LATEST frame
(never accumulates — bounded memory, no leak). A new `BrowserView` thread card
renders it: an image that updates in place as the agent works, with the current
action as a caption. Content is a real `<img>` present by default — no animation
gating. When the run reaches a terminal state the last frame stays as a still.

This is periodic screenshots, not raw CDP screencast: `page.screenshot()` is in
Cloudflare's own documented example and already used here, so it is verifiable;
CDP `Page.startScreencast` relay is not something we can confirm against the
Cloudflare endpoint without a live account, so it is explicitly out of scope.
Frame cadence is throttled (≤ ~1–2/s) to bound SSE volume.

### 3. Real merchants behind the flag

When `USE_PRAVA_SHOP` is on, the agentic loop drives the wallet/UCP shopper
(`prava_shop.py`) instead of the demo DOM: observe = search/product/quote, add/
remove = adjust the line set, done = checkout via the wallet. Screenshots are
unavailable there (no page we drive), so the browser view degrades to the audit
timeline. This path is production-only and spends a real card, so it is NOT
auto-verified — it ships behind the flag and is exercised only with a human
present (per AGENTS.md).

## Invariants (unchanged, still pinned)

The card follows the cart; a re-quoted total must equal the approved total; a
timed-out checkout is never retried; approval requires an on-screen passkey; the
demo storefront DOM is a contract. The agentic loop sits BEFORE the session mint,
so none of these move.

## Testing / verification

- `test_demo_store.py`: add the new remove-button + per-line qty hooks to the
  contract.
- New `test_agentic_shop.py`: a fake page-driver proves observe→add→remove→done
  produces the intended cart, refuses a banned add, and refuses an add that would
  breach `min(policy, max_cents)`.
- New reducer test: `browser.frame` keeps only the latest frame (bounded).
- Frontend: `tsc`, `vitest`, OpenNext build. Backend: all standalone tests.
- Live demo-store run verified locally with the LOCAL Playwright path.

## Out of scope (explicit)

- Raw CDP screencast video (unverifiable against Cloudflare here → screenshots).
- Auto-running the real-merchant path (real money; human-gated).
- Free-form open-web navigation on the demo path (bounded tool surface only).
