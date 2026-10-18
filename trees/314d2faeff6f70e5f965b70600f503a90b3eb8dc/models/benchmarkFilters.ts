import type { Snapshot } from "../../api";

export type BenchmarkRow = Extract<
  Snapshot["models"],
  { availability: "AVAILABLE" }
>["data"]["benchmarks"][number];

export function uniqueDevices(benchmarks: BenchmarkRow[]): string[] {
  return [...new Set(benchmarks.map((b) => b.device))].sort();
}

export function uniqueShapes(
  benchmarks: BenchmarkRow[],
): { width: number; context: number; label: string }[] {
  const seen = new Map<string, { width: number; context: number }>();
  for (const b of benchmarks) {
    const key = `${b.width}:${b.context}`;
    if (!seen.has(key)) seen.set(key, { width: b.width, context: b.context });
  }
  return [...seen.values()]
    .sort((a, b) => a.width - b.width || a.context - b.context)
    .map(({ width, context }) => ({
      width,
      context,
      label: `${width}×${context}`,
    }));
}

export function filterBenchmarks(
  benchmarks: BenchmarkRow[],
  device: string,
  shape: string,
): BenchmarkRow[] {
  return benchmarks.filter((b) => {
    const deviceOk = device === "all" || b.device === device;
    const shapeOk =
      shape === "all" || `${b.width}×${b.context}` === shape;
    return deviceOk && shapeOk;
  });
}

/** Timings are comparable only within one width×context×device group. */
export function timingsComparable(rows: BenchmarkRow[]): boolean {
  if (rows.length === 0) return false;
  const first = rows[0]!;
  return rows.every(
    (r) =>
      r.width === first.width &&
      r.context === first.context &&
      r.device === first.device,
  );
}

export function minMeasuredInference(rows: BenchmarkRow[]): number | null {
  const values = rows
    .map((r) => r.medianInferenceMs)
    .filter((v): v is number => v !== null);
  if (values.length === 0) return null;
  return Math.min(...values);
}
