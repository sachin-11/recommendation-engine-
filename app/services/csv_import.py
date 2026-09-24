"""CSV parsing for item ingestion, with column auto-detection against the domain config."""

import csv
import difflib
import io
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.exceptions import BadRequestError

# Header names accepted for the item id, after normalisation.
EXTERNAL_ID_ALIASES = ("external_id", "externalid", "id", "item_id", "sku")
_DELIMITERS = ",;\t|"


@dataclass
class CsvParseResult:
    items: list[dict[str, Any]]
    column_mapping: dict[str, str]


def normalize_header(header: str) -> str:
    """'Job Title ' -> 'job_title', 'Dietary-Tags' -> 'dietary_tags'."""
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def parse_items_csv(content: bytes, domain_config: dict[str, Any]) -> CsvParseResult:
    text = _decode(content)
    reader = csv.reader(io.StringIO(text), dialect=_sniff(text))
    headers = next(reader, None)
    if not headers or not any(h.strip() for h in headers):
        raise BadRequestError("CSV is empty or has no header row")

    mapping = _map_columns(headers, domain_config)
    items: list[dict[str, Any]] = []
    missing_id_rows: list[int] = []
    for line_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        item = {
            mapping[header]: value.strip()
            for header, value in zip(headers, row, strict=False)
            if header in mapping and value.strip()
        }
        if not item.get("external_id"):
            missing_id_rows.append(line_number)
            continue
        items.append(item)
        if len(items) > settings.MAX_CSV_ITEMS:
            raise BadRequestError(f"CSV has more than {settings.MAX_CSV_ITEMS} rows")

    if missing_id_rows:
        shown = ", ".join(map(str, missing_id_rows[:10]))
        raise BadRequestError(f"{len(missing_id_rows)} row(s) have no external_id (lines {shown})")
    if not items:
        raise BadRequestError("CSV has a header row but no data rows")
    return CsvParseResult(items=items, column_mapping=mapping)


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadRequestError("CSV must be UTF-8 encoded") from exc


def _sniff(text: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=_DELIMITERS)
    except csv.Error:
        return csv.excel


def _map_columns(headers: list[str], domain_config: dict[str, Any]) -> dict[str, str]:
    """Map each CSV header to an item field. Domain fields match case- and
    punctuation-insensitively; other columns are kept under their normalised name."""
    primary: str = domain_config["primary_embedding_field"]
    known = dict.fromkeys(
        [
            primary,
            *domain_config.get("searchable_fields", []),
            *domain_config.get("filter_fields", []),
        ]
    )
    by_normalized = {normalize_header(name): name for name in known}

    mapping: dict[str, str] = {}
    for header in headers:
        norm = normalize_header(header)
        if not norm:
            continue
        if norm in EXTERNAL_ID_ALIASES and "external_id" not in mapping.values():
            mapping[header] = "external_id"
        else:
            mapping[header] = by_normalized.get(norm, norm)

    duplicates = {f for f in mapping.values() if list(mapping.values()).count(f) > 1}
    missing = [f for f in ("external_id", primary) if f not in mapping.values()]
    if missing or duplicates:
        raise BadRequestError(
            "CSV columns do not match this tenant's domain config",
            details=_suggestions(missing, duplicates, headers, mapping, list(known)),
        )
    return mapping


def _suggestions(
    missing: list[str],
    duplicates: set[str],
    headers: list[str],
    mapping: dict[str, str],
    known_fields: list[str],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    unmatched = [h for h in headers if mapping.get(h) not in known_fields]
    for field in missing:
        candidates = difflib.get_close_matches(
            normalize_header(field), [normalize_header(h) for h in unmatched], n=3, cutoff=0.5
        )
        originals = [h for h in unmatched if normalize_header(h) in candidates]
        if field == "external_id":
            hint = f"Add a column named one of: {', '.join(EXTERNAL_ID_ALIASES)}."
        else:
            hint = f"Add a column named '{field}'."
        if originals:
            hint += f" Did you mean to rename {', '.join(repr(h) for h in originals)}?"
        details.append(
            {"field": field, "message": f"No column found. {hint}", "type": "missing_column"}
        )
    for field in sorted(duplicates):
        columns = [h for h, f in mapping.items() if f == field]
        details.append(
            {
                "field": field,
                "message": f"Columns {columns} all map to '{field}'; keep only one.",
                "type": "duplicate_column",
            }
        )
    details.append(
        {
            "field": None,
            "message": "Expected columns: external_id, " + ", ".join(known_fields),
            "type": "expected_columns",
        }
    )
    return details
