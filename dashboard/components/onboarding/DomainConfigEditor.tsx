"use client";

import * as React from "react";
import { AlertCircle, Braces, ListChecks } from "lucide-react";

import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { TagInput } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { domainConfigSchema, parseDomainConfig } from "@/lib/validators";
import type { DomainConfig } from "@/types";

/** Errors for the current config, keyed by field ("" for form-wide). */
export function configErrors(config: DomainConfig): Record<string, string> {
  const result = domainConfigSchema.safeParse(config);
  if (result.success) return {};
  const errors: Record<string, string> = {};
  for (const issue of result.error.issues) {
    const key = String(issue.path[0] ?? "");
    errors[key] ??= issue.message;
  }
  return errors;
}

/**
 * Edit a domain config as a form (tag inputs) or as raw JSON. Both views stay in sync;
 * invalid JSON is kept in the text box until it parses, so typing is never interrupted.
 */
export function DomainConfigEditor({
  value,
  onChange,
}: {
  value: DomainConfig;
  onChange: (config: DomainConfig) => void;
}) {
  const [json, setJson] = React.useState(() => JSON.stringify(value, null, 2));
  const [jsonErrors, setJsonErrors] = React.useState<string[]>([]);
  const errors = configErrors(value);

  // Keep the JSON view in step with form edits (but not while the user is mid-typing JSON).
  const lastEmitted = React.useRef(value);
  React.useEffect(() => {
    if (value !== lastEmitted.current) {
      setJson(JSON.stringify(value, null, 2));
      setJsonErrors([]);
    }
  }, [value]);

  const update = (patch: Partial<DomainConfig>) => {
    const next = { ...value, ...patch };
    lastEmitted.current = next;
    onChange(next);
  };

  const onJsonChange = (text: string) => {
    setJson(text);
    const parsed = parseDomainConfig(text);
    if (parsed.ok) {
      setJsonErrors([]);
      lastEmitted.current = parsed.config;
      onChange(parsed.config);
    } else {
      setJsonErrors(parsed.errors);
    }
  };

  return (
    <Tabs defaultValue="form">
      <TabsList>
        <TabsTrigger value="form">
          <ListChecks /> Fields
        </TabsTrigger>
        <TabsTrigger value="json">
          <Braces /> JSON
        </TabsTrigger>
      </TabsList>

      <TabsContent value="form" className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="searchable">Searchable fields</Label>
          <TagInput
            id="searchable"
            value={value.searchable_fields}
            onChange={(searchable_fields) => {
              const primary = searchable_fields.includes(value.primary_embedding_field)
                ? value.primary_embedding_field
                : (searchable_fields[0] ?? "");
              update({ searchable_fields, primary_embedding_field: primary });
            }}
            placeholder="title, description… press Enter to add"
            invalid={Boolean(errors.searchable_fields)}
          />
          <p className="text-xs text-muted-foreground">
            These fields are combined into the text that gets embedded.
          </p>
          <FieldError message={errors.searchable_fields} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="primary">Primary embedding field</Label>
            <NativeSelect
              id="primary"
              value={value.primary_embedding_field}
              onChange={(e) => update({ primary_embedding_field: e.target.value })}
              aria-invalid={Boolean(errors.primary_embedding_field)}
            >
              {value.searchable_fields.length === 0 && <option value="">Add a searchable field</option>}
              {value.searchable_fields.map((field) => (
                <option key={field} value={field}>
                  {field}
                </option>
              ))}
            </NativeSelect>
            <p className="text-xs text-muted-foreground">Placed first in the embedded text.</p>
            <FieldError message={errors.primary_embedding_field} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="label">Item label</Label>
            <Input
              id="label"
              value={value.item_label}
              onChange={(e) => update({ item_label: e.target.value })}
              placeholder="job, dish, product…"
              aria-invalid={Boolean(errors.item_label)}
            />
            <FieldError message={errors.item_label} />
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="filters">Filter fields</Label>
          <TagInput
            id="filters"
            value={value.filter_fields}
            onChange={(filter_fields) => update({ filter_fields })}
            placeholder="location, category… press Enter to add"
            invalid={Boolean(errors.filter_fields)}
          />
          <p className="text-xs text-muted-foreground">
            Stored with each vector so recommendations can be filtered, e.g. by location or price.
          </p>
          <FieldError message={errors.filter_fields} />
        </div>
      </TabsContent>

      <TabsContent value="json" className="space-y-2">
        <Textarea
          value={json}
          onChange={(e) => onJsonChange(e.target.value)}
          spellCheck={false}
          rows={12}
          className="font-mono text-xs leading-relaxed"
          aria-invalid={jsonErrors.length > 0}
          aria-label="Domain config JSON"
        />
        {jsonErrors.length > 0 ? (
          <div className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
            {jsonErrors.map((error) => (
              <p key={error} className="flex items-start gap-1.5">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {error}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Valid config. Changes apply as you type.</p>
        )}
      </TabsContent>
    </Tabs>
  );
}
