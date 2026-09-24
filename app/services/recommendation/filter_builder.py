"""Turns user-friendly filters into Pinecone metadata filters.

Accepted forms per field (the field must be one of the tenant's `filter_fields`):

    "location": "Delhi"                      -> {"location": {"$eq": "Delhi"}}
    "cuisine": ["Thai", "Indian"]            -> {"cuisine": {"$in": ["Thai", "Indian"]}}
    "experience_years": {"gte": 3, "lt": 8}  -> {"experience_years": {"$gte": 3, "$lt": 8}}

Operators may be written with or without the `$`: eq, ne, gt, gte, lt, lte, in, nin.
"""

from typing import Any

from app.core.exceptions import BadRequestError

_RANGE_OPS = {"gt", "gte", "lt", "lte"}
_SCALAR_OPS = {"eq", "ne"}
_LIST_OPS = {"in", "nin"}
_ALL_OPS = _RANGE_OPS | _SCALAR_OPS | _LIST_OPS

Scalar = str | int | float | bool


def _is_scalar(value: Any) -> bool:
    return isinstance(value, Scalar)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class FilterBuilder:
    def build_pinecone_filter(
        self, filters_input: dict[str, Any] | None, domain_config: dict[str, Any]
    ) -> dict[str, Any]:
        if not filters_input:
            return {}
        allowed: list[str] = domain_config.get("filter_fields") or []
        errors: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        for field, value in filters_input.items():
            if field not in allowed:
                errors.append(
                    {
                        "field": field,
                        "message": f"Not a filter field. Allowed: {', '.join(allowed) or 'none'}",
                        "type": "unknown_filter",
                    }
                )
                continue
            try:
                result[field] = self._condition(value)
            except ValueError as exc:
                errors.append({"field": field, "message": str(exc), "type": "invalid_filter"})
        if errors:
            raise BadRequestError("Invalid filters", details=errors)
        return result

    def _condition(self, value: Any) -> dict[str, Any]:
        if isinstance(value, list):
            return {"$in": self._scalar_list(value)}
        if isinstance(value, dict):
            return self._operators(value)
        if _is_scalar(value):
            return {"$eq": value}
        raise ValueError("Use a value, a list of values, or an operator object like {'gte': 3}")

    def _operators(self, spec: dict[str, Any]) -> dict[str, Any]:
        if not spec:
            raise ValueError("Operator object is empty")
        condition: dict[str, Any] = {}
        for raw_op, operand in spec.items():
            op = raw_op.removeprefix("$")
            if op not in _ALL_OPS:
                raise ValueError(
                    f"Unknown operator '{raw_op}'. Use one of: {', '.join(sorted(_ALL_OPS))}"
                )
            if op in _RANGE_OPS and not _is_number(operand):
                raise ValueError(f"'{op}' needs a number, got {operand!r}")
            if op in _SCALAR_OPS and not _is_scalar(operand):
                raise ValueError(f"'{op}' needs a single value, got {operand!r}")
            if op in _LIST_OPS:
                if not isinstance(operand, list):
                    raise ValueError(f"'{op}' needs a list, got {operand!r}")
                operand = self._scalar_list(operand)
            condition[f"${op}"] = operand

        low = max((condition[k] for k in ("$gt", "$gte") if k in condition), default=None)
        high = min((condition[k] for k in ("$lt", "$lte") if k in condition), default=None)
        if low is not None and high is not None and low > high:
            raise ValueError(f"Empty range: lower bound {low} is above upper bound {high}")
        return condition

    @staticmethod
    def _scalar_list(values: list[Any]) -> list[Any]:
        if not values:
            raise ValueError("List must not be empty")
        if not all(_is_scalar(v) for v in values):
            raise ValueError("List values must be strings, numbers or booleans")
        return values
