"""Unit tests для робастного JSON-парсера LLM (utils/json_utils)."""
from utils.json_utils import (
    as_dict,
    as_float,
    as_str,
    as_str_list,
    extract_json,
    validate_schema,
)


class TestExtractJson:
    def test_valid_json(self):
        data = extract_json('{"topic": "Игры", "items": [1, 2]}')
        assert data == {"topic": "Игры", "items": [1, 2]}

    def test_markdown_wrapped(self):
        data = extract_json('```json\n{"topic": "Игры"}\n```')
        assert data == {"topic": "Игры"}

    def test_extra_text_around(self):
        data = extract_json('Вот ответ: {"topic": "Игры"} Спасибо!')
        assert data == {"topic": "Игры"}

    def test_invalid_json(self):
        assert extract_json("просто текст без json") is None
        assert extract_json('{"topic": "broken') is None

    def test_empty(self):
        assert extract_json("") is None
        assert extract_json("   ") is None
        assert extract_json(None) is None

    def test_missing_braces(self):
        assert extract_json("нет скобок вообще") is None

    def test_array_json(self):
        data = extract_json('[{"a": 1}, {"a": 2}]')
        assert isinstance(data, list) and len(data) == 2

    def test_no_eval_used(self):
        """Парсер не использует eval/exec — только json.loads."""
        import inspect
        from utils import json_utils as ju
        source = inspect.getsource(ju)
        assert "eval(" not in source.replace("Никогда не использует eval().", "")
        assert "exec(" not in source


class TestSchemaValidation:
    def test_valid_schema(self):
        data = validate_schema({"hook": "X", "body": "Y", "ending": "Z"}, ["hook", "body", "ending"])
        assert data == {"hook": "X", "body": "Y", "ending": "Z"}

    def test_missing_field(self):
        assert validate_schema({"hook": "X"}, ["hook", "body"]) is None

    def test_empty_required(self):
        assert validate_schema({"hook": "", "body": "Y"}, ["hook", "body"]) is None

    def test_not_dict(self):
        assert validate_schema("string", ["hook"]) is None
        assert validate_schema(None, ["hook"]) is None
        assert validate_schema([1, 2], ["hook"]) is None

    def test_wrong_type(self):
        assert validate_schema(
            {"hook": "X", "keywords": "not-a-list"}, ["hook"], {"keywords": list}
        ) is None
        assert validate_schema(
            {"hook": "X", "keywords": ["ok"]}, ["hook"], {"keywords": list}
        ) == {"hook": "X", "keywords": ["ok"]}

    def test_partial_types_ok(self):
        assert validate_schema(
            {"hook": "X", "items": "ignored-type"}, ["hook"], {"items": list}
        ) is None


class TestConverters:
    def test_as_str(self):
        assert as_str("  x  ") == "x"
        assert as_str(42) == "42"
        assert as_str(None) == ""
        assert as_str([1, 2]) == ""
        assert as_str("long text", limit=4) == "long"

    def test_as_str_list(self):
        assert as_str_list(["a", " b ", "c"]) == ["a", "b", "c"]
        assert as_str_list(["a", "", 5, None]) == ["a"]
        assert as_str_list("not list") == []
        assert as_str_list(None) == []

    def test_as_dict(self):
        assert as_dict({"a": 1}) == {"a": 1}
        assert as_dict("x") is None
        assert as_dict([1]) is None

    def test_as_float(self):
        assert as_float("3.5") == 3.5
        assert as_float("bad", default=1.0) == 1.0
        assert as_float("30", lo=0.5, hi=30) == 30
        assert as_float("999", hi=30) == 30
        assert as_float("0.1", lo=0.5) == 0.5