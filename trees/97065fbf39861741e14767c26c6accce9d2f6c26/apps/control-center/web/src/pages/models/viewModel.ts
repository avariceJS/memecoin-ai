import type { RunRow } from "./benchmarkFilters";

export type ArchitectureBar = {
  modelKind: string;
  parameters: number | null;
  runs: number;
  syntheticRuns: number;
  realRuns: number;
};

export function architectureBars(runs: readonly RunRow[]): ArchitectureBar[] {
  const groups = new Map<string, RunRow[]>();
  for (const run of runs) {
    const list = groups.get(run.modelKind);
    if (list) list.push(run);
    else groups.set(run.modelKind, [run]);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([modelKind, group]) => {
      const parameters = new Set(group.map((run) => run.parameters));
      return {
        modelKind,
        parameters: parameters.size === 1 ? group[0]!.parameters : null,
        runs: group.length,
        syntheticRuns: group.filter((run) => run.datasetKind === "SYNTHETIC").length,
        realRuns: group.filter((run) => run.datasetKind === "REAL").length,
      };
    });
}
