export type CalendarTimeZone = "UTC" | "Europe/Moscow";

export const TIME_ZONE_LABELS: Record<CalendarTimeZone, string> = {
  UTC: "UTC (Coordinated Universal Time)",
  "Europe/Moscow": "Europe/Moscow (МСК)",
};

export function formatIsoInstant(iso: string, zone: CalendarTimeZone): string {
  const date = new Date(iso);
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: zone,
    dateStyle: "short",
    timeStyle: "medium",
  }).format(date);
}

export function formatEpochNs(ns: string, zone: CalendarTimeZone): string {
  const ms = Number(BigInt(ns) / 1_000_000n);
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: zone,
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(ms));
}

export function plannedAtMs(iso: string): number {
  return new Date(iso).getTime();
}
