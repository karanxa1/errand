"""Guards for the two fields Prava forwards to the card network.

`merchant_details.url` and the customer email do not stay inside Prava. Both are
handed to Visa, and Visa's acceptance rules are stricter than Prava's request
schema — which is why a body that passes validation and returns 201 can still
kill the payment several steps later, with an error that names none of this.

Two failure modes, both observed in sandbox and both silent until the worst
possible moment:

  * A reserved-TLD customer email (`@acme.local`, `@shop.test`). Everything
    works — the card is added, the OTP arrives, the OTP is accepted — and then
    passkey registration fails at the very last step (PASSKEY_REG_FAILED) with a
    generic error. Note `example.com` is FINE: `.com` is a real delegated TLD.
    It is `something.example` that is not.

  * A merchant URL that is not a bare https origin on a real TLD. A scheme typo
    (`htttps://`), a missing scheme, a made-up TLD (`.demo`, `.nep`), or a full
    product path instead of an origin all fail at the authentication step,
    before any card is charged, as a generic 400. One wrong character breaks
    100% of that merchant's checkouts.

Both are cheap to check and impossible to debug from the symptom, so they are
checked here, at the last point before the request leaves us, rather than
trusted from whichever caller built the value.

On "any made-up TLD": the explicit reserved list below is enforced offline and
deterministically. Catching an arbitrary invented TLD needs the IANA root zone,
which we do not carry — `assert_resolvable()` is the opt-in check for that, and
it is DNS-backed rather than list-backed. See PRAVA_VERIFY_MERCHANT_DNS.
"""

from __future__ import annotations

import re
import socket
from urllib.parse import urlparse

# Reserved / special-use TLDs that can never be delegated on the public
# internet, per RFC 2606 and RFC 6761 plus the ones Prava calls out. A merchant
# or customer on any of these is unreachable by definition.
BANNED_TLDS = frozenset(
    {
        "local",
        "test",
        "example",
        "demo",
        "invalid",
        "localhost",
        "internal",
        "devices",
    }
)

# A TLD is alphabetic and at least two characters (or an xn-- punycode label).
# This rejects numeric and single-letter garbage without needing a root-zone list.
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$")

_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+)$")


class PravaValidationError(ValueError):
    """A value that would be rejected downstream, caught before we send it.

    Carries a message written for whoever has to fix it, naming the field, the
    offending value and the downstream symptom — because the symptom on its own
    (a generic 400, or a last-step passkey failure) points nowhere near here.
    """


def _tld_of(host: str) -> str:
    return host.rsplit(".", 1)[-1].lower() if "." in host else ""


def _check_host(host: str, *, field: str, value: str) -> None:
    if not host:
        raise PravaValidationError(f"{field} has no host: {value!r}")
    if host != host.strip() or " " in host:
        raise PravaValidationError(f"{field} host contains whitespace: {value!r}")
    if "." not in host:
        # A single label ("localhost", "shop") is never routable, and a bare
        # hostname is also what you get from forgetting the scheme.
        raise PravaValidationError(
            f"{field} must be a fully-qualified domain, got {host!r} in {value!r}"
        )
    tld = _tld_of(host)
    if tld in BANNED_TLDS:
        raise PravaValidationError(
            f"{field} uses the reserved TLD .{tld} ({value!r}). Reserved TLDs are "
            f"rejected by the card network — use a real, delegated domain. "
            f"(example.com is fine; something.example is not.)"
        )
    if not _TLD_RE.match(tld):
        raise PravaValidationError(
            f"{field} has an implausible TLD .{tld} ({value!r}); it must be a "
            f"real, delegated TLD."
        )


def validate_customer_email(email: str) -> str:
    """Return the email, or raise if the card network would reject it.

    Rejects reserved TLDs. This is the value forwarded during passkey
    registration; getting it wrong fails the payment at the LAST step, after the
    OTP has already been accepted, as PASSKEY_REG_FAILED.
    """
    value = (email or "").strip()
    match = _EMAIL_RE.match(value)
    if not match:
        raise PravaValidationError(f"customer email is not a valid address: {email!r}")
    _check_host(match.group(1).lower(), field="customer email domain", value=value)
    return value


def merchant_origin(url: str) -> str:
    """Normalize a merchant URL to the bare https origin the card network wants.

    Returns `https://host` — scheme and host only. A path is DROPPED rather than
    rejected: the shopper legitimately needs a deep link to navigate to (the demo
    storefront's URL ends in /store/index.html), but the card network wants the
    merchant's identity, not the page. Sending the path is a generic 400 at
    authentication on every single checkout for that merchant.

    Everything else — a scheme typo, a missing scheme, http, a reserved TLD — is
    an error, because none of those have a safe interpretation.
    """
    value = (url or "").strip()
    if not value:
        raise PravaValidationError("merchant url is empty")
    parsed = urlparse(value)
    if not parsed.scheme:
        # "www.acme.com" — urlparse reads the whole thing as a path.
        raise PravaValidationError(
            f"merchant url is missing its scheme: {value!r} (expected https://…)"
        )
    if parsed.scheme != "https":
        raise PravaValidationError(
            f"merchant url must be https, got {parsed.scheme!r} in {value!r}"
            + (" — check for a typo in the scheme." if parsed.scheme.startswith("htt") else "")
        )
    host = (parsed.hostname or "").lower()
    _check_host(host, field="merchant url", value=value)
    # Port is dropped along with the path: an origin the card network can match
    # against the merchant of record has neither.
    return f"https://{host}"


def assert_resolvable(host: str) -> None:
    """Raise if `host` does not resolve in DNS.

    The list-based checks above cannot know that `.nep` is invented or that
    `demo-pantry.example.com` was never delegated; DNS can. Opt-in
    (PRAVA_VERIFY_MERCHANT_DNS) because it adds a network hop to session
    creation, and because it FAILS CLOSED — appropriate for a spend path, but
    not something to switch on without knowing your resolver is reliable.

    A resolver error that is not "no such host" is treated as inconclusive and
    allowed through: we are checking the merchant, not our own DNS.
    """
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        if exc.errno in (socket.EAI_NONAME, socket.EAI_NODATA):
            raise PravaValidationError(
                f"merchant host {host!r} does not resolve — the card network "
                f"cannot accept a merchant that does not exist."
            ) from exc
    except OSError:
        return  # inconclusive; not the merchant's fault
