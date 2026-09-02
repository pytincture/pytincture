import pytest

from pytincture.backend.bff_requests import (
    BFFArguments,
    BFFRequestValidationError,
    parse_canonical_bff_body,
    validate_bff_arguments,
)


def parse(body: str, **overrides):
    return parse_canonical_bff_body(
        body.encode("utf-8"),
        max_bytes=overrides.get("max_bytes", 4096),
        max_depth=overrides.get("max_depth", 8),
        max_items=overrides.get("max_items", 100),
    )


def test_canonical_bff_body_has_one_json_representation():
    assert parse('{"args":[1],"kwargs":{"label":"ok"}}') == BFFArguments(
        (1,), {"label": "ok"}
    )
    for alias in (
        '"{\\"args\\":[],\\"kwargs\\":{}}"',
        "{}",
        '{"label":"legacy bare kwargs"}',
        '{"args":[]}',
        '{"kwargs":{}}',
        '[]',
    ):
        with pytest.raises(BFFRequestValidationError):
            parse(alias)


@pytest.mark.parametrize(
    "body",
    (
        '{"args":[],"args":[],"kwargs":{}}',
        '{"args":[],"kwargs":{"value":1,"value":2}}',
        '{"args":[NaN],"kwargs":{}}',
        '{"args":[Infinity],"kwargs":{}}',
        '{"args":[-Infinity],"kwargs":{}}',
    ),
)
def test_canonical_bff_body_rejects_duplicate_keys_and_non_finite_numbers(body):
    with pytest.raises(BFFRequestValidationError):
        parse(body)


def test_canonical_bff_body_enforces_byte_depth_and_item_limits():
    with pytest.raises(BFFRequestValidationError, match="too large"):
        parse('{"args":[],"kwargs":{}}', max_bytes=5)
    with pytest.raises(BFFRequestValidationError, match="nesting"):
        parse('{"args":[[[[1]]]],"kwargs":{}}', max_depth=4)
    with pytest.raises(BFFRequestValidationError, match="item"):
        parse('{"args":[1,2,3],"kwargs":{}}', max_items=4)


def test_static_signature_binding_validates_shape_and_common_annotations():
    parameters = (
        {
            "name": "count",
            "kind": "positional_or_keyword",
            "required": True,
            "annotation": "int",
        },
        {
            "name": "labels",
            "kind": "keyword_only",
            "required": True,
            "annotation": "list[str]",
        },
        {
            "name": "enabled",
            "kind": "keyword_only",
            "required": False,
            "annotation": "bool | None",
        },
    )
    validate_bff_arguments(
        BFFArguments((2,), {"labels": ["a", "b"], "enabled": None}),
        parameters,
    )
    invalid = (
        BFFArguments((), {"labels": ["a"]}),
        BFFArguments((True,), {"labels": ["a"]}),
        BFFArguments((2,), {"labels": [1]}),
        BFFArguments((2,), {"labels": ["a"], "unknown": 1}),
        BFFArguments((2,), {"count": 3, "labels": ["a"]}),
    )
    for arguments in invalid:
        with pytest.raises(BFFRequestValidationError):
            validate_bff_arguments(arguments, parameters)


def test_static_signature_binding_supports_varargs_kwargs_and_positional_only():
    parameters = (
        {
            "name": "first",
            "kind": "positional_only",
            "required": True,
            "annotation": "str",
        },
        {
            "name": "values",
            "kind": "var_positional",
            "required": False,
            "annotation": "int",
        },
        {
            "name": "options",
            "kind": "var_keyword",
            "required": False,
            "annotation": "bool",
        },
    )
    validate_bff_arguments(
        BFFArguments(("first", 1, 2), {"cached": True}),
        parameters,
    )
    with pytest.raises(BFFRequestValidationError, match="positional-only"):
        validate_bff_arguments(BFFArguments((), {"first": "bad"}), parameters)
    with pytest.raises(BFFRequestValidationError, match="wrong type"):
        validate_bff_arguments(
            BFFArguments(("first", "not-int"), {}),
            parameters,
        )


@pytest.mark.parametrize(
    ("annotation", "accepted", "rejected"),
    (
        ("Literal[True]", True, (1, 1.0, False, None, "true")),
        ("Literal[1]", 1, (True, 1.0, 0, None, "1")),
        ("Literal[1.0]", 1.0, (True, 1, 0.0, None, "1.0")),
        ('Literal["1"]', "1", (True, 1, 1.0, None, "one")),
        ("Literal[None]", None, (False, 0, 0.0, "", "none")),
    ),
)
def test_literal_validation_requires_exact_runtime_type(
    annotation,
    accepted,
    rejected,
):
    parameters = (
        {
            "name": "selector",
            "kind": "positional_or_keyword",
            "required": True,
            "annotation": annotation,
        },
    )
    validate_bff_arguments(BFFArguments((accepted,), {}), parameters)
    for value in rejected:
        with pytest.raises(BFFRequestValidationError, match="wrong type"):
            validate_bff_arguments(BFFArguments((value,), {}), parameters)


@pytest.mark.parametrize("value", (True, 1, 1.0, "1", None))
def test_literal_validation_accepts_each_exact_option_in_a_mixed_literal(value):
    parameters = (
        {
            "name": "selector",
            "kind": "positional_or_keyword",
            "required": True,
            "annotation": 'Literal[True, 1, 1.0, "1", None]',
        },
    )
    validate_bff_arguments(BFFArguments((value,), {}), parameters)
