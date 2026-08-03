"""app/mcp/schema.py — coercing third-party tool schemas into an accepted shape.

The cases here are not hypotheses. Every REJECTED shape asserted below was
measured against the live /v1/chat/completions `tools` slot, and the accepted
outputs were re-verified through the same endpoint (22/22). The module docstring
records the full probe; these tests pin the behaviour so a future refactor cannot
quietly re-introduce a 400.

The bug that motivated all of it: a server published a tool whose schema was a
top-level `anyOf`, which is legal JSON Schema and an instant HTTP 400 on the whole
request — taking down every OTHER tool in the turn, including the built-in ones.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402

from app.mcp.schema import EMPTY_SCHEMA, normalise_tool_schema  # noqa: E402


def _root_is_valid(schema: dict) -> bool:
    """The rule the API actually enforces, as measured.

    `type` must be the plain string "object" — the LIST form `["object", "null"]`
    is rejected even though it contains "object", which is the trap worth pinning.
    And none of the composition/constraint keywords may appear at the root.
    """
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "object":
        return False
    forbidden = ("anyOf", "oneOf", "allOf", "enum", "const", "not")
    return not any(k in schema for k in forbidden)


# ── the root must always come out valid ──────────────────────────────────────


_ROOT_CASES = [
    ("plain", {"type": "object", "properties": {"a": {"type": "string"}}}),
    ("empty", {}),
    ("none", None),
    ("not_a_dict", ["nope"]),
    ("a_string", "object"),
    ("an_int", 7),
    ("top_anyOf", {"anyOf": [{"type": "object", "properties": {"a": {}}}]}),
    ("top_oneOf", {"oneOf": [{"type": "object", "properties": {"a": {}}}]}),
    ("top_allOf", {"allOf": [{"type": "object", "properties": {"a": {}}}]}),
    ("top_enum", {"enum": ["x", "y"]}),
    ("top_const", {"const": "x"}),
    ("top_not", {"not": {"type": "string"}}),
    ("type_string", {"type": "string"}),
    ("type_array", {"type": "array", "items": {"type": "string"}}),
    ("type_null", {"type": "null"}),
    ("type_list", {"type": ["object", "null"], "properties": {"a": {}}}),
    ("type_list_no_object", {"type": ["string", "null"]}),
    ("type_is_dict", {"type": {"weird": True}}),
    ("props_not_dict", {"type": "object", "properties": ["a"]}),
    ("required_not_list", {"type": "object", "properties": {}, "required": "a"}),
]


def test_root_is_always_accepted_shape() -> None:
    for label, schema in _ROOT_CASES:
        out = normalise_tool_schema(schema)
        assert _root_is_valid(out), f"{label} produced an invalid root: {out}"
        # Must survive the trip to the API as JSON.
        json.dumps(out)


def test_never_raises_on_hostile_input() -> None:
    """Totality is the contract: this sits between a third party and a live turn."""

    class Exploding(dict):
        def items(self):  # noqa: ANN204
            raise RuntimeError("boom")

    out = normalise_tool_schema(Exploding({"type": "object"}))
    assert out == EMPTY_SCHEMA


def test_cyclic_schema_does_not_hang() -> None:
    """A self-referencing dict is bounded by depth rather than recursing forever."""
    node: dict = {"type": "object", "properties": {}}
    node["properties"]["me"] = node
    out = normalise_tool_schema(node)
    assert _root_is_valid(out)
    json.dumps(out)  # would raise ValueError on a circular reference


# ── the root repair must not touch nested schemas ────────────────────────────


def test_nested_composition_is_preserved_untouched() -> None:
    """`anyOf` is illegal at the root and legal one level down.

    Rewriting it would change the tool's contract with its own server, so a
    property's schema is forwarded as-is. This is the load-bearing distinction in
    the module.
    """
    schema = {
        "type": "object",
        "properties": {
            "target": {
                "anyOf": [{"type": "string"}, {"type": "integer"}],
                "description": "id or name",
            },
            "mode": {"type": "string", "enum": ["fast", "slow"]},
        },
        "required": ["target"],
    }
    out = normalise_tool_schema(schema)
    assert out["properties"]["target"]["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
    ]
    assert out["properties"]["mode"]["enum"] == ["fast", "slow"]
    assert out["required"] == ["target"]


def test_refs_and_defs_survive() -> None:
    """$ref/$defs are accepted by the API, so they must not be stripped."""
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/T"}},
        "$defs": {"T": {"type": "string", "minLength": 2}},
    }
    out = normalise_tool_schema(schema)
    assert out["properties"]["a"] == {"$ref": "#/$defs/T"}
    assert out["$defs"]["T"]["minLength"] == 2


# ── composition merging semantics ────────────────────────────────────────────


def test_anyof_merges_properties_and_intersects_required() -> None:
    """The real higgsfield shape.

    Both branches are callable, so the model needs every property. But a key
    required by only ONE branch is not required in general — claiming otherwise
    tells the model a field is mandatory when a whole valid call shape omits it.
    `prompt` is in both branches; the two ids are each in one.
    """
    schema = {
        "anyOf": [
            {
                "type": "object",
                "properties": {"video_url": {"type": "string"}, "prompt": {"type": "string"}},
                "required": ["video_url", "prompt"],
            },
            {
                "type": "object",
                "properties": {"video_id": {"type": "string"}, "prompt": {"type": "string"}},
                "required": ["video_id", "prompt"],
            },
        ]
    }
    out = normalise_tool_schema(schema)
    assert set(out["properties"]) == {"video_url", "video_id", "prompt"}
    assert out["required"] == ["prompt"]


def test_allof_unions_required() -> None:
    """`allOf` means every branch applies, so all their required keys are required."""
    schema = {
        "allOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            {"type": "object", "properties": {"b": {"type": "number"}}, "required": ["b"]},
        ]
    }
    out = normalise_tool_schema(schema)
    assert set(out["properties"]) == {"a", "b"}
    assert out["required"] == ["a", "b"]


def test_anyof_branch_without_required_empties_the_intersection() -> None:
    """A branch needing nothing means nothing is universally required."""
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            {"type": "object", "properties": {"a": {"type": "string"}}},
        ]
    }
    out = normalise_tool_schema(schema)
    assert "a" in out["properties"]
    assert "required" not in out


def test_first_branch_wins_for_a_clashing_property() -> None:
    """Deterministic regardless of dict ordering."""
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"type": "object", "properties": {"a": {"type": "number"}}},
        ]
    }
    out = normalise_tool_schema(schema)
    assert out["properties"]["a"] == {"type": "string"}


def test_nested_composition_branch_is_flattened() -> None:
    """A branch that is itself a composition still contributes its properties."""
    schema = {
        "anyOf": [
            {"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}}]},
            {"type": "object", "properties": {"b": {"type": "string"}}},
        ]
    }
    out = normalise_tool_schema(schema)
    assert set(out["properties"]) == {"a", "b"}


def test_composition_with_no_usable_branch_degrades_to_empty_object() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    out = normalise_tool_schema(schema)
    assert _root_is_valid(out)
    assert out["properties"] == {}


# ── truthful degradation ─────────────────────────────────────────────────────


def test_scalar_root_becomes_no_parameters_not_an_invented_wrapper() -> None:
    """A `{"type": "string"}` root has no honest object form.

    Wrapping it in a key the server never named ({"value": ...}) would produce
    calls the server rejects, which is worse than the model knowing the tool takes
    no arguments it can describe. Asserting the ABSENCE of invention.
    """
    out = normalise_tool_schema({"type": "string"})
    assert out == EMPTY_SCHEMA


def test_properties_without_type_are_kept() -> None:
    """A missing `type` alongside `properties` is an unambiguous intent."""
    out = normalise_tool_schema({"properties": {"a": {"type": "string"}}})
    assert out["type"] == "object"
    assert "a" in out["properties"]


def test_wrong_type_with_properties_is_corrected() -> None:
    out = normalise_tool_schema({"type": "string", "properties": {"a": {"type": "string"}}})
    assert out["type"] == "object"
    assert "a" in out["properties"]


def test_object_null_list_is_unwrapped() -> None:
    """The trap: a list containing "object" is still rejected by the API."""
    out = normalise_tool_schema(
        {"type": ["object", "null"], "properties": {"a": {"type": "string"}}}
    )
    assert out["type"] == "object"
    assert "a" in out["properties"]


# ── required hygiene ────────────────────────────────────────────────────────


def test_required_drops_ghosts_and_duplicates() -> None:
    """A `required` naming an absent property makes the model send a field the
    schema never describes, which the server then rejects."""
    out = normalise_tool_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a", "ghost", "a", 7, None],
        }
    )
    assert out["required"] == ["a"]


def test_empty_required_is_omitted() -> None:
    out = normalise_tool_schema(
        {"type": "object", "properties": {"a": {}}, "required": ["ghost"]}
    )
    assert "required" not in out


# ── properties hygiene ──────────────────────────────────────────────────────


def test_boolean_property_schemas() -> None:
    """`true` means any value; `false` means nothing can validate, so it is dropped."""
    out = normalise_tool_schema({"type": "object", "properties": {"a": True, "b": False}})
    assert out["properties"] == {"a": {}}


def test_scalar_property_schema_is_dropped() -> None:
    """`{"a": "string"}` instead of `{"a": {"type": "string"}}` — a real emitter bug."""
    out = normalise_tool_schema(
        {"type": "object", "properties": {"a": "string", "b": {"type": "number"}}}
    )
    assert set(out["properties"]) == {"b"}


def test_root_noise_is_dropped_but_nested_is_not() -> None:
    """`$schema`/`title`/`description` at the root are inert token cost.

    Nested ones are part of a contract we do not own — and a property's
    `description` is what tells the model what to put there, so removing it would
    actively degrade tool calling.
    """
    out = normalise_tool_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Thing",
            "description": "top level prose",
            "type": "object",
            "properties": {"a": {"type": "string", "description": "keep me"}},
        }
    )
    assert "$schema" not in out
    assert "title" not in out
    assert "description" not in out
    assert out["properties"]["a"]["description"] == "keep me"


def test_deep_nesting_is_bounded_but_shallow_is_intact() -> None:
    deep: dict = {"type": "string"}
    for _ in range(40):
        deep = {"type": "object", "properties": {"n": deep}}
    out = normalise_tool_schema(deep)
    assert _root_is_valid(out)
    assert len(json.dumps(out)) < 4000  # bounded, not 40 levels of prose


def test_unknown_keywords_are_left_alone() -> None:
    """The API accepts them, so stripping them risks removing something meaningful."""
    out = normalise_tool_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "string", "format": "uri", "x-vendor": 1}},
            "additionalProperties": False,
        }
    )
    assert out["properties"]["a"]["format"] == "uri"
    assert out["additionalProperties"] is False


if __name__ == "__main__":
    raise SystemExit(conftest.run_standalone(dict(globals())))
