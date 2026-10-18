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
