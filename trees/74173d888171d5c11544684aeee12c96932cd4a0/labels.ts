export type DevelopmentWindow = {
  access: "DEVELOPMENT";
  id: string;
  index: number;
  plannedAt: string;
  recordedStatus: "completed" | "pending" | "failed" | "missed";
  actualStartNs: string | null;
  finishedNs: string | null;
  firstReceiptAt: string | null;
  lastReceiptAt: string | null;
  snapshots: number | null;
  plannedSnapshots: number;
  usable: number | null;
  matureDecisions: number | null;
  scenarios: number | null;
  corpusExitCode: number | null;
  qaExitCode: number | null;
  executionOrCollectionExitCode: number | null;
  recordedSealSha256: string | null;
};

export function statusLabel(status: DevelopmentWindow["recordedStatus"]): string {
  switch (status) {
    case "completed":
      return "завершено";
    case "pending":
      return "ожидание";
    case "failed":
      return "ошибка";
    case "missed":
      return "пропущено";
    default:
      return status;
  }
}

export function statusTone(status: DevelopmentWindow["recordedStatus"]): "ok" | "wait" | "bad" {
  switch (status) {
    case "completed":
      return "ok";
    case "pending":
      return "wait";
    default:
      return "bad";
  }
}

export function unpublishedCounter(value: number | null): string {
  return value === null ? "не опубликовано" : String(value);
}

export function exitCodeLabel(kind: string, code: number | null): string {
  if (code === null) {
    return `${kind}: не опубликовано`;
  }
  const outcome = code === 0 ? "успех" : `код ${code} (неуспех)`;
  return `${kind}: ${outcome}`;
}
