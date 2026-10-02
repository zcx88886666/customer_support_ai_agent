from resolveai.openrouter import strict_json_schema
from resolveai.schemas import RouteDecision


def test_route_schema_is_compatible_with_strict_output():
    schema = strict_json_schema(RouteDecision)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["route", "intents", "uncertainty"]
    assert "default" not in schema["properties"]["uncertainty"]
    assert schema["properties"]["uncertainty"]["anyOf"][-1] == {"type": "null"}
