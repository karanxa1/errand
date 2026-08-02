"""Credential encryption at rest, and namespaced tool ids.

Two independent units, both cheap and both load-bearing:

  * crypto — every MCP credential is stored encrypted. A refresh token is durable
    access to someone's third-party account, so "is it actually unreadable in the
    column" is worth pinning rather than assuming.
  * tool_id — the model is handed one flat function name per tool and has to be
    able to name a tool unambiguously. The round trip is where the reference
    implementation this is modelled on has a real bug (see the docstring in
    app/mcp/tool_id.py), so it is pinned here.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: F401,E402

from app.config import settings  # noqa: E402
from app.mcp.crypto import (  # noqa: E402
    CredentialCryptoError,
    decrypt_json,
    encrypt_json,
    encryption_available,
)
from app.mcp.tool_id import (  # noqa: E402
    MAX_TOOL_ID_LEN,
    is_mcp_tool_id,
    make_tool_id,
    parse_tool_id,
)

# ── encryption at rest ────────────────────────────────────────────────────────


def test_a_secret_round_trips() -> None:
    # Fixture values are deliberately NOT shaped like real credentials. All a test
    # needs is a distinctive string it can grep for, so a realistic provider key
    # prefix buys nothing and costs a secret-scanner alert plus a reviewer's
    # double-take.
    payload = {"Authorization": "Bearer FIXTURE-not-a-real-token", "X-Api-Key": "k"}
    blob = encrypt_json(payload)
    assert decrypt_json(blob) == payload


def test_the_plaintext_does_not_appear_in_the_stored_blob() -> None:
    """The whole point: what lands in the column must not be readable.

    Checked against the raw secret rather than the JSON, because that is the string
    an operator or a leaked dump would grep for.
    """
    blob = encrypt_json({"Authorization": "Bearer super-secret-token"})
    assert "super-secret-token" not in blob
    assert "Authorization" not in blob
    assert "Bearer" not in blob


def test_encryption_is_not_deterministic() -> None:
    """Fernet includes a random IV, so equal inputs give different ciphertexts.

    Worth pinning: a deterministic scheme would let anyone with read access to the
    table tell which two users configured the same token.
    """
    payload = {"k": "v"}
    assert encrypt_json(payload) != encrypt_json(payload)


def test_an_unreadable_value_degrades_to_absent_rather_than_raising() -> None:
    """A key change must not 500 the whole tool list.

    An orphaned credential has to read as "needs authorizing again", which is what
    returning None means to every caller.
    """
    assert decrypt_json("not-a-fernet-token") is None
    assert decrypt_json(None) is None
    assert decrypt_json("") is None
    # A well-formed token from a DIFFERENT key is the realistic rotation case.
    from cryptography.fernet import Fernet

    foreign = Fernet(Fernet.generate_key()).encrypt(b'{"a":1}').decode()
    assert decrypt_json(foreign) is None


def test_an_explicit_key_is_used_in_preference_to_the_derived_one() -> None:
    from cryptography.fernet import Fernet

    original = settings.mcp_encryption_key
    try:
        settings.mcp_encryption_key = Fernet.generate_key().decode()
        blob = encrypt_json({"a": 1})
        assert decrypt_json(blob) == {"a": 1}
        # Under the derived key that same blob is unreadable, which confirms the
        # explicit key was the one actually in use.
        settings.mcp_encryption_key = ""
        assert decrypt_json(blob) is None
    finally:
        settings.mcp_encryption_key = original


def test_a_malformed_explicit_key_is_named_precisely() -> None:
    """Otherwise this surfaces as an opaque binascii error at write time."""
    original = settings.mcp_encryption_key
    try:
        settings.mcp_encryption_key = "obviously-not-a-fernet-key"
        assert encryption_available() is False
        try:
            encrypt_json({"a": 1})
        except CredentialCryptoError as exc:
            assert "MCP_ENCRYPTION_KEY" in str(exc)
            assert "Fernet.generate_key" in str(exc)
        else:
            raise AssertionError("a malformed key should be refused")
    finally:
        settings.mcp_encryption_key = original


def test_no_key_at_all_reports_unavailable_rather_than_writing_garbage() -> None:
    """A deployment that cannot store a secret must say so BEFORE accepting one."""
    original_key = settings.mcp_encryption_key
    original_jwt = settings.jwt_secret
    try:
        settings.mcp_encryption_key = ""
        settings.jwt_secret = ""
        assert encryption_available() is False
    finally:
        settings.mcp_encryption_key = original_key
        settings.jwt_secret = original_jwt
    assert encryption_available() is True


# ── namespaced tool ids ───────────────────────────────────────────────────────


def test_the_round_trip_survives_underscores_in_both_halves() -> None:
    """The bug in the reference implementation, pinned.

    better-chatbot joins with a single `_` and splits on the first one, so
    ("my_tools", "run") comes back as ("my", "tools_run") and the call fails as
    "unknown tool" on a name the model was told it could use. A `__` separator plus
    a server half that cannot contain one makes the split exact.
    """
    for server, tool in (
        ("my_tools", "run"),
        ("a_b_c", "d_e_f"),
        ("GitHub", "search_repositories"),
        ("crm", "find_customer"),
        # `__` in the TOOL half is fine: it is everything after the first
        # separator, so there is nothing left to disambiguate.
        ("srv", "weird__tool"),
    ):
        tool_id = make_tool_id(server, tool)
        assert parse_tool_id(tool_id) == (server, tool), tool_id


def test_single_underscores_are_preserved_verbatim() -> None:
    """The id is what the model reads and reproduces.

    An escape-every-underscore scheme turns `find_customer` into `find_-customer`,
    which is both ugly and a gratuitous difference from the server's own naming.
    """
    assert make_tool_id("Acme CRM", "find_customer") == "mcp__Acme-CRM__find_customer"
    assert make_tool_id("GitHub", "search_repositories") == (
        "mcp__GitHub__search_repositories"
    )


def test_a_server_name_cannot_contain_the_separator() -> None:
    """What makes the split exact, enforced where names are accepted."""
    from app.mcp.config import McpConfigError, validate_name

    assert validate_name("my_tools") == "my_tools"
    try:
        validate_name("my__tools")
    except McpConfigError as exc:
        assert "two underscores" in str(exc)
    else:
        raise AssertionError("a name containing `__` must be refused")


def test_sanitization_cannot_manufacture_a_separator() -> None:
    """Illegal characters map to `-`, never `_`.

    Mapping to an underscore would let "a b" or "a!b" become "a__b" and silently
    break the split for a name that passed validation.
    """
    for name in ("a b", "a!b", "a  b", "a@#b"):
        tool_id = make_tool_id(name, "run")
        server_half = tool_id[len("mcp__") :].partition("__")[0]
        assert "__" not in server_half, tool_id


def test_ids_satisfy_the_openai_function_name_constraint() -> None:
    """^[a-zA-Z0-9_-]{1,64}$ — a violation is an HTTP 400 on the whole turn."""
    import re

    pattern = re.compile(r"\A[a-zA-Z0-9_-]{1,64}\Z")
    for server, tool in (
        ("GitHub Tools", "search repositories by topic"),
        ("sérvér wîth accénts", "tôöl"),
        ("a" * 80, "b" * 80),
        ("x", "y"),
        ("emoji 🙂 server", "do 🎉 thing"),
    ):
        tool_id = make_tool_id(server, tool)
        assert pattern.match(tool_id), f"{tool_id!r} is not a legal function name"
        assert len(tool_id) <= MAX_TOOL_ID_LEN


def test_long_names_that_share_a_prefix_stay_distinct() -> None:
    """Truncation alone would collapse these onto one id.

    A collision means the model cannot address one of the two tools at all, so the
    truncated half carries a digest of the full name.
    """
    a = make_tool_id("server", "search-repositories-by-topic-" + "x" * 60)
    b = make_tool_id("server", "search-repositories-by-owner-" + "x" * 60)
    assert a != b
    long_server_a = make_tool_id("integration-server-" + "y" * 60, "run")
    long_server_b = make_tool_id("integration-server-" + "z" * 60, "run")
    assert long_server_a != long_server_b


def test_built_in_tool_names_are_not_mistaken_for_mcp_tools() -> None:
    """The dispatch switch relies on this to route correctly."""
    for name in ("run_errand", "web_search", "shop_live", "mcp", "mcp__", "mcp__x"):
        assert not is_mcp_tool_id(name), name
    assert is_mcp_tool_id(make_tool_id("s", "t"))


def test_empty_halves_degrade_to_a_usable_id() -> None:
    """A server that reports a blank tool name must not produce an illegal id."""
    tool_id = make_tool_id("", "")
    assert tool_id.startswith("mcp__")
    assert parse_tool_id(tool_id) is not None


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
