import type { Snapshot } from "../../api";

const BLOCKER_LABELS: Record<string, string> = {
  account_plan_and_zero_dollar_hard_cap_unverified:
    "План аккаунта и жёсткий нулевой лимит не проверены",
  runtime_credential_to_billing_account_mapping_unverified:
    "Соответствие runtime-учётных данных и billing-аккаунта не проверено",
  calendar_service_and_wake_uptime_unqualified:
    "Календарный сервис и время бодрствования не квалифицированы",
  full_live_window_not_qualified: "Полное live-окно не квалифицировано",
  separate_activation_decision_absent: "Отдельное решение об активации отсутствует",
};

const VERDICT_LABELS: Record<string, string> = {
  NOT_READY_FOR_ACTIVATION: "Не готово к активации",
};

const QUALIFICATION_LABELS: Record<string, string> = {
  BOUNDED_PROBE_ONLY: "Ограниченный probe",
};

const POLICY_LABELS: Record<string, string> = {
  REVIEWED_PROJECTION_ONLY: "Проверенная проекция",
};

export function blockerLabel(code: string): string {
  return BLOCKER_LABELS[code] ?? "Подробности недоступны";
}

export function verdictLabel(code: string): string {
  return VERDICT_LABELS[code] ?? "Подробности недоступны";
}

export function qualificationLabel(code: string): string {
  return QUALIFICATION_LABELS[code] ?? "Подробности недоступны";
}

export function policyLabel(policy: string): string {
  return POLICY_LABELS[policy] ?? "Подробности недоступны";
}

export function shortenPoolId(poolId: string): string {
  if (poolId.length <= 12) return poolId;
  return `${poolId.slice(0, 4)}…${poolId.slice(-4)}`;
}

export function observationLine(snapshot: Snapshot, sourceIds: readonly string[]): string {
  const dates = new Set<string>();
  const missing: string[] = [];
  for (const id of sourceIds) {
    const stamp = snapshot.sources[id];
    if (!stamp) {
      missing.push(id);
      continue;
    }
    dates.add(stamp.evidenceDate);
  }
  const listed = [...dates].sort();
  const dateText =
    listed.length > 0
      ? `Дата наблюдения ${listed.join(", ")}.`
      : "Дата наблюдения не опубликована.";
  const missingText =
    missing.length > 0 ? " Для части идентификаторов квалификации штамп не опубликован." : "";
  return `${dateText}${missingText}`;
}

export function probeScale(values: readonly number[]): { max: number; scalable: boolean } {
  const scalable = values.length > 0 && values.every((value) => Number.isFinite(value) && value >= 0);
  const max = scalable ? Math.max(...values) : 0;
  return { max, scalable: scalable && max > 0 };
}
