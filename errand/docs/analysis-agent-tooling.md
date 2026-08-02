# Errand — Agent Orchestration, Tool-Calling & Payment Patterns

Read-only analysis of `/Users/macbook/prava/reference-repos/*` mapped onto the
Errand FastAPI backend. Every candidate cites exact source paths + line ranges.
Effort: **S** ≈ hours, **M** ≈ 1–2 days, **L** ≈ 3+ days.

Current Errand state (baseline for "fit"):
- `backend/app/orchestrator/run_errand.py:32-138` — linear 8-step engine, emits
  `AuditEvent` per step, single approval gate via `approve()` callback.
- `backend/app/main.py:73-134` — SSE stream + in-memory approval futures
  (`_approvals`), 300s `wait_for` timeout, errors surfaced as `run.error` events.
- `backend/app/brokers/__init__.py:23-54` — `Brokers` dataclass (context/shopper/
  payment/mail) behind Pydantic Protocols (`contracts.py:135-162`).
- **Gap:** the Deepgram voice agent's `think.functions` → tool bridge is NOT built.

---

## 1. Top 5 Recommendations (ranked by value ÷ effort)

### #1 — Build the Deepgram function-calling → broker bridge (backend-relay)
**What:** A WS relay in the Python backend that holds the Deepgram Voice Agent
socket, receives `FunctionCallRequest`, dispatches to a `FUNCTION_MAP`, and
returns `FunctionCallResponse`. This is the single biggest gap and the whole
point of the voice UX.
**Source:** `reference-repos/dg-fn-calling/client.py:300-383` (the request→
execute→response loop), `functions.py:264-273` (`FUNCTION_MAP` dispatch table),
`business_logic.py:201-226` (filler-message protocol). Newer batched wire shape
confirmed in `reference-repos/deepgram-agent/packages/sdk/src/agent-session.ts:141-143,326-356`.
**Why it helps Errand:** Turns voice intents into the exact broker calls we
already have (`Brokers` in `brokers/__init__.py:23-28`). The relay is also the
Deepgram-token workaround already flagged in `docs/api-reference.md:121-126`
(backend holds the WS = no `auth/grant` scope needed).
**How to adopt:** New module `backend/app/voice/agent_relay.py` + `FUNCTION_MAP`
mapping the 5 Errand tools to brokers (full design in §2).
**Effort:** L. **Risk:** Med — protocol version drift (see §2 warning); mitigate
by centralizing the wire shape in one `_send_function_response()` helper.

### #2 — Adopt `needsApproval` predicate + typed resume tokens for the approval gate
**What:** agents-starter gates a tool on a *predicate over the arguments*
(`needsApproval: async ({a,b}) => Math.abs(a)>1000`), not a hardcoded step. The
UI resolves it with a stable `approvalId`, and the run resumes exactly there.
**Source:** `reference-repos/agents-starter/src/server.ts:99-128`
(`calculate` tool with `needsApproval`), `src/app.tsx:148-194` (approval-requested
render + `addToolApprovalResponse({id, approved})`), `:196-215` (explicit
`output-denied` rejected state).
**Why it helps Errand:** Our gate is hardcoded at `run_errand.py:85-92` and keyed
only by `run_id` (`main.py:39,128-134`) — one pending approval per run, no
approval id, no typed decline reason. Prava requires exactly one hard stop
(pre-spend confirm) and we should make that gate first-class: an `approval_id`,
a `reason` on decline, and a threshold predicate (e.g. only gate when
`cart.total_cents > auto_approve_ceiling`).
**How to adopt:** Extend `ApprovalFn` (`run_errand.py:25`) to return a small
result object `{approved: bool, reason: str|None, approval_id: str}`; add
`approval_id` to the `approval.request` event (`main.py:84`) and match it in
`/approve` (`main.py:128-134`) instead of matching on `run_id` alone.
**Effort:** S. **Risk:** Low — additive.

