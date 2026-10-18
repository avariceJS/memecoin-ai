import type { Snapshot } from "../../api";
import { shapeKey } from "./format";

export type BenchmarkRow = Extract<
  Snapshot["models"],
  { availability: "AVAILABLE" }
>["data"]["benchmarks"][number];

export type RunRow = Extract<
  Snapshot["models"],
  { availability: "AVAILABLE" }
>["data"]["runs"][number];

export type TimingMetric = "medianInferenceMs" | "medianForwardBackwardMs";

/** device: CPU и MPS внутри одной модели. model: архитектуры на одном device. */
export type ComparisonMode = "device" | "model";

export type ComparisonFault =
  | "empty"
  | "mixed-width"
  | "mixed-context"
  | "mixed-batch"
  | "mixed-status"
  | "mixed-model"
  | "mixed-device";

const FAULT_TEXT: Record<ComparisonFault, string> = {
  empty: "Нет строк в выбранной группе. Сравнения нет.",
  "mixed-width": "Разная width. Сравнения нет.",
  "mixed-context": "Разный context. Сравнения нет.",
  "mixed-batch": "Разный batch. Сравнения нет.",
  "mixed-status": "Разный status. Сравнения нет.",
  "mixed-model": "Разные модели. CPU и MPS сравниваются только для одной модели. Сравнения нет.",
  "mixed-device": "Разные device. Архитектуры сравниваются только на одном device. Сравнения нет.",
};

export type BenchmarkQuery = {
  device?: string;
  shape?: string;
  batch?: string;
  model?: string;
  status?: string;
};

export type AxisSelection = {
  model: string;
  device: string;
  shape: string;
  batch: string;
};

export function uniqueDevices(benchmarks: readonly BenchmarkRow[]): string[] {
  return [...new Set(benchmarks.map((row) => row.device))].sort();
}

export function uniqueModels(benchmarks: readonly BenchmarkRow[]): string[] {
  return [...new Set(benchmarks.map((row) => row.modelKind))].sort();
}

export function uniqueShapes(
  benchmarks: readonly BenchmarkRow[],
): { width: number; context: number; label: string }[] {
  const seen = new Map<string, { width: number; context: number }>();
  for (const row of benchmarks) {
    const key = shapeKey(row.width, row.context);
    if (!seen.has(key)) seen.set(key, { width: row.width, context: row.context });
  }
  return [...seen.values()]
    .sort((a, b) => a.width - b.width || a.context - b.context)
    .map(({ width, context }) => ({
      width,
      context,
      label: shapeKey(width, context),
    }));
}

export function uniqueBatches(benchmarks: readonly BenchmarkRow[]): string[] {
  return [...new Set(benchmarks.map((row) => row.batchSize))]
    .sort((a, b) => a - b)
    .map(String);
}

export function filterBenchmarks(
  benchmarks: readonly BenchmarkRow[],
  query: BenchmarkQuery,
): BenchmarkRow[] {
  return benchmarks.filter((row) => {
    if (query.device && query.device !== "all" && row.device !== query.device) return false;
    if (query.shape && query.shape !== "all" && shapeKey(row.width, row.context) !== query.shape) {
      return false;
    }
    if (query.batch && query.batch !== "all" && String(row.batchSize) !== query.batch) return false;
    if (query.model && query.model !== "all" && row.modelKind !== query.model) return false;
    if (query.status && query.status !== "all" && row.status !== query.status) return false;
    return true;
  });
}

export function rowsForAxis(
  benchmarks: readonly BenchmarkRow[],
  mode: ComparisonMode,
  selection: AxisSelection,
): BenchmarkRow[] {
  return filterBenchmarks(benchmarks, {
    shape: selection.shape,
    batch: selection.batch,
    model: mode === "device" ? selection.model : "all",
    device: mode === "model" ? selection.device : "all",
  });
}

export function comparisonFault(
  rows: readonly BenchmarkRow[],
  mode: ComparisonMode,
): ComparisonFault | null {
  if (rows.length === 0) return "empty";
  const first = rows[0]!;
  if (rows.some((row) => row.width !== first.width)) return "mixed-width";
  if (rows.some((row) => row.context !== first.context)) return "mixed-context";
  if (rows.some((row) => row.batchSize !== first.batchSize)) return "mixed-batch";
  if (rows.some((row) => row.status !== first.status)) return "mixed-status";
  if (mode === "device" && rows.some((row) => row.modelKind !== first.modelKind)) return "mixed-model";
  if (mode === "model" && rows.some((row) => row.device !== first.device)) return "mixed-device";
  return null;
}

/**
 * Сопоставимость одной оси и одной метрики.
 * device: одна модель, width, context, batch, status; device может различаться.
 * model: один device, width, context, batch, status; модель может различаться.
 * Метрика — единственное поле замера. null или несовпадение ключей → false, без минимума.
 */
export function timingsComparable(
  rows: readonly BenchmarkRow[],
  mode: ComparisonMode,
  metric: TimingMetric,
): boolean {
  if (comparisonFault(rows, mode) !== null) return false;
  return rows.every((row) => row[metric] !== null);
}

export function comparisonError(
  rows: readonly BenchmarkRow[],
  mode: ComparisonMode,
  metric: TimingMetric,
): string | null {
  const fault = comparisonFault(rows, mode);
  if (fault) return FAULT_TEXT[fault];
  if (rows.some((row) => row[metric] === null)) return "Метрика не измерена. Сравнения нет.";
  return null;
}

/** Минимум группы только если она сопоставима и в ней хотя бы две измеренные строки. */
export function groupMinimum(
  rows: readonly BenchmarkRow[],
  mode: ComparisonMode,
  metric: TimingMetric,
): number | null {
  if (!timingsComparable(rows, mode, metric) || rows.length < 2) return null;
  let min = Number.POSITIVE_INFINITY;
  for (const row of rows) {
    const value = row[metric];
    if (value === null) return null;
    if (value < min) min = value;
  }
  return Number.isFinite(min) ? min : null;
}
