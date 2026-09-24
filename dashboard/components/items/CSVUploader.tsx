"use client";

import * as React from "react";
import { AlertCircle, ArrowRight, FileSpreadsheet, Loader2, Upload, UploadCloud } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { NativeSelect } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toastApiError } from "@/lib/api";
import { EXTERNAL_ID_ALIASES, normalizeHeader, type ParsedCsv, parseCsv, toCsv } from "@/lib/csv";
import { useUploadCsv } from "@/lib/hooks/items";
import { cn } from "@/lib/utils";
import type { Tenant } from "@/types";

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_ROWS = 10_000;
const SKIP = "__skip__";
const KEEP = "__keep__";

type Mapping = Record<string, string>; // CSV header -> target field, SKIP or KEEP

function autoMap(headers: string[], fields: string[]): Mapping {
  const byNormalized = new Map(fields.map((f) => [normalizeHeader(f), f]));
  const mapping: Mapping = {};
  let idTaken = false;
  for (const header of headers) {
    const norm = normalizeHeader(header);
    if (!idTaken && EXTERNAL_ID_ALIASES.includes(norm)) {
      mapping[header] = "external_id";
      idTaken = true;
    } else {
      mapping[header] = byNormalized.get(norm) ?? (norm ? KEEP : SKIP);
    }
  }
  return mapping;
}

function target(header: string, choice: string): string | null {
  if (choice === SKIP) return null;
  return choice === KEEP ? normalizeHeader(header) : choice;
}

