"""Coercing a third-party MCP tool schema into one a tool-calling API accepts.

WHY THIS MODULE EXISTS. An MCP server publishes `inputSchema` as ordinary JSON
Schema, and JSON Schema permits a great deal that the OpenAI and Deepgram tool
parameter slots do not. The mismatch is not theoretical — a real server shipped a
tool whose schema was a top-level `anyOf`, and it produced:

    Invalid schema for function 'video_analysis_create': schema must have type
    'object' and not have 'oneOf'/'anyOf'/'allOf'/'enum'/'const'/'not' at the
    top level.

That is an HTTP 400 on the WHOLE request, so ONE malformed tool from ONE server
took down every tool in the turn, including the built-in ones. The same server
works in ChatGPT and Claude because those clients normalise before sending. So
does this.

WHAT THE API ACTUALLY ACCEPTS, measured against /v1/chat/completions rather than
inferred from the docs (probe: 20 shapes, one tool per request):

    ACCEPTED  {"type": "object", ...}          the only accepted root
    ACCEPTED  {}  and  null                    treated as "no parameters"
    ACCEPTED  nested anyOf / oneOf / enum / const / not, at any depth
    ACCEPTED  $ref + $defs, and even external $ref URIs
    ACCEPTED  unknown keywords ($schema, title, x-vendor, examples, format)
    ACCEPTED  12 levels of nesting
    REJECTED  anyOf / oneOf / allOf / enum / const / not at the ROOT
    REJECTED  {"type": "string"} / {"type": "array"} at the root
    REJECTED  {"type": ["object", "null"]}      ← a LIST fails even containing "object"

Two consequences shape everything below.

FIRST, ONLY THE ROOT IS EVER REWRITTEN. Every construct that is illegal at the
root is legal one level down, so nested schemas are copied through untouched.
This is a correctness requirement, not thrift: a tool's nested schema is its
contract with its own server, and rewriting it would change what arguments the
model is told to send. We repair the envelope and leave the meaning alone.

SECOND, `type` MUST BE THE PLAIN STRING "object". The list form is the trap — it
reads as valid and is not, so a nullable-object schema from a server that emitted
`["object", "null"]` fails in a way no amount of reading the error message
explains.

WHAT IS NOT HERE. No strict-mode / structured-outputs conversion (no forcing
`additionalProperties: false`, no requiring every key). Tools are sent in
ordinary mode, where a permissive schema is fine and an over-tightened one breaks
real calls by rejecting arguments the server would have accepted.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("errand.mcp.schema")

# Keywords that are legal JSON Schema but illegal at the root of a tool's
# parameters. Kept as data because they are also exactly what we look for when
# deciding whether a root schema is a composition that can be merged.
_COMPOSITION_KEYS = ("allOf", "anyOf", "oneOf")
_ROOT_FORBIDDEN = _COMPOSITION_KEYS + ("enum", "const", "not")

# Dropped from the root only: inert for tool calling and pure input-token cost on
# every request of every turn. Left in place when nested, since a `$schema` inside
# a property is part of a contract we do not own.
_ROOT_NOISE = ("$schema", "title", "description", "examples", "example", "default")

# A schema nested deeper than this is almost certainly a cycle expanded by a
# generator. The API accepted 12 levels, but the reason to bound it is cost, not
# validity: schema text is re-sent on every request of the tool loop.
_MAX_DEPTH = 10

EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

# How many branch names to name in a constraint note before giving up on prose.
_MAX_NAMED_BRANCHES = 6


def normalise_tool_schema(schema: Any) -> dict[str, Any]:
    """Return a schema the tool parameter slot will accept. Never raises.

    Total by construction: every input, including one that is not JSON Schema at
    all, maps to *some* accepted object schema. That totality is the point — this
    sits between an untrusted third-party server and a request that carries the
    user's whole turn, so "I could not understand this schema" has to degrade to
    "this tool takes no arguments I can describe", never to an exception and never
    to a 400.

    Note this is the opposite bias from app/mcp/config.validate_remote_url, which
    fails CLOSED on anything it cannot resolve. That one is a security check on a
    destination we are about to reach; this is a compatibility shim on a
    description we are about to forward. Different question, different default.
    """
    try:
        return _normalise_root(schema)
    except Exception:  # noqa: BLE001 — a shim must not be the thing that breaks
        logger.warning("Could not normalise an MCP tool schema; sending no parameters", exc_info=True)
        return dict(EMPTY_SCHEMA)


def _normalise_root(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        # `null`, `{}`, a list, a string. All mean "nothing I can describe".
        return dict(EMPTY_SCHEMA)

    root = {k: v for k, v in schema.items() if k not in _ROOT_NOISE}

    # A root composition (`anyOf`/`oneOf`/`allOf`) is flattened into one object
    # schema. Order matters: this must happen BEFORE the `type` check, because a
    # composition legitimately carries no `type` of its own.
    if any(k in root for k in _COMPOSITION_KEYS):
        root = _flatten_composition(root)

    declared = root.get("type")

    # The list form. `["object", "null"]` is the common emitter output and is
    # REJECTED despite containing "object", so it is unwrapped rather than trusted.
    if isinstance(declared, list):
        declared = "object" if "object" in declared else (
            next((t for t in declared if isinstance(t, str)), None)
        )

    if declared != "object":
        if isinstance(root.get("properties"), dict):
            # Properties but a wrong or missing `type`: the intent is unambiguous,
            # so correct the envelope and keep the body.
            declared = "object"
        else:
            # A genuinely non-object root — `{"type": "string"}`, a bare `enum`.
            # There is no honest object form of "this tool takes one string", and
            # inventing a wrapper key the server never named would produce calls
            # it rejects. Describing no parameters is the truthful degradation.
            return dict(EMPTY_SCHEMA)

    root["type"] = "object"

    # Anything still forbidden at the root is dropped. Reached when a root carried
    # both `properties` and a stray `enum`/`const`/`not`, which some generators
    # emit; the properties are the usable part.
    for key in _ROOT_FORBIDDEN:
        root.pop(key, None)

    props = root.get("properties")
    root["properties"] = _clean_properties(props) if isinstance(props, dict) else {}
    root["required"] = _clean_required(root.get("required"), root["properties"])
    if not root["required"]:
        # An empty `required: []` is legal but noise on every request.
        root.pop("required")
    return root


def describe_constraints(schema: Any) -> str:
    """A sentence naming a root constraint the flattened schema cannot express.

    WHY THIS IS NEEDED, and it is not cosmetic. Merging a root `oneOf` into one
    object gives the model every callable argument, but it ERASES the relationship
    between them: two mutually exclusive fields come out looking like two ordinary
    optional ones. The model is then free to send both, or neither, and the server
    rejects the call — a failure that reads as "this tool is broken" when the
    schema was merely flattened.

    Reference clients solve it the same way. The live higgsfield
    `video_analysis_create`, which is what surfaced this whole bug, arrives from
    Claude's own MCP harness with a plain object schema and a description opening:

        "Input constraint: Provide parameters for exactly one of:
         (video_input_id) or (youtube_url)."

    So the constraint moves from the schema, where it cannot survive, into the
    description, where the model will actually read it. The wording here follows
    that observed format rather than inventing a new one.

    Returns "" when there is nothing to say, so callers can concatenate blindly.
    """
    try:
        return _describe(schema)
    except Exception:  # noqa: BLE001 — a note is never worth failing a turn for
        logger.warning("Could not describe an MCP tool's schema constraints", exc_info=True)
        return ""


def _describe(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""

    for key, phrase in (("oneOf", "exactly one of"), ("anyOf", "at least one of")):
        branches = schema.get(key)
        if not isinstance(branches, list) or len(branches) < 2:
            continue
        groups: list[str] = []
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            props = branch.get("properties")
            if not isinstance(props, dict) or not props:
                continue
            # Prefer the branch's REQUIRED keys: those are what identify the shape.
            # A branch's optional extras are shared noise and naming them makes the
            # groups look identical.
            required = [
                r for r in (branch.get("required") or []) if isinstance(r, str) and r in props
            ]
            names = required or [n for n in props if isinstance(n, str)]
            if names:
                groups.append(f"({', '.join(names)})")
        # Distinct, order-preserving. Identical branches say nothing.
        unique = list(dict.fromkeys(groups))
        if len(unique) < 2 or len(unique) > _MAX_NAMED_BRANCHES:
            continue
        return f"Input constraint: Provide parameters for {phrase}: {' or '.join(unique)}."

    return ""


def _flatten_composition(root: dict[str, Any]) -> dict[str, Any]:
    """Merge a root `allOf`/`anyOf`/`oneOf` into a single object schema.

    Merging rather than picking the first branch, because the model needs every
    parameter the tool can accept — a tool with two argument shapes is not
    callable if half of them are invisible. Since tools are sent in permissive
    mode, a union of properties is safe: the server still validates, and the model
    is told in the description which combinations make sense.

    `required` differs by operator, and this is the part worth being careful about:
      * allOf — every branch applies, so a key required by any branch is required.
        Union.
      * anyOf / oneOf — only one branch need apply, so a key required by just one
        branch is NOT required in general. Intersection. Claiming otherwise would
        make the model believe a field is mandatory when a whole valid call shape
        does not use it.
    """
    merged: dict[str, Any] = {
        k: v for k, v in root.items() if k not in _COMPOSITION_KEYS
    }
    properties: dict[str, Any] = dict(merged.get("properties") or {})
    required_sets: list[set[str]] = []
    saw_branch = False

    for key in _COMPOSITION_KEYS:
        branches = root.get(key)
        if not isinstance(branches, list):
            continue
        intersect = key in ("anyOf", "oneOf")
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            # A branch may itself be a composition; one level of recursion covers
            # the shapes generators actually emit without risking a cycle.
            if any(k in branch for k in _COMPOSITION_KEYS):
                branch = _flatten_composition(branch)
            branch_props = branch.get("properties")
            if isinstance(branch_props, dict):
                saw_branch = True
                for name, sub in branch_props.items():
                    # First branch to define a property wins, so the merge is
                    # deterministic regardless of dict ordering.
                    properties.setdefault(name, sub)
            branch_required = branch.get("required")
            if isinstance(branch_required, list):
                required_sets.append({r for r in branch_required if isinstance(r, str)})
            elif intersect:
                # A branch with no `required` means this shape needs nothing, so
                # the intersection is empty. Recording it explicitly is what makes
                # that true rather than accidentally skipping it.
                required_sets.append(set())

        if saw_branch and intersect and required_sets:
            keep = set.intersection(*required_sets) if required_sets else set()
        elif required_sets:
            keep = set().union(*required_sets)
        else:
            keep = set()
        existing = {r for r in (merged.get("required") or []) if isinstance(r, str)}
        merged["required"] = sorted(existing | keep) if key == "allOf" else sorted(
            (existing & keep) if existing and keep else (existing or keep)
        )
        required_sets = []

    merged["properties"] = properties
    merged["type"] = "object"
    return merged


def _clean_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Copy properties through, bounding depth and dropping unusable entries.

    Deliberately shallow: a property's schema is forwarded as-is because every
    construct that is illegal at the root is legal here, and rewriting it would
    change the tool's contract. The only interventions are a depth bound and
    discarding a property whose schema is not an object at all (a server that
    emitted `{"a": "string"}` instead of `{"a": {"type": "string"}}`), which would
    otherwise reach the API as a malformed member.
    """
    cleaned: dict[str, Any] = {}
    for name, sub in props.items():
        if not isinstance(name, str) or not name:
            continue
        if isinstance(sub, bool):
            # `true`/`false` are valid JSON Schema meaning any/nothing. Express
            # the permissive one and drop the other.
            if sub:
                cleaned[name] = {}
            continue
        if not isinstance(sub, dict):
            continue
        cleaned[name] = _bound_depth(sub, _MAX_DEPTH)
    return cleaned


def _bound_depth(node: Any, budget: int) -> Any:
    """Truncate a schema past `budget` levels, preserving its `type` if it has one."""
    if budget <= 0:
        if isinstance(node, dict):
            kind = node.get("type")
            return {"type": kind} if isinstance(kind, str) else {}
        return {}
    if isinstance(node, dict):
        return {k: _bound_depth(v, budget - 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_bound_depth(v, budget - 1) for v in node]
    return node


def _clean_required(required: Any, properties: dict[str, Any]) -> list[str]:
    """`required` reduced to names that actually exist, de-duplicated, ordered.

    A `required` naming an absent property is accepted by the API today, but it
    tells the model to send a field the schema never describes — which reliably
    produces a call the server rejects. Dropping it is a correctness fix, not
    validation appeasement.
    """
    if not isinstance(required, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for name in required:
        if not isinstance(name, str) or name in seen or name not in properties:
            continue
        seen.add(name)
        out.append(name)
    return out
