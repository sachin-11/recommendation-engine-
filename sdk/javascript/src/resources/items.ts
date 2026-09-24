import type { RecoEngineClient } from "../client";
import { RecoEngineError } from "../errors";
import type {
  BatchStatus,
  CsvBatchResult,
  Item,
  ItemInput,
  ListOptions,
  PaginatedItems,
  UploadOptions,
  UploadResult,
  WaitOptions,
} from "../types";

const enc = encodeURIComponent;

export class Items {
  constructor(private readonly client: RecoEngineClient) {}

  /**
   * Upload up to 1000 items. Returns a batch to poll (default), or per-item results with
   * `{ async: false }` (at most 50 items). Re-uploading an external_id updates the item.
   */
  async upload(items: ItemInput[], options: UploadOptions = {}): Promise<UploadResult> {
    const response = await this.client.request<UploadResult>({
      method: "POST",
      url: "/items/upload",
      data: { items, async: options.async ?? true },
    });
    return response.data;
  }

  /**
   * Upload a CSV file. In Node pass a file path; anywhere pass a Blob/File.
   * Always asynchronous: returns a batch to poll.
   */
  async uploadCSV(file: string | Blob, filename = "items.csv"): Promise<CsvBatchResult> {
    let blob: Blob;
    if (typeof file === "string") {
      const { readFile } = await import("node:fs/promises");
      const { basename } = await import("node:path");
      blob = new Blob([await readFile(file)], { type: "text/csv" });
      filename = basename(file);
    } else {
      blob = file;
    }
    const form = new FormData();
    form.append("file", blob, filename);
    const response = await this.client.request<CsvBatchResult>({
      method: "POST",
      url: "/items/upload-csv",
      data: form,
    });
    return response.data;
  }

  async getBatchStatus(batchId: string): Promise<BatchStatus> {
    return (await this.client.request<BatchStatus>({ method: "GET", url: `/items/batch/${enc(batchId)}` })).data;
  }

  /** Poll a batch until it is DONE or PARTIAL_FAIL. */
  async waitForBatch(batchId: string, options: WaitOptions = {}): Promise<BatchStatus> {
    const interval = options.intervalMs ?? 2000;
    const deadline = Date.now() + (options.timeoutMs ?? 300_000);
    for (;;) {
      const batch = await this.getBatchStatus(batchId);
      if (batch.status === "DONE" || batch.status === "PARTIAL_FAIL") return batch;
      if (Date.now() + interval > deadline) {
        throw new RecoEngineError(
          `Batch ${batchId} still ${batch.status} (${batch.processed_items}/${batch.total_items}) after timeout`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, interval));
    }
  }

  async get(externalId: string): Promise<Item> {
    return (await this.client.request<Item>({ method: "GET", url: `/items/${enc(externalId)}` })).data;
  }

  async list(options: ListOptions = {}): Promise<PaginatedItems> {
    const response = await this.client.request<PaginatedItems>({
      method: "GET",
      url: "/items",
      params: { page: options.page, status: options.status, search: options.search },
    });
    return response.data;
  }

  /** Delete one item from the database and the vector index. */
  async delete(externalId: string): Promise<void> {
    await this.client.request({ method: "DELETE", url: `/items/${enc(externalId)}` });
  }

  /** Delete up to 1000 items; ids that do not exist are returned in `not_found`. */
  async deleteMany(externalIds: string[]): Promise<{ deleted: number; not_found: string[] }> {
    const response = await this.client.request<{ deleted: number; not_found: string[] }>({
      method: "POST",
      url: "/items/bulk-delete",
      data: { external_ids: externalIds },
    });
    return response.data;
  }
}
