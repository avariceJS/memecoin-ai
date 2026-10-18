export function formatProbeSeconds(value: number): string {
  if (value >= 1) return value.toFixed(3);
  return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, ".0");
}

export function formatClockAgeRangeSeconds(range: number[]): string {
  if (range.length < 2) return "—";
  const lo = Math.min(range[0]!, range[1]!);
  const hi = Math.max(range[0]!, range[1]!);
  return `${lo.toFixed(3)}–${hi.toFixed(3)} с`;
}

export function shortUtc(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "UTC",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function yesNo(value: boolean): string {
  return value ? "да" : "нет";
}
