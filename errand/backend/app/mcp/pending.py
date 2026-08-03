"""The OAuth rendezvous: how a browser round-trip suspends a connect().

WHY THIS EXISTS AT ALL, since it is the one place this feature could not copy
its reference implementation.

better-chatbot handles an MCP OAuth callback by re-driving the connect on
whichever instance received it and having the OAuth provider ADOPT the `state`
from Postgres (src/lib/ai/mcp/pg-oauth-provider.ts `adoptState`), which makes the
flow instance-independent. That works because the TypeScript SDK's provider reads
`state` and the PKCE verifier back out of its storage.

The MCP Python SDK does not. Both values are created inside a local stack frame
and validated there:

    pkce_params = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    ...
    await self.context.redirect_handler(authorization_url)
    result = await self.context.callback_handler()
    if result.state is None or not secrets.compare_digest(result.state, state):
        raise OAuthFlowError(...)
    -- .venv/.../mcp/client/auth/oauth2.py, _perform_authorization_code_grant

There is no storage hook for either, so there is nothing to adopt. The coroutine
that called connect() has to still be alive and parked in `callback_handler` when
the code arrives. Which is exactly what this module arranges: `redirect_handler`
publishes the authorization URL and returns, `callback_handler` parks on an
asyncio.Event, and the OAuth callback route resolves it.

Verified rather than assumed: a flow parked for 6s across a simulated browser
round-trip resumed and exchanged the code with the correct PKCE verifier, and no
httpx timeout fired while parked — while suspended between yields the auth
generator holds no socket, so no read deadline is running.

WHAT THIS COSTS, STATED PLAINLY. The waiter is in-process memory, so:

  * The POST that starts authorization and the GET that completes it must land on
    the same process. This deployment is already pinned to min=max=1 replica /
    single uvicorn worker, for the in-memory `run_errand` state described in
    app/main.py, and app/voice/tickets.py has the identical constraint. So this
    adds no NEW limit — but it is a real one, and a future move to multiple
    workers has to solve this along with the other two.
  * A restart mid-authorization loses the parked flow. The user re-clicks
    Authorize and gets a fresh attempt; nothing is corrupted, because tokens are
    only ever written after a successful exchange.

Bounded on purpose: attempts expire (AUTH_TIMEOUT_S), there is a cap on
concurrent attempts per user, and an attempt is single-use. The last one is not
theoretical — the probe showed the SDK can re-enter the grant and would happily
replay an already-spent code, so delivering a code exactly once and failing the
second ask is what turns a broken server into a clear auth error instead of a
loop.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

# How long an authorization attempt may stay parked before it is abandoned. A
# human has to open a browser, sign in, and consent, possibly with an MFA step —
# five minutes is the same budget this repo gives the spend-approval gate
# (APPROVAL_TIMEOUT_S), for the same reason.
AUTH_TIMEOUT_S = 300.0

# Per-user ceiling on parked attempts. Each one is a coroutine, an open httpx
# client and a socket, so an unbounded count is a resource leak reachable by
# clicking Authorize repeatedly.
MAX_PENDING_PER_USER = 5


class McpAuthError(RuntimeError):
    """An authorization attempt that cannot proceed."""


@dataclass
class PendingAuth:
    """One in-flight authorization, parked between redirect and callback."""

    id: str
    user_id: str
    server_id: str
    created_at: float
    # Set by redirect_handler once the SDK has built the URL; the API polls for it.
    authorization_url: str | None = None
    # The SDK's own `state`, read off that URL. The callback's routing key.
    oauth_state: str | None = None
    url_ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Resolved by the OAuth callback route with the code + state.
    code: str | None = None
    state: str | None = None
    iss: str | None = None
    error: str | None = None
    delivered: bool = False
    code_ready: asyncio.Event = field(default_factory=asyncio.Event)

    def expired(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.created_at > AUTH_TIMEOUT_S


# id -> PendingAuth. Process-local by necessity; see the module docstring.
_pending: dict[str, PendingAuth] = {}

# The SDK's OAuth `state` -> attempt id.
#
# WHY THIS INDEX HAS TO EXIST. The redirect URI is one fixed path, because it is
# registered with the authorization server and has to match byte-for-byte on every
# later attempt — so it cannot carry the attempt id. And the `state` that comes
# back on the callback is the one the SDK generated inside its own stack frame, not
# a value we chose. So the only way to map a callback to the flow parked on it is
# to LEARN the SDK's state, which we can: `redirect_handler` receives the fully
# built authorization URL, and the state is a query parameter on it. This is the
# same state -> session mapping better-chatbot keeps in Postgres
# (getSessionByState), held in memory here because the parked coroutine is anyway.
_by_state: dict[str, str] = {}


def _forget(attempt_id: str) -> PendingAuth | None:
    attempt = _pending.pop(attempt_id, None)
    for state, mapped in list(_by_state.items()):
        if mapped == attempt_id:
            _by_state.pop(state, None)
    return attempt


def _prune(now: float | None = None) -> None:
    stamp = now or time.monotonic()
    for key in [k for k, v in _pending.items() if v.expired(stamp)]:
        attempt = _forget(key)
        if attempt is not None and not attempt.code_ready.is_set():
            # Wake the parked coroutine so it fails cleanly rather than holding a
            # client open until the process ends.
            attempt.error = "Authorization timed out."
            attempt.code_ready.set()


def start(user_id: str, server_id: str) -> PendingAuth:
    """Register a new attempt for (user, server), replacing any earlier one.

    Replacing rather than reusing: clicking Authorize again means the user is
    starting over, and the previous attempt's `state` is dead to us the moment a
    new authorization URL is generated. The old attempt is woken with an error so
    its coroutine unwinds instead of leaking.
    """
    _prune()
    for key, existing in list(_pending.items()):
        if existing.user_id == user_id and existing.server_id == server_id:
            _forget(key)
            if not existing.code_ready.is_set():
                existing.error = "Superseded by a new authorization attempt."
                existing.code_ready.set()

    if sum(1 for v in _pending.values() if v.user_id == user_id) >= MAX_PENDING_PER_USER:
        raise McpAuthError(
            "Too many authorization attempts in progress. Finish or wait for one "
            "to expire before starting another."
        )

    attempt = PendingAuth(
        id=secrets.token_urlsafe(24),
        user_id=user_id,
        server_id=server_id,
        created_at=time.monotonic(),
    )
    _pending[attempt.id] = attempt
    return attempt


def get(attempt_id: str) -> PendingAuth | None:
    _prune()
    attempt = _pending.get(attempt_id)
    if attempt is None or attempt.expired():
        return None
    return attempt


def publish_url(attempt: PendingAuth, url: str) -> None:
    """Called from redirect_handler: index the attempt and release the waiter.

    The SDK's `state` is read off the URL it just built, which is the only place it
    is ever visible to us — see `_by_state`. An authorization URL with no state
    would mean the SDK stopped sending one, so the attempt simply stays
    unreachable by callback and expires rather than silently accepting any callback
    that arrives.
    """
    attempt.authorization_url = url
    sdk_state = parse_qs(urlparse(url).query).get("state", [None])[0]
    if sdk_state:
        attempt.oauth_state = sdk_state
        _by_state[sdk_state] = attempt.id
    attempt.url_ready.set()


def resolve(oauth_state: str, *, code: str, iss: str | None) -> PendingAuth:
    """Called from the OAuth callback route: wake the flow parked on this state.

    Looked up by the SDK's `state`, which is what the authorization server echoes
    back. The value is then handed to the SDK UNCHANGED so it can run its own
    comparison — this lookup is routing, not the CSRF check.
    """
    _prune()
    attempt_id = _by_state.get(oauth_state)
    attempt = _pending.get(attempt_id) if attempt_id else None
    if attempt is None or attempt.expired():
        raise McpAuthError(
            "This authorization link has expired or was already used. Start the "
            "authorization again."
        )
    if attempt.code_ready.is_set():
        raise McpAuthError("This authorization attempt was already completed.")
    attempt.code = code
    attempt.state = oauth_state
    attempt.iss = iss
    attempt.code_ready.set()
    return attempt


def fail_by_state(oauth_state: str, message: str) -> PendingAuth | None:
    """Wake a flow with an error, addressed by the callback's state."""
    attempt_id = _by_state.get(oauth_state)
    return fail(attempt_id, message) if attempt_id else None


