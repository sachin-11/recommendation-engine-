"""Turns an item's raw fields into the text we embed, driven by the tenant's domain config.

The engine never knows what a "job" or a "dish" is; the domain config names the
fields that matter, which is what keeps it domain-agnostic.
"""

import json
from typing import Any


class TextBuilder:
    def build_embedding_text(self, item_data: dict[str, Any], domain_config: dict[str, Any]) -> str:
        """Primary field first, then the other searchable fields, each as `field: value`.

        Example: "description: Senior Python Developer... skills: Python FastAPI title: Backend
        Engineer". Missing or empty fields are skipped; an empty result means the item has
        nothing to embed.
        """
        primary: str | None = domain_config.get("primary_embedding_field")
        searchable: list[str] = domain_config.get("searchable_fields") or []
        ordered = [primary, *(f for f in searchable if f != primary)] if primary else searchable

        parts: list[str] = []
        for field in ordered:
            value = self._stringify(item_data.get(field))
            if value:
                parts.append(f"{field}: {value}")
        return " ".join(parts)

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return " ".join(value.split())
        if isinstance(value, list | tuple | set):
            return " ".join(filter(None, (TextBuilder._stringify(v) for v in value)))
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)
