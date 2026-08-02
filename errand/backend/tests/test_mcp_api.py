"""The MCP HTTP surface: ownership isolation, secret handling, tool wiring.

The properties pinned here are the ones whose failure is a security bug rather
than a bug:

  * one user cannot see, use, rename or delete another user's server, and the
    denial is a 404 so server ids cannot be probed;
  * no route ever returns a stored secret;
  * a tool id is resolved against the CALLER's own catalogue, so a leaked or
    replayed id cannot reach someone else's server.

Real HTTP through the ASGI app with real tokens the app issued, per the house
style in tests/conftest.py.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402
from conftest import api_client, ensure_schema, register_user, run_async  # noqa: E402

from app.config import settings  # noqa: E402

# A registrable URL that passes validation but is never actually reached: every
# test here either stops before connecting or tolerates the probe failing. Uses a
# real delegated domain so the SSRF resolver check passes.
SERVER_URL = "https://example.com/mcp"


def _resolver_available() -> bool:
    import socket

    try:
        socket.getaddrinfo("example.com", 443)
        return True
    except OSError:
        return False


async def _create(client, auth, *, name: str, **kw):
    """POST a server. `auth` is the bearer headers; `kw` goes into the body.

    Note `auth` rather than `headers`: the body ALSO has a `headers` field (the
    server's credential headers), and one name for both is how the first version of
    this helper collided with itself.
    """
    body = {"name": name, "config": {"url": SERVER_URL}, "auth_mode": "none", **kw}
    return await client.post("/api/mcp/servers", json=body, headers=auth)


def test_a_server_is_registered_and_listed() -> None:
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        async with api_client() as client:
            _, headers = await register_user(client)
            res = await _create(client, headers, name="Docs")
            assert res.status_code == 201, res.text
            created = res.json()
            assert created["name"] == "Docs"
            assert created["transport"] == "http"
            # The probe ran and recorded a verdict; example.com is not an MCP
            # server, so 'error' is the honest outcome. What matters is that the
            # row survived the failed probe rather than the request 500ing.
            assert created["status"] in ("error", "authorizing", "connected")

            listed = await client.get("/api/mcp/servers", headers=headers)
            assert listed.status_code == 200
            assert [s["id"] for s in listed.json()] == [created["id"]]

    run_async(scenario())


def test_authentication_is_required_on_every_route() -> None:
    ensure_schema()

    async def scenario() -> None:
        async with api_client() as client:
            for method, path in (
                ("get", "/api/mcp/servers"),
                ("post", "/api/mcp/servers"),
                ("patch", "/api/mcp/servers/abc"),
                ("delete", "/api/mcp/servers/abc"),
                ("post", "/api/mcp/servers/abc/refresh"),
                ("post", "/api/mcp/servers/abc/authorize"),
                ("post", "/api/mcp/servers/abc/disconnect"),
                ("get", "/api/mcp/servers/abc/authorize/xyz"),
            ):
                res = await getattr(client, method)(
                    path, **({"json": {}} if method in ("post", "patch") else {})
                )
                assert res.status_code in (401, 403), f"{method} {path} -> {res.status_code}"

    run_async(scenario())


def test_another_users_server_is_404_not_403() -> None:
    """The isolation test. 404 rather than 403 so ids cannot be enumerated.

    Every mutating route is checked, not just the read: a rename or a delete that
    skipped the ownership filter would be worse than a leak.
    """
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        async with api_client() as client:
            _, alice = await register_user(client)
            _, bob = await register_user(client)

            created = await _create(client, alice, name="Alice Server")
            assert created.status_code == 201
            server_id = created.json()["id"]

            # Bob sees nothing of Alice's.
            listed = await client.get("/api/mcp/servers", headers=bob)
            assert listed.json() == []

            for method, path, body in (
                ("patch", f"/api/mcp/servers/{server_id}", {"name": "stolen"}),
                ("delete", f"/api/mcp/servers/{server_id}", None),
                ("post", f"/api/mcp/servers/{server_id}/refresh", {}),
                ("post", f"/api/mcp/servers/{server_id}/authorize", {}),
                ("post", f"/api/mcp/servers/{server_id}/disconnect", {}),
                ("get", f"/api/mcp/servers/{server_id}/authorize/whatever", None),
            ):
                kwargs = {"headers": bob}
                if body is not None:
                    kwargs["json"] = body
                res = await getattr(client, method)(path, **kwargs)
                assert res.status_code == 404, f"{method} {path} -> {res.status_code}"

            # Alice's server is untouched by all of that.
            still = await client.get("/api/mcp/servers", headers=alice)
            assert [s["name"] for s in still.json()] == ["Alice Server"]

    run_async(scenario())


def test_secret_headers_are_never_returned() -> None:
    """A stored credential must not come back out of any route.

    The UI needs to know WHICH headers are set, so the names are returned and the
    values are not. Checked against the whole response body, because a leak
    through a nested field is exactly the kind that gets missed.
    """
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return
    # Not shaped like a real credential on purpose — see the note in
    # test_mcp_crypto_tool_id.py. It only has to be a string distinctive enough to
    # search a response body for.
    secret = "FIXTURE-must-not-appear-in-any-response"

    async def scenario() -> None:
        async with api_client() as client:
            _, headers = await register_user(client)
            res = await _create(
                client,
                headers,
                name="Keyed",
                auth_mode="headers",
                headers={"X-Api-Key": secret},
            )
            assert res.status_code == 201, res.text
            assert secret not in res.text
            assert res.json()["header_names"] == ["X-Api-Key"]

            listed = await client.get("/api/mcp/servers", headers=headers)
            assert secret not in listed.text

            server_id = res.json()["id"]
            refreshed = await client.post(
                f"/api/mcp/servers/{server_id}/refresh", headers=headers
            )
            assert secret not in refreshed.text

    run_async(scenario())


def test_header_mode_requires_at_least_one_header() -> None:
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        async with api_client() as client:
            _, headers = await register_user(client)
            res = await _create(client, headers, name="Empty", auth_mode="headers")
            assert res.status_code == 400
            assert "header" in res.json()["detail"].lower()

    run_async(scenario())


def test_a_duplicate_name_for_one_user_is_refused_but_allowed_across_users() -> None:
    """Names are unique PER USER because a name is half of a tool id.

    Two servers sharing a name under one user would produce colliding tool ids and
    the model could not say which it meant. Across users there is no such problem.
    """
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        async with api_client() as client:
            _, alice = await register_user(client)
            _, bob = await register_user(client)
            assert (await _create(client, alice, name="Shared")).status_code == 201
            dup = await _create(client, alice, name="Shared")
            assert dup.status_code == 409
            # Bob may use the same name.
            assert (await _create(client, bob, name="Shared")).status_code == 201

    run_async(scenario())


def test_the_server_limit_is_enforced() -> None:
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        original = settings.mcp_max_servers_per_user
        settings.mcp_max_servers_per_user = 2
        try:
            async with api_client() as client:
                _, headers = await register_user(client)
                assert (await _create(client, headers, name="One")).status_code == 201
                assert (await _create(client, headers, name="Two")).status_code == 201
                third = await _create(client, headers, name="Three")
                assert third.status_code == 400
                assert "up to 2" in third.json()["detail"]
        finally:
            settings.mcp_max_servers_per_user = original

    run_async(scenario())


def test_an_ssrf_url_is_refused_by_the_api_not_just_the_validator() -> None:
    """The guard has to be on the route, not only in the unit under it."""
    ensure_schema()

    async def scenario() -> None:
        async with api_client() as client:
            _, headers = await register_user(client)
            res = await client.post(
                "/api/mcp/servers",
                json={
                    "name": "Metadata",
                    "config": {"url": "https://169.254.169.254/metadata/instance"},
                    "auth_mode": "none",
                },
                headers=headers,
            )
            assert res.status_code == 400
            assert "non-public" in res.json()["detail"].lower()

    run_async(scenario())


def test_a_stdio_config_is_refused_through_the_api_by_default() -> None:
    ensure_schema()

    async def scenario() -> None:
        async with api_client() as client:
            _, headers = await register_user(client)
            res = await client.post(
                "/api/mcp/servers",
                json={
                    "name": "Local",
                    "config": {"command": "sh", "args": ["-c", "env"]},
                    "auth_mode": "none",
                },
                headers=headers,
            )
            assert res.status_code == 400
            assert "disabled" in res.json()["detail"].lower()

    run_async(scenario())


def test_disabling_a_server_removes_its_tools_from_the_catalogue() -> None:
    """The on/off switch has to actually gate what the model is told about.

    The catalogue is seeded directly because reaching a real MCP server from a test
    is not the point — what is being pinned is that `enabled` gates it.
    """
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        from sqlalchemy import select

        from app.mcp import registry
        from app.models import McpServer
        from conftest import session_scope

        async with api_client() as client:
            user, headers = await register_user(client)
            created = await _create(client, headers, name="Toolbox")
            server_id = created.json()["id"]

            async with session_scope() as session:
                row = (
                    await session.scalars(
                        select(McpServer).where(McpServer.id == server_id)
                    )
                ).one()
                row.tools_json = [
                    {
                        "name": "lookup",
                        "description": "Look something up.",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ]
                await session.commit()

            catalogue = await registry.load_catalogue(user["id"])
            assert [t.tool_name for t in catalogue.tools] == ["lookup"]
            assert catalogue.tools[0].tool_id == "mcp__Toolbox__lookup"
            # And it renders for both surfaces.
            assert catalogue.openai_tools()[0]["function"]["name"] == "mcp__Toolbox__lookup"
            assert catalogue.deepgram_functions()[0]["name"] == "mcp__Toolbox__lookup"

            patched = await client.patch(
                f"/api/mcp/servers/{server_id}",
                json={"enabled": False},
                headers=headers,
            )
            assert patched.status_code == 200
            assert patched.json()["enabled"] is False
            assert (await registry.load_catalogue(user["id"])).tools == ()

    run_async(scenario())


def test_a_tool_id_is_resolved_against_the_callers_own_catalogue() -> None:
    """A leaked or replayed tool id must not reach another user's server.

    registry.call_tool re-derives the catalogue for the user it is given, so Alice's
    id is simply unknown to Bob — no call is attempted, and the answer is the same
    "unknown tool" a hallucinated name gets.
    """
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        from sqlalchemy import select

        from app.mcp import registry
        from app.models import McpServer
        from conftest import session_scope

        async with api_client() as client:
            alice, alice_h = await register_user(client)
            bob, _ = await register_user(client)
            created = await _create(client, alice_h, name="Private")
            server_id = created.json()["id"]

            async with session_scope() as session:
                row = (
                    await session.scalars(
                        select(McpServer).where(McpServer.id == server_id)
                    )
                ).one()
                row.tools_json = [
                    {"name": "read", "description": "", "input_schema": {}}
                ]
                await session.commit()

            tool_id = "mcp__Private__read"
            assert (await registry.load_catalogue(alice["id"])).by_id(tool_id) is not None
            assert (await registry.load_catalogue(bob["id"])).by_id(tool_id) is None

            # Bob calling Alice's tool id gets a refusal, not a connection.
            result = await registry.call_tool(bob["id"], tool_id, {})
            assert "unknown tool" in result.lower()

    run_async(scenario())


def test_changing_the_url_drops_a_stored_authorization() -> None:
    """A token minted by one server's IdP is meaningless at another.

    Keeping it would leave the UI claiming an authorization that cannot work.
    """
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        from mcp.shared.auth import OAuthToken

        from app.mcp import storage

        async with api_client() as client:
            _, headers = await register_user(client)
            created = await _create(client, headers, name="Movable")
            server_id = created.json()["id"]

            store = storage.DbTokenStorage(server_id, SERVER_URL)
            await store.set_tokens(OAuthToken(access_token="t", token_type="Bearer"))
            assert await storage.has_tokens(server_id) is True

            patched = await client.patch(
                f"/api/mcp/servers/{server_id}",
                json={"config": {"url": "https://example.org/mcp"}},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text
            assert await storage.has_tokens(server_id) is False

    run_async(scenario())


def test_deleting_a_server_deletes_its_credentials() -> None:
    """A forgotten server must not leave a live refresh token behind."""
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        from mcp.shared.auth import OAuthToken

        from app.mcp import storage

        async with api_client() as client:
            _, headers = await register_user(client)
            created = await _create(client, headers, name="Temporary")
            server_id = created.json()["id"]

            store = storage.DbTokenStorage(server_id, SERVER_URL)
            await store.set_tokens(OAuthToken(access_token="t", token_type="Bearer"))
            assert await storage.has_tokens(server_id) is True

            deleted = await client.delete(
                f"/api/mcp/servers/{server_id}", headers=headers
            )
            assert deleted.status_code == 204
            assert await storage.has_tokens(server_id) is False

    run_async(scenario())


def test_oauth_tokens_survive_a_round_trip_and_are_encrypted_in_the_column() -> None:
    """Consent has to survive a restart, and the token must not be readable."""
    ensure_schema()
    if not _resolver_available():
        print("  (skipped: no resolver)")
        return

    async def scenario() -> None:
        from mcp.shared.auth import OAuthToken
        from sqlalchemy import select

        from app.mcp import storage
        from app.models import McpOAuthSession
        from conftest import session_scope

        async with api_client() as client:
            _, headers = await register_user(client)
            # auth_mode='oauth' explicitly: `authorized` reports whether an OAUTH
            # server holds credentials, so it is false by definition for the other
            # modes and this test would otherwise be asserting the wrong field.
            created = await _create(client, headers, name="Authed", auth_mode="oauth")
            server_id = created.json()["id"]

            store = storage.DbTokenStorage(server_id, SERVER_URL)
            await store.set_tokens(
                OAuthToken(
                    access_token="at-secret-value",
                    token_type="Bearer",
                    refresh_token="rt-secret-value",
                )
            )
            # A fresh storage instance reads it back — i.e. it is persisted, not
            # cached in the object.
            reread = await storage.DbTokenStorage(server_id, SERVER_URL).get_tokens()
            assert reread is not None
            assert reread.access_token == "at-secret-value"
            assert reread.refresh_token == "rt-secret-value"

            async with session_scope() as session:
                row = (
                    await session.scalars(
                        select(McpOAuthSession).where(
                            McpOAuthSession.server_id == server_id
                        )
                    )
                ).one()
                assert "at-secret-value" not in (row.tokens or "")
                assert "rt-secret-value" not in (row.tokens or "")

            # And the API reports it as authorized without leaking the token.
            listed = await client.get("/api/mcp/servers", headers=headers)
            assert "at-secret-value" not in listed.text
            entry = next(s for s in listed.json() if s["id"] == server_id)
            assert entry["authorized"] is True

    run_async(scenario())


def test_config_advertises_mcp_readiness() -> None:
    """The client must be able to tell what this deployment will accept."""
    ensure_schema()

    async def scenario() -> None:
        async with api_client() as client:
            res = await client.get("/api/config")
            assert res.status_code == 200
            mcp = res.json()["mcp"]
            assert mcp["enabled"] is True
            # Off by default: offering the local-command option in the UI on a
            # deployment that refuses it would be a dead control.
            assert mcp["allowStdio"] is False
            assert mcp["canStoreCredentials"] is True
            assert isinstance(mcp["maxServers"], int)

    run_async(scenario())


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
