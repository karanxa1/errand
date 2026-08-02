"""MCP config validation: the SSRF guard and the stdio gate.

These are security boundaries, not formatting checks. A user-supplied URL is
fetched BY THIS BACKEND, which holds live payment credentials and sits inside a
VNet, so `validate_remote_url` is what stands between a registered server and the
cloud metadata endpoint. And a stdio config is a command this backend spawns.

Runs under pytest if installed, standalone otherwise (house style — see
tests/conftest.py).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: F401,E402 — sets env before app.* imports

from app.config import settings  # noqa: E402
from app.mcp.config import (  # noqa: E402
    McpConfigError,
    is_remote,
    is_stdio,
    redact_config,
    transport_of,
    validate_config,
    validate_headers,
    validate_name,
    validate_remote_url,
)


def _expect_error(fn, *args, contains: str = "") -> str:
    try:
        fn(*args)
    except McpConfigError as exc:
        message = str(exc)
        if contains:
            assert contains.lower() in message.lower(), (
                f"expected {contains!r} in {message!r}"
            )
        return message
    raise AssertionError(f"{fn.__name__} accepted {args!r}, expected a refusal")


# ── the SSRF guard ────────────────────────────────────────────────────────────


def test_link_local_metadata_address_is_refused() -> None:
    """169.254.169.254 is the cloud instance metadata endpoint.

    On Azure (this backend's host) that is IMDS, which serves managed-identity
    tokens to anything that can make an HTTP request from the instance. This is
    the single most important case in the file.
    """
    _expect_error(
        validate_remote_url, "https://169.254.169.254/metadata/instance",
        contains="non-public",
    )


def test_loopback_and_private_ranges_are_refused() -> None:
    for url in (
        "https://127.0.0.1/mcp",
        "https://10.0.0.5/mcp",
        "https://192.168.1.10/mcp",
        "https://172.16.4.4/mcp",
        # CGNAT — routable-looking, and not public.
        "https://100.64.0.1/mcp",
        # IPv6 loopback, and the IPv4-mapped form that is "global" by v6 rules
        # while being loopback in effect.
        "https://[::1]/mcp",
        "https://[::ffff:127.0.0.1]/mcp",
    ):
        _expect_error(validate_remote_url, url, contains="non-public")


def test_a_hostname_resolving_into_a_private_range_is_refused() -> None:
    """The check that is easy to forget.

    A public-looking name can point anywhere. `localtest.me` is a real, delegated
    domain whose entire purpose is resolving to 127.0.0.1, so it is a genuine
    public-name-private-target case rather than a synthetic one.

    Skipped rather than failed when the resolver cannot answer: this asserts our
    logic, not the network's availability.
    """
    import socket

    try:
        socket.getaddrinfo("localtest.me", 443)
    except OSError:
        print("  (skipped: localtest.me did not resolve)")
        return
    message = _expect_error(validate_remote_url, "https://localtest.me/mcp")
    assert "non-public" in message.lower() or "does not resolve" in message.lower()


def test_plain_http_is_refused_by_default() -> None:
    _expect_error(validate_remote_url, "http://example.com/mcp", contains="https")


def test_non_http_schemes_are_refused() -> None:
    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"):
        _expect_error(validate_remote_url, url)


def test_a_missing_scheme_is_named_rather_than_guessed() -> None:
    _expect_error(validate_remote_url, "example.com/mcp", contains="scheme")


def test_a_public_https_url_is_accepted() -> None:
    import socket

    try:
        socket.getaddrinfo("example.com", 443)
    except OSError:
        print("  (skipped: no resolver)")
        return
    assert validate_remote_url("https://example.com/mcp") == "https://example.com/mcp"


def test_the_insecure_http_escape_hatch_is_dev_only_and_explicit() -> None:
    """MCP_ALLOW_INSECURE_HTTP is the ONLY way a private target is allowed.

    It exists so a developer can point at a server on their own machine. This test
    pins that it is off by default and that turning it on is what changes the
    answer — so the private-range checks can never be skipped by accident.
    """
    original = settings.mcp_allow_insecure_http
    assert original is False, "MCP_ALLOW_INSECURE_HTTP must default to off"
    try:
        settings.mcp_allow_insecure_http = True
        assert validate_remote_url("http://localhost:9000/mcp")
        assert validate_remote_url("http://127.0.0.1:9000/mcp")
    finally:
        settings.mcp_allow_insecure_http = original
    # And off again, the same URL is refused.
    _expect_error(validate_remote_url, "http://127.0.0.1:9000/mcp")


# ── the stdio gate ────────────────────────────────────────────────────────────


def test_stdio_is_refused_unless_explicitly_enabled() -> None:
    """A stdio server is a command this backend spawns.

    Off by default because registering one is equivalent to shell access to the
    container holding every provider key. The refusal names why, so an operator
    turning it on is making an informed choice.
    """
    assert settings.mcp_allow_stdio is False, "MCP_ALLOW_STDIO must default to off"
    message = _expect_error(
        validate_config, {"command": "sh", "args": ["-c", "env"]}
    )
    assert "disabled" in message.lower()
    assert "environment" in message.lower() or "api key" in message.lower()


def test_stdio_is_accepted_when_enabled() -> None:
    original = settings.mcp_allow_stdio
    try:
        settings.mcp_allow_stdio = True
        config = validate_config(
            {"command": "uvx", "args": ["some-server"], "env": {"K": "v"}}
        )
        assert config == {"command": "uvx", "args": ["some-server"], "env": {"K": "v"}}
        assert is_stdio(config) and transport_of(config) == "stdio"
    finally:
        settings.mcp_allow_stdio = original


def test_stdio_env_values_are_not_returned_to_the_client() -> None:
    """A stdio `env` is where an API key for the child process goes.

    redact_config returns the key NAMES so the UI can show what is configured,
    and never the values.
    """
    redacted = redact_config(
        {"command": "uvx", "args": ["s"], "env": {"TOKEN": "super-secret"}}
    )
    assert redacted["envKeys"] == ["TOKEN"]
    assert "super-secret" not in repr(redacted)


# ── headers ───────────────────────────────────────────────────────────────────


def test_header_injection_via_newlines_is_refused() -> None:
    _expect_error(
        validate_headers, {"X-Api-Key": "abc\r\nX-Admin: true"}
    )
    _expect_error(validate_headers, {"X-Bad\r\nInjected": "v"})


def test_transport_owned_headers_are_refused() -> None:
    """Pinning these produces failures that look like server bugs."""
    for name in ("Host", "Content-Type", "Mcp-Session-Id", "Content-Length"):
        _expect_error(validate_headers, {name: "x"}, contains="cannot be set")


def test_an_api_key_header_is_accepted() -> None:
    assert validate_headers({"X-Api-Key": " secret "}) == {"X-Api-Key": "secret"}
    # Authorization is NOT reserved: a static bearer token is a legitimate
    # header-auth credential and is exactly what this mode is for.
    assert validate_headers({"Authorization": "Bearer t"}) == {"Authorization": "Bearer t"}


# ── names + shape discrimination ──────────────────────────────────────────────


def test_names_are_constrained_to_what_survives_a_tool_id() -> None:
    assert validate_name("  GitHub Tools ") == "GitHub Tools"
    for bad in ("", " ", "-leading", "a" * 49, "sql;drop", "emoji🙂"):
        _expect_error(validate_name, bad)


def test_config_shape_discriminates_remote_from_stdio() -> None:
    assert is_remote({"url": "https://x.example"})
    assert not is_remote({"command": "sh"})
    assert is_stdio({"command": "sh"})
    assert not is_stdio({"url": "https://x.example"})
    _expect_error(validate_config, {"neither": 1}, contains="url")


def test_headers_never_survive_into_the_stored_config() -> None:
    """The config column is plain JSON; secrets go to the encrypted column.

    A client that sends headers inline must not have them persisted in the clear.
    """
    import socket

    try:
        socket.getaddrinfo("example.com", 443)
    except OSError:
        print("  (skipped: no resolver)")
        return
    config = validate_config(
        {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer leak"}}
    )
    assert "headers" not in config
    assert "leak" not in repr(config)


def test_sse_transport_is_preserved_and_defaults_to_http() -> None:
    import socket

    try:
        socket.getaddrinfo("example.com", 443)
    except OSError:
        print("  (skipped: no resolver)")
        return
    assert validate_config({"url": "https://example.com/sse", "transport": "sse"})[
        "transport"
    ] == "sse"
    assert validate_config({"url": "https://example.com/mcp"})["transport"] == "http"


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
