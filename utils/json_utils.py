"""Робастный JSON-парсер для ответов LLM.

Цепочка разбора (см. ТЗ v2.0.1 «robust JSON parser»):
  1. Попытка json.loads напрямую.
  2. Извлечение JSON-блока (первая { ... последняя }) из markdown/мусора.
  3. Валидация схемы (типы, обязательные поля).
  4. Fallback — возврат None (вызывающий код использует эвристику).

Никогда не использует eval().
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)


def extract_json(content: str) -> Any | None:
    """Извлекает JSON из ответа LLM. Возвращает None при неудаче."""
    if not content or not content.strip():
        return None

    # 1. Прямой разбор
    cleaned = _FENCE_RE.sub("", content.strip()).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Извлечение блока { ... }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Массив [ ... ] (некоторые LLM возвращают список)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def as_dict(value: Any) -> dict | None:
    """Приводит значение к dict, либо None."""
    if isinstance(value, dict):
        return value
    return None


def as_str(value: Any, default: str = "", limit: int = 0) -> str:
    """Приводит значение к строке (не список/словарь)."""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (int, float)):
        text = str(value).strip()
    else:
        text = default
    if limit and len(text) > limit:
        text = text[:limit]
    return text


def as_str_list(value: Any) -> list[str]:
    """Приводит значение к списку непустых строк."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def as_float(value: Any, default: float = 0.0, lo: float | None = None,
             hi: float | None = None) -> float:
    """Приводит значение к float с ограничением диапазона."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def validate_schema(
    data: Any,
    required: list[str],
    types: dict[str, type] | None = None,
) -> dict | None:
    """Валидация схемы: данные — dict, обязательные поля на месте и непусты.

    types — опциональное соответствие {поле: (тип или tuple типов)}.
    Возвращает провалидированный dict или None.
    """
    data = as_dict(data)
    if data is None:
        return None
    types = types or {}
    for field in required:
        if field not in data:
            return None
        value = data[field]
        if isinstance(value, str) and not value.strip():
            return None
        if value is None:
            return None
    for field, field_types in types.items():
        if field in data and data[field] is not None:
            if not isinstance(data[field], field_types):
                return None
    return data