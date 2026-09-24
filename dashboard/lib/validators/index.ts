import { z } from "zod";

import type { DomainConfig } from "@/types";

// Same rule as the backend's FieldName: identifier-like, max 64 chars.
const fieldName = z
  .string()
  .trim()
  .regex(/^[A-Za-z_][A-Za-z0-9_]{0,63}$/, "Use letters, digits and underscores; start with a letter");

export const accountSchema = z.object({
  name: z.string().trim().min(1, "Business name is required").max(255),
  email: z.string().trim().toLowerCase().email("Enter a valid email address"),
  password: z.string().min(8, "Use at least 8 characters").max(128),
});
export type AccountValues = z.infer<typeof accountSchema>;

export const loginSchema = z.object({
  email: z.string().trim().toLowerCase().email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});
export type LoginValues = z.infer<typeof loginSchema>;

export const apiKeyLoginSchema = z.object({
  apiKey: z
    .string()
    .trim()
    .regex(/^reco_[A-Za-z0-9_-]{20,}$/, "API keys look like reco_… (at least 25 characters)"),
});
export type ApiKeyLoginValues = z.infer<typeof apiKeyLoginSchema>;

export const domainConfigSchema = z
  .object({
    primary_embedding_field: fieldName,
    searchable_fields: z.array(fieldName).min(1, "Add at least one searchable field").max(50),
    filter_fields: z.array(fieldName).max(50),
    item_label: z.string().trim().min(1, "Item label is required").max(50),
  })
  .strict()
  .superRefine((config, ctx) => {
    for (const key of ["searchable_fields", "filter_fields"] as const) {
      if (new Set(config[key]).size !== config[key].length) {
        ctx.addIssue({ code: "custom", path: [key], message: "Remove duplicate fields" });
      }
    }
    if (!config.searchable_fields.includes(config.primary_embedding_field)) {
      ctx.addIssue({
        code: "custom",
        path: ["primary_embedding_field"],
        message: "Must also be one of the searchable fields",
      });
    }
  });

/** Parse JSON text into a validated domain config, or return the errors as strings. */
export function parseDomainConfig(
  text: string,
): { ok: true; config: DomainConfig } | { ok: false; errors: string[] } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, errors: [`Invalid JSON: ${(error as Error).message}`] };
  }
  const result = domainConfigSchema.safeParse(raw);
  if (result.success) return { ok: true, config: result.data };
  return {
    ok: false,
    errors: result.error.issues.map((i) => (i.path.length ? `${i.path.join(".")}: ${i.message}` : i.message)),
  };
}

const itemSchema = z
  .object({
    external_id: z.union([z.string().trim().min(1).max(255), z.number().int()]),
  })
  .passthrough();

export const MAX_ITEMS_PER_UPLOAD = 1000;

export interface ItemsCheck {
  items: Record<string, unknown>[];
  errors: string[];
  warnings: string[];
}

/** Validate pasted JSON items against the upload rules and the tenant's domain config. */
export function checkItemsJson(text: string, config: DomainConfig): ItemsCheck {
  const result: ItemsCheck = { items: [], errors: [], warnings: [] };
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    result.errors.push(`Invalid JSON: ${(error as Error).message}`);
    return result;
  }
  const list = Array.isArray(raw) ? raw : (raw as { items?: unknown })?.items;
  if (!Array.isArray(list)) {
    result.errors.push("Paste a JSON array of items, or an object with an `items` array.");
    return result;
  }
  if (list.length === 0) result.errors.push("The array is empty.");
  if (list.length > MAX_ITEMS_PER_UPLOAD) {
    result.errors.push(`At most ${MAX_ITEMS_PER_UPLOAD} items per upload (got ${list.length}). Use CSV for more.`);
  }
  const embeddable = [config.primary_embedding_field, ...config.searchable_fields];
  let missingPrimary = 0;
  let nothingToEmbed = 0;
  list.forEach((entry, index) => {
    const parsed = itemSchema.safeParse(entry);
    if (!parsed.success) {
      if (result.errors.length < 10) result.errors.push(`Item ${index + 1}: needs a non-empty external_id`);
      return;
    }
    const item: Record<string, unknown> = { ...parsed.data, external_id: String(parsed.data.external_id) };
    if (item[config.primary_embedding_field] == null) missingPrimary += 1;
    if (!embeddable.some((field) => item[field] != null && item[field] !== "")) nothingToEmbed += 1;
    result.items.push(item);
  });
  const ids = result.items.map((i) => i.external_id);
  const duplicates = ids.length - new Set(ids).size;
  if (duplicates) result.warnings.push(`${duplicates} duplicate external_id(s): the last one wins.`);
  if (missingPrimary) {
    result.warnings.push(`${missingPrimary} item(s) have no "${config.primary_embedding_field}" (the primary field).`);
  }
  if (nothingToEmbed) {
    result.errors.push(`${nothingToEmbed} item(s) have none of the searchable fields and would fail to embed.`);
  }
  return result;
}