### #3 — Structured `ActionResult` + navigate health-check/retry for the shopper
**What:** browser-use returns a structured `ActionResult(extracted_content,
error, long_term_memory, is_done, success)` from every action (never throws to
the loop), and its `navigate` does an **empty-DOM health check → wait 3s →
reload → wait 5s → structured error** before giving up.
**Source:** `reference-repos/browser-use/browser_use/tools/service.py:507-558`
(navigate retry ladder + `_page_appears_empty`), `:498-500` (search returns
`ActionResult(error=…)` not raise). Agent-level knobs: `AGENTS.md` →
`max_failures=3`, `step_timeout=120`, `llm_timeout=90`, global action timeout
`_ACTION_TIMEOUT_FALLBACK_S=180` (`service.py:99-142`).
**Why it helps Errand:** Our `CloudflareShopperBroker` (`brokers/__init__.py:46-48`)
will hit SPA-not-rendered, anti-bot, and timeout failures at checkout. Adopting
the "wait-then-reload-then-structured-error" ladder + a per-action timeout makes
`build_cart`/`complete_checkout` resilient instead of hanging the SSE stream.
**How to adopt:** In the shopper broker, wrap CDP navigations with the empty-DOM
recheck (verbatim shape in §4), and give every shopper action an
`asyncio.wait_for(..., timeout=...)` guard. Return typed failures the orchestrator
can turn into `cart.failed` audit events rather than raising.
**Effort:** M. **Risk:** Med — Cloudflare CDP timing differs from local Chromium;
tune the 3s/5s waits.

### #4 — Prava report-status discipline: always report, even on DECLINE + mandate pre-check
**What:** prava-skills makes report-status **mandatory on every outcome incl.
DECLINED** ("Skipping this leaves the transaction stuck in awaiting_result"), and
checks for an existing **mandate** before minting a fresh session.
**Source:** `reference-repos/prava-skills/prava-sdk-integration/references/integration-flow.md:195-219`
(report-status required, common mistakes table), `prava-pay/SKILL.md` buy-flow
step 0 ("Check for a mandate first"), `prava-pay/references/about-prava.md:93`
(revoke/cancel mandate stops future charges).
**Why it helps Errand:** We only report `APPROVED` (`run_errand.py:120`). If
checkout fails *after* the credential is issued (`run_errand.py:116`), or the
operator declines late, we never send `DECLINED` → transaction stuck. Also we
have no revoke path if the run aborts after `create_session`.
**How to adopt:** Wrap steps 6-7 (`run_errand.py:116-121`) in try/finally: on any
checkout exception, `report_status(session_id, txn_ref_id, "DECLINED")` before
re-raising. Add a `payment.report_status` call on the decline branch
(`run_errand.py:89-91`) once a session exists. Emit `payment.reported` with the
actual status either way.
**Effort:** S. **Risk:** Low — strictly safer; verify `report_status` is
idempotent server-side before double-calling.

### #5 — Guardrail classifier + `stopWhen` step cap for the orchestrator loop
**What:** openai-realtime-agents runs a cheap moderation classifier over agent
output with a **fail-open** wrapper (guardrail error → `tripwireTriggered:false`,
never blocks the call), and agents-starter caps tool loops with
`stopWhen: stepCountIs(20)` + threads an `abortSignal` through `streamText`.
**Source:** `reference-repos/openai-realtime-agents/src/app/agentConfigs/guardrails.ts:80-99`
(fail-open guardrail), `reference-repos/agents-starter/src/server.ts:182-183`
(`stopWhen` + `abortSignal`), `:148-150` (idempotent scheduling).
**Why it helps Errand:** Once voice tools can drive spend, we need (a) a cap so a
misbehaving model can't loop tool calls, and (b) cancellation so the operator can
kill an in-flight run. Our SSE run (`main.py:90-110`) has no cancel token and the
orchestrator has no step cap.
**How to adopt:** Pass an `asyncio.Event` cancel token into `run_errand` and
check it between steps; add a `max_tool_calls` counter in the voice relay's
dispatch loop; keep any guardrail fail-open so moderation outages never wedge a
run.
**Effort:** M. **Risk:** Low.

---

## 2. Design: Deepgram Function-Calling → Broker Bridge (the biggest gap)

Blueprint = `reference-repos/dg-fn-calling/client.py` receiver loop, ported to a
**server-side relay** (we hold the Deepgram socket, per `api-reference.md:121-126`).

> ⚠️ **Protocol version mismatch — decide once, centralize.** The Python reference
> `dg-fn-calling` uses the **older flat shape**: request fields
> `function_name` / `function_call_id` / `input`, response
> `{type:"FunctionCallResponse", function_call_id, output}`, endpoint
> `wss://agent.deepgram.com/agent`, settings type `SettingsConfiguration`
> (`client.py:169,300-303,374-378,86`). Our `docs/api-reference.md:99-119` and the
> TS SDK use the **newer batched shape**: `FunctionCallRequest.functions[] =
> {id,name,arguments (JSON string),client_side}`, response `{type:
> "FunctionCallResponse", id, name, content}`, endpoint
> `wss://agent.deepgram.com/v1/agent/converse`, settings type `Settings`
> (`deepgram-agent/.../agent-session.ts:141-143`; `api-reference.md:113-116`).
> **Build to the newer batched shape** (matches our verified api-reference) and
> isolate every wire field in one encode/decode helper so a future flip is a
> one-line change.