export function CSVUploader({
  open,
  onOpenChange,
  tenant,
  onQueued,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tenant: Tenant;
  onQueued: (batchId: string) => void;
}) {
  const config = tenant.domain_config;
  const fields = React.useMemo(
    () => Array.from(new Set([config.primary_embedding_field, ...config.searchable_fields, ...config.filter_fields])),
    [config],
  );
  const [file, setFile] = React.useState<File | null>(null);
  const [csv, setCsv] = React.useState<ParsedCsv | null>(null);
  const [mapping, setMapping] = React.useState<Mapping>({});
  const [fileError, setFileError] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const upload = useUploadCsv();

  const reset = () => {
    setFile(null);
    setCsv(null);
    setMapping({});
    setFileError(null);
  };

  const load = async (picked: File | undefined) => {
    if (!picked) return;
    setFileError(null);
    if (picked.size > MAX_BYTES) return setFileError("The file is larger than 10 MB.");
    const parsed = parseCsv(await picked.text());
    if (!parsed.headers.length) return setFileError("The file has no header row.");
    if (!parsed.rows.length) return setFileError("The file has a header row but no data rows.");
    if (parsed.rows.length > MAX_ROWS) return setFileError(`At most ${MAX_ROWS} rows per file.`);
    setFile(picked);
    setCsv(parsed);
    setMapping(autoMap(parsed.headers, fields));
  };

  const targets = csv ? csv.headers.map((h) => target(h, mapping[h] ?? SKIP)) : [];
  const used = targets.filter((t): t is string => Boolean(t));
  const duplicates = Array.from(new Set(used.filter((t, i) => used.indexOf(t) !== i)));
  const problems: string[] = [];
  if (csv) {
    if (!used.includes("external_id")) problems.push("Map one column to external_id.");
    if (!used.includes(config.primary_embedding_field)) {
      problems.push(`Map a column to "${config.primary_embedding_field}" (the primary field).`);
    }
    if (duplicates.length) problems.push(`Several columns map to: ${duplicates.join(", ")}.`);
  }

  const submit = () => {
    if (!csv || !file || problems.length) return;
    const kept = csv.headers.map((h, i) => [i, target(h, mapping[h] ?? SKIP)] as const).filter(([, t]) => t);
    const body = toCsv(
      kept.map(([, t]) => t as string),
      csv.rows.map((row) => kept.map(([i]) => row[i] ?? "")),
    );
    const mapped = new File([body], file.name.replace(/\.[^.]+$/, "") + ".mapped.csv", { type: "text/csv" });
    upload.mutate(mapped, {
      onSuccess: (batch) => {
        toast.success(`Queued ${batch.total_items} items from ${file.name}`);
        onQueued(batch.batch_id);
        reset();
        onOpenChange(false);
      },
      onError: (error) => toastApiError(error, "CSV upload failed"),
    });
  };

  const optionsFor = (header: string) => (
    <>
      <option value="external_id">external_id (id)</option>
      <optgroup label="Domain fields">
        {fields.map((field) => (
          <option key={field} value={field}>
            {field}
            {field === config.primary_embedding_field ? " (primary)" : ""}
          </option>
        ))}
      </optgroup>
      <optgroup label="Other">
        <option value={KEEP}>Keep as “{normalizeHeader(header) || header}”</option>
        <option value={SKIP}>Skip column</option>
      </optgroup>
    </>
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Upload CSV</DialogTitle>
          <DialogDescription>
            Up to {MAX_ROWS.toLocaleString()} rows (10 MB). Match each column to one of your
            fields; columns you keep but do not map are stored with the item.
          </DialogDescription>
        </DialogHeader>

        {!csv ? (
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                void load(e.dataTransfer.files[0]);
              }}
              className={cn(
                "flex w-full flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors hover:border-primary/60 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                dragging && "border-primary bg-primary/5",
              )}
            >
              <UploadCloud className="h-10 w-10 text-primary" />
              <span className="font-medium">Drop a CSV file here, or click to choose</span>
              <span className="text-sm text-muted-foreground">
                Include an id column (external_id, id, item_id or sku) and “{config.primary_embedding_field}”.
              </span>
            </button>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => void load(e.target.files?.[0])}
            />
            {fileError && (
              <p className="flex items-center gap-1.5 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" /> {fileError}
              </p>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <FileSpreadsheet className="h-4 w-4 text-primary" />
              <span className="font-medium">{file?.name}</span>
              <Badge variant="secondary">{csv.rows.length} rows</Badge>
              <Badge variant="secondary">{csv.headers.length} columns</Badge>
              <Button variant="link" size="sm" className="h-auto p-0" onClick={reset}>
                Choose another file
              </Button>
            </div>

            <div className="space-y-2" role="list" aria-label="Column mapping">
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span>CSV column</span>
                <span />
                <span>Maps to</span>
              </div>
              {csv.headers.map((header, index) => {
                const choice = mapping[header] ?? SKIP;
                const mapped = target(header, choice);
                const isDuplicate = mapped !== null && duplicates.includes(mapped);
                return (
                  <div key={`${header}-${index}`} role="listitem" className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
                    <div className="min-w-0 rounded-md border bg-muted/30 px-3 py-1.5">
                      <p className="truncate font-mono text-xs font-medium">{header || `(column ${index + 1})`}</p>
                      <p className="truncate text-xs text-muted-foreground">e.g. {csv.rows[0]?.[index] || "—"}</p>
                    </div>
                    <ArrowRight
                      className={cn(
                        "h-4 w-4",
                        choice === SKIP ? "text-muted-foreground/40" : "text-primary",
                      )}
                    />
                    <NativeSelect
                      value={choice}
                      onChange={(e) => setMapping({ ...mapping, [header]: e.target.value })}
                      aria-label={`Map ${header}`}
                      aria-invalid={isDuplicate}
                      className={cn("font-mono text-xs", isDuplicate && "border-destructive")}
                    >
                      {optionsFor(header)}
                    </NativeSelect>
                  </div>
                );
              })}
            </div>

            <div>
              <p className="mb-2 text-sm font-medium">Preview</p>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {targets.map((t, i) => t && <TableHead key={i} className="font-mono normal-case">{t}</TableHead>)}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {csv.rows.slice(0, 3).map((row, r) => (
                      <TableRow key={r}>
                        {targets.map(
                          (t, i) =>
                            t && (
                              <TableCell key={i} className="max-w-[12rem] truncate text-xs">
                                {row[i]}
                              </TableCell>
                            ),
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>

            {problems.map((problem) => (
              <p key={problem} className="flex items-start gap-1.5 text-sm text-destructive">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {problem}
              </p>
            ))}
          </div>
        )}

        <DialogFooter>
          <Button onClick={submit} disabled={!csv || problems.length > 0 || upload.isPending}>
            {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
            Upload {csv ? `${csv.rows.length} rows` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
