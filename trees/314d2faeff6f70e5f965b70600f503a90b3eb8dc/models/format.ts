export function formatTimingMs(value: number | null): string {
  if (value === null) return "не измерено";
  if (value < 1) return `${value.toFixed(3)} ms`;
  return `${value.toFixed(2)} ms`;
}

export function formatCount(value: number): string {
  return value.toLocaleString("ru-RU");
}

export function shapeKey(width: number, context: number): string {
  return `${width}×${context}`;
}
