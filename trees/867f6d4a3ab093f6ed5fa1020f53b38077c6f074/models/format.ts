export function formatTimingMs(value: number | null): string {
  if (value === null) return "не измерено";
  if (value < 1) return `${value.toFixed(3)} ms`;
  return `${value.toFixed(2)} ms`;
}

export function formatCount(value: number): string {
  return value.toLocaleString("ru-RU");
}

export function runsLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} прогон`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} прогона`;
  return `${count} прогонов`;
}

export function shapeKey(width: number, context: number): string {
  return `${width}×${context}`;
}

export function metricLabel(metric: "medianInferenceMs" | "medianForwardBackwardMs"): string {
  if (metric === "medianInferenceMs") return "медиана инференса";
  return "медиана прямого и обратного";
}
