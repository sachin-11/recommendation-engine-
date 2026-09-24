// Turns the playground's plain text filter inputs into API filters.
//   "Delhi"        -> "Delhi"            (exact)
//   "Delhi, Pune"  -> ["Delhi", "Pune"]  (any of)
//   ">=3" / "<5"   -> { gte: 3 } / { lt: 5 }
//   "3..8"         -> { gte: 3, lte: 8 }
//   "true"         -> true

type Scalar = string | number | boolean;

function scalar(raw: string): Scalar {
  const value = raw.trim();
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}

export function parseFilterValue(raw: string): unknown {
  const value = raw.trim();
  if (!value) return undefined;
  const range = value.match(/^(-?\d+(?:\.\d+)?)\s*\.\.\s*(-?\d+(?:\.\d+)?)$/);
  if (range) return { gte: Number(range[1]), lte: Number(range[2]) };
  const comparison = value.match(/^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$/);
  if (comparison) {
    const op = { ">=": "gte", "<=": "lte", ">": "gt", "<": "lt" }[comparison[1]]!;
    return { [op]: Number(comparison[2]) };
  }
  if (value.includes(",")) {
    const parts = value.split(",").map((part) => part.trim()).filter(Boolean);
    return parts.map(scalar);
  }
  return scalar(value);
}

export function buildFilters(inputs: Record<string, string>): Record<string, unknown> {
  const filters: Record<string, unknown> = {};
  for (const [field, raw] of Object.entries(inputs)) {
    const parsed = parseFilterValue(raw);
    if (parsed !== undefined) filters[field] = parsed;
  }
  return filters;
}