### Tool map (voice tool → Errand broker/orchestrator)
| Voice tool | Executes | Errand target |
|---|---|---|
| `getContext` | `brokers.context.get_context(profile, intent)` | `contracts.py:136` |
| `buildCart` | `brokers.shopper.build_cart(url, intent, ctx)` | `contracts.py:140` |
| `createSession` | `brokers.payment.create_session(CreateSessionInput)` | `contracts.py:149` |
| `approveSpend` | emit `approval.request`, await gate | `main.py:82-88` |
| `waitForConfirmation` | `brokers.mail.wait_for_confirmation(...)` | `contracts.py:158` |

Plus two protocol helpers copied from the reference: `agentFiller`
(`business_logic.py:201-226`) and `endCall` (`:228-253`).

### Flow (newer batched protocol)
```
Browser mic ─audio─▶ Backend relay ─WS─▶ Deepgram Voice Agent
                          ▲                       │
                          │   FunctionCallRequest │  {functions:[{id,name,
                          │◀──────────────────────┘   arguments,client_side}]}
              for each fn: FUNCTION_MAP[name](json.loads(arguments))
                          │
   FunctionCallResponse ──┘  {type,id,name,content: json.dumps(result)}
```

### Skeleton (`backend/app/voice/agent_relay.py`, new file)
Mirrors `dg-fn-calling/client.py:300-383` but batched + server-side, and reuses
the existing `EventStream`/`AuditEvent` so voice steps show up in the same SSE
feed as text runs.

```python
# FUNCTION_MAP: name -> async (args: dict, ctx: RelayCtx) -> dict
async def _dispatch(msg, ws, ctx):
    responses = []
    for fn in msg["functions"]:                 # BATCHED: loop the array
        name, call_id = fn["name"], fn["id"]
        args = json.loads(fn.get("arguments") or "{}")
        # filler while a slow tool runs (business_logic.py:201-226)
        if name in ("buildCart", "waitForConfirmation"):
            await ws.send(json.dumps({"type": "InjectAgentMessage",
                                      "message": "One moment…"}))
        try:
            result = await FUNCTION_MAP[name](args, ctx)   # -> broker call
        except KeyError:
            result = {"error": f"unknown function {name}"}
        except Exception as e:                              # never crash the loop
            result = {"error": str(e)}                      # cf. client.py:366-368
        responses.append({"type": "FunctionCallResponse",
                          "id": call_id, "name": name,
                          "content": json.dumps(result)})   # STRINGIFIED
    for r in responses:
        await ws.send(json.dumps(r))
```

Key rules carried over from the reference:
- **Stringify `content`** (`client.py:377`, `agent-session.ts:141-143`).
- **Filler must be a tool/inject, never free-text** — the model is prompted to
  call `agentFiller` before slow lookups (`client.py:143-158`,
  `functions.py:82-96`). For Errand, inject "One moment…" before `buildCart`.
- **`approveSpend` bridges to the existing gate:** the tool emits
  `approval.request` (`main.py:84`) and awaits the same `asyncio.Future`
  (`main.py:79-88`) so voice and click share one approval path. Return
  `{approved, reason}` as the tool result so the model can speak the outcome.
- **`endCall` sequencing:** send response → inject farewell → wait for
  `AgentAudioDone` → close (`client.py:338-362,495-536`). Don't `os._exit`
  (reference does at `:362`) — in FastAPI just close the socket + cancel the task.
- **Reconnect with jittered backoff** if the Deepgram socket drops:
  `base = min(baseDelay * 2**(n-1), maxDelay); delay = base*(0.8+rand*0.4)`
  (`agent-session.ts:364-386`).
- Reuse `run_errand`'s broker instances via `build_brokers()`
  (`brokers/__init__.py:31-53`) so voice and text share config/mocks.

---

## 3. REJECT list (patterns NOT to adopt)

- **Multi-agent handoff graph** (`openai-realtime-agents/.../simpleHandoff.ts:10-25`,
  `customerServiceRetail/*`). Errand is a single deterministic pipeline
  (`run_errand.py:32-138`); a handoff mesh adds routing complexity with no payoff.
- **Cloudflare `AIChatAgent` / Durable-Object agent runtime**
  (`agents-starter/src/server.ts:14-19`). We're long-running FastAPI, not
  serverless DO hibernation; `waitForMcpConnections`/`chatRecovery` solve
  problems we don't have. Adopt only the `needsApproval` *pattern* (#2), not the
  runtime.
