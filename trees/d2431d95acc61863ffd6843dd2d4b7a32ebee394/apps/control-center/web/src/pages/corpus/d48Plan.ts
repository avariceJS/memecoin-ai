const MS_PER_DAY = 86_400_000;
const NS_PER_MS = 1_000_000n;

export type D48Sampling = {
  anchorNs: string;
  developmentEndNs: string;
  holdoutEndNs: string;
  plannedWindows: number;
  developmentWindows: number;
  reservedWindows: number;
  utcOffsets: string[];
};

export type D48Cell = {
  linear: number;
  dayIndex: number;
  stratumIndex: number;
  kind: "development" | "reserved";
  iso: string;
  offset: string;
};

export type D48Month = {
  key: string;
  label: string;
  dayCount: number;
};

export type D48Week = {
  dayIndex: number;
  dayCount: number;
  label: string;
  iso: string;
};

export type D48Plan =
  | {
      ok: true;
      strata: string[];
      days: number;
      developmentDays: number;
      reservedDays: number;
      cells: D48Cell[];
      months: D48Month[];
      weeks: D48Week[];
    }
  | { ok: false; reason: "D48_PLAN_INCONSISTENT" };

function nsToMs(ns: string): number | null {
  if (!/^\d+$/.test(ns)) return null;
  const ms = BigInt(ns) / NS_PER_MS;
  if (ms > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(ms);
}

function wholeDays(startMs: number, endMs: number): number | null {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return null;
  const delta = endMs - startMs;
  if (delta <= 0 || delta % MS_PER_DAY !== 0) return null;
  return delta / MS_PER_DAY;
}

function utcIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function fail(): D48Plan {
  return { ok: false, reason: "D48_PLAN_INCONSISTENT" };
}

export function deriveD48Plan(sampling: D48Sampling): D48Plan {
  const strata = sampling.utcOffsets;
  if (strata.length === 0 || strata.some((offset) => !/^\d{2}:\d{2}$/.test(offset))) {
    return fail();
  }
  if (sampling.plannedWindows !== sampling.developmentWindows + sampling.reservedWindows) {
    return fail();
  }
  if (
    sampling.developmentWindows % strata.length !== 0 ||
    sampling.reservedWindows % strata.length !== 0 ||
    sampling.plannedWindows % strata.length !== 0
  ) {
    return fail();
  }

  const anchor = nsToMs(sampling.anchorNs);
  const developmentEnd = nsToMs(sampling.developmentEndNs);
  const holdoutEnd = nsToMs(sampling.holdoutEndNs);
  if (anchor === null || developmentEnd === null || holdoutEnd === null) return fail();

  const developmentDays = wholeDays(anchor, developmentEnd);
  const reservedDays = wholeDays(developmentEnd, holdoutEnd);
  const days = wholeDays(anchor, holdoutEnd);
  if (developmentDays === null || reservedDays === null || days === null) return fail();
  if (developmentDays + reservedDays !== days) return fail();
  if (developmentDays * strata.length !== sampling.developmentWindows) return fail();
  if (reservedDays * strata.length !== sampling.reservedWindows) return fail();
  if (days * strata.length !== sampling.plannedWindows) return fail();

  const cells: D48Cell[] = [];
  for (let dayIndex = 0; dayIndex < days; dayIndex += 1) {
    const iso = utcIso(anchor + dayIndex * MS_PER_DAY);
    for (let stratumIndex = 0; stratumIndex < strata.length; stratumIndex += 1) {
      cells.push({
        linear: dayIndex * strata.length + stratumIndex,
        dayIndex,
        stratumIndex,
        kind: dayIndex < developmentDays ? "development" : "reserved",
        iso,
        offset: strata[stratumIndex] ?? "",
      });
    }
  }

  const months: D48Month[] = [];
  let cursor = 0;
  while (cursor < days) {
    const startMs = anchor + cursor * MS_PER_DAY;
    const key = utcIso(startMs).slice(0, 7);
    let dayCount = 0;
    while (cursor + dayCount < days && utcIso(anchor + (cursor + dayCount) * MS_PER_DAY).slice(0, 7) === key) {
      dayCount += 1;
    }
    months.push({
      key,
      dayCount,
      label: new Intl.DateTimeFormat("ru-RU", {
        timeZone: "UTC",
        month: "short",
      }).format(new Date(startMs)),
    });
    cursor += dayCount;
  }

  const weeks: D48Week[] = [];
  for (let dayIndex = 0; dayIndex < days; dayIndex += 7) {
    const startMs = anchor + dayIndex * MS_PER_DAY;
    weeks.push({
      dayIndex,
      dayCount: Math.min(7, days - dayIndex),
      iso: utcIso(startMs),
      label: new Intl.DateTimeFormat("ru-RU", {
        timeZone: "UTC",
        day: "2-digit",
        month: "2-digit",
      }).format(new Date(startMs)),
    });
  }

  return {
    ok: true,
    strata,
    days,
    developmentDays,
    reservedDays,
    cells,
    months,
    weeks,
  };
}