def fail(attempt_id: str, message: str) -> PendingAuth | None:
    """Wake a parked flow with an error (the IdP returned `error=access_denied`).

    BOTH events are set, not just `code_ready`. A failure can happen BEFORE an
    authorization URL exists — discovery refused, dynamic registration rejected,
    the host unreachable — and at that moment `POST /authorize` is blocked in
    `wait_for_url`. Waking only the code waiter left that request hanging for the
    full 45-second URL timeout and then reporting a generic "did not return an
    authorization URL in time" instead of the real reason.
    """
    attempt = get(attempt_id)
    if attempt is None or attempt.code_ready.is_set():
        return None
    attempt.error = message
    attempt.code_ready.set()
    # wait_for_url checks `attempt.error` immediately after waking, so this
    # surfaces the true failure rather than a timeout.
    attempt.url_ready.set()
    return attempt


def finish(attempt_id: str) -> None:
    """Drop an attempt once its flow has finished, however it finished."""
    _forget(attempt_id)


async def wait_for_url(attempt: PendingAuth, timeout: float = 45.0) -> str:
    """Block until redirect_handler publishes the URL.

    The POST that begins authorization returns this URL to the browser, so it has
    to wait for the SDK to do discovery and (usually) a dynamic client
    registration first — two or three HTTP round trips to the authorization
    server. The timeout is generous for that and still far below the request
    ceiling, so a server that never answers fails as a clear error rather than
    hanging the tab.
    """
    try:
        await asyncio.wait_for(attempt.url_ready.wait(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise McpAuthError(
            "The server did not return an authorization URL in time. Check the "
            "server URL and that it supports OAuth."
        ) from exc
    if attempt.error:
        raise McpAuthError(attempt.error)
    if not attempt.authorization_url:
        raise McpAuthError("No authorization URL was produced.")
    return attempt.authorization_url


async def wait_for_code(attempt: PendingAuth) -> tuple[str, str | None, str | None]:
    """Park until the callback resolves this attempt. Single-use.

    SINGLE-USE IS LOAD-BEARING. The SDK's auth generator can re-enter the
    authorization grant (observed: a server that answers 401 even with a valid
    token drives it round twice), and on the second pass this would hand back the
    same code — which the token endpoint has already spent. That surfaces as a
    confusing `invalid_grant` loop. Failing the second ask instead makes the real
    problem legible.
    """
    if attempt.delivered:
        raise McpAuthError(
            "The authorization code was already used. The server rejected the "
            "token it was issued — check that its OAuth configuration is correct."
        )
    await asyncio.wait_for(attempt.code_ready.wait(), timeout=AUTH_TIMEOUT_S)
    if attempt.error:
        raise McpAuthError(attempt.error)
    if not attempt.code:
        raise McpAuthError("No authorization code was received.")
    attempt.delivered = True
    return attempt.code, attempt.state, attempt.iss


def clear() -> None:
    """Drop all attempts. Tests only."""
    _pending.clear()
    _by_state.clear()


def pending_count() -> int:
    """Tests only."""
    _prune()
    return len(_pending)