- **PyAudio mic/speaker + janus threads** (`dg-fn-calling/client.py:200-472`).
  That's a desktop client; our audio lives in the browser and the backend only
  relays WS frames. Port the receiver *logic*, drop the audio device code.
- **browser-use `@sandbox` cloud runtime / `ChatBrowserUse`** (AGENTS.md). We use
  Cloudflare Browser Rendering over CDP (`api-reference.md:130-135`); pulling in
  browser-use's cloud stack duplicates infra. Take the resilience *patterns* (#3)
  only.
- **Prava CLI link/keypair machinery** (`prava-skills/src/commands/status.ts`,
  `src/crypto/*`, `http/client.ts` semver-verdict). That's for CLI agents linking
  a user account; Errand talks to Prava server-side with `sk_test_` keys
  (`api-reference.md:10-13`). Keep the *report-status/mandate discipline* (#4),
  skip the CLI/link layer.
- **Mock-data generator** (`dg-fn-calling/business_logic.py:9-115`). Demo scaffold
  only.

---

## 4. Copy-able utilities (with source paths)

**A. Empty-DOM navigate health-check → retry ladder** (for the shopper broker).
Source: `browser-use/browser_use/tools/service.py:514-544`.
```python
def _page_appears_empty(state) -> bool:
    return state.dom_state._root is None or not state.dom_state.llm_representation().strip()

# after navigate: recheck → wait 3s → recheck → reload → wait 5s → structured error
if url_is_http and _page_appears_empty(state):
    await asyncio.sleep(3.0); state = await get_state()
    if _page_appears_empty(state):
        await reload(); await asyncio.sleep(5.0); state = await get_state()
        if state.dom_state._root is None:
            return ActionResult(error="Page loaded but returned empty content …")
```

**B. Defensive env-timeout parser** (per-action timeout, never crash on bad env).
Source: `browser-use/browser_use/tools/service.py:113-142`.
```python
_FALLBACK_S = 180.0
def _parse_env_timeout(raw: str | None) -> float:
    if not raw: return _FALLBACK_S
    try:
        v = float(raw)
        return v if math.isfinite(v) and v > 0 else _FALLBACK_S
    except ValueError:
        return _FALLBACK_S
```

**C. Jittered exponential-backoff reconnect** (for the Deepgram relay socket).
Source: `deepgram-agent/packages/sdk/src/agent-session.ts:364-386`.
```python
base = min(base_delay * 2 ** (attempt - 1), max_delay)
delay = base * (0.8 + random.random() * 0.4)  # ±20% jitter
```

**D. Fail-open guardrail wrapper** (moderation outage must never wedge a run).
Source: `openai-realtime-agents/src/app/agentConfigs/guardrails.ts:84-98`.
```python
async def guardrail(text: str) -> bool:  # True = tripwire
    try:
        return (await classify(text)) != "NONE"
    except Exception:
        return False  # fail open
```

**E. Filler-message protocol** (return response first, inject message second).
Source: `dg-fn-calling/business_logic.py:201-226` + `client.py:318-336`.
```python
# 1) send FunctionCallResponse (content=json.dumps({"status":"queued"}))
# 2) then send {"type":"InjectAgentMessage","message":"Let me look that up…"}
```

**F. De-dupe-once warning helper** (avoid log spam in poll loops).
Source: `prava-skills/src/http/client.ts:104-111`.
```python
_warned: set[str] = set()
def warn_once(key: str, msg: str) -> None:
    if key in _warned: return
    _warned.add(key); logger.warning(msg)
```

---

### 5-line summary
1. **Build the Deepgram→broker relay** (§2): hold the WS server-side, loop
   `FunctionCallRequest.functions[]`, dispatch a `FUNCTION_MAP` of 5 tools to our
   brokers, return stringified `FunctionCallResponse` — this closes the top gap
   and doubles as the token workaround. **Build to the newer batched shape.**
2. **Upgrade the approval gate** with a `needsApproval` threshold predicate,
   `approval_id`, and typed decline `reason` (agents-starter `server.ts:99-128`).
3. **Harden the shopper** with browser-use's empty-DOM wait→reload→structured-
   error ladder + per-action `wait_for` timeouts (`service.py:514-544`).
4. **Fix Prava discipline:** always `report_status` (incl. `DECLINED`) via
   try/finally + mandate pre-check (`integration-flow.md:195-219`).
5. **Add loop safety:** step cap + `asyncio.Event` cancel token + fail-open
   guardrail so voice-driven runs can't loop or wedge (`server.ts:182-183`,
   `guardrails.ts:84-98`).
