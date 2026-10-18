import type { Capability } from "../../api";

export type CapabilityTone = "recorded" | "unavailable" | "locked";

export type CapabilityCardModel = {
  id: string;
  label: string;
  tone: CapabilityTone;
  code: string;
  summary: string;
};

const READ_ORDER = [
  "calendar",
  "corpus",
  "research",
  "models",
  "sourceQualification",
] as const;

const CLOSED_ORDER = [
  "marketFeed",
  "predictions",
  "trainingControl",
  "collectorControl",
  "portfolio",
  "paper",
  "shadow",
  "live",
  "emergencyStop",
  "hardwareLive",
  "rawLogs",
  "replay",
] as const;

const READ_IDS = new Set<string>(READ_ORDER);

const LABELS: Record<string, string> = {
  calendar: "Календарь",
  corpus: "Корпус",
  research: "Исследование",
  models: "Модели",
  sourceQualification: "Квалификация источников",
  marketFeed: "Рыночный поток",
  predictions: "Прогнозы",
  trainingControl: "Обучение",
  collectorControl: "Сбор",
  portfolio: "Портфель",
  paper: "Paper",
  shadow: "Shadow",
  live: "Live",
  emergencyStop: "Аварийная остановка",
  hardwareLive: "Аппаратный контур",
  rawLogs: "Сырые логи",
  replay: "Replay",
};

const READ_SUMMARY: Record<string, string> = {
  calendar: "Записанный календарь открыт только для чтения.",
  corpus: "Записанный корпус открыт только для чтения.",
  research: "Записанное исследование открыто только для чтения.",
  models: "Инженерные прогоны открыты только для чтения.",
  sourceQualification: "Записанная квалификация источников открыта только для чтения.",
};

const CODE_SUMMARY: Record<string, string> = {
  NO_APPROVED_CORE_READ_OR_CONTROL_CONTRACT: "Нет утверждённого контракта чтения или управления.",
  NO_CURRENT_SERVICE_OR_HARDWARE_FEED: "Нет текущего service или hardware feed.",
  NO_APPROVED_TRADING_CONTRACT: "Нет утверждённого торгового контракта.",
  CAPABILITY_NOT_PUBLISHED: "В snapshot нет этой возможности.",
  READ_ONLY_RECORDED: "Чтение записанной проекции. Это не запущенный процесс.",
  SNAPSHOT_QUERY_FAILED: "Запрос snapshot не подтвердил adapter.",
};

function rank(id: string): number {
  const readIndex = (READ_ORDER as readonly string[]).indexOf(id);
  if (readIndex >= 0) return readIndex;
  const closedIndex = (CLOSED_ORDER as readonly string[]).indexOf(id);
  if (closedIndex >= 0) return READ_ORDER.length + closedIndex;
  return READ_ORDER.length + CLOSED_ORDER.length;
}

function toCard(cap: Capability): CapabilityCardModel {
  const id = cap.id;
  const label = LABELS[id] ?? id;
  if (cap.availability === "UNAVAILABLE") {
    const code = cap.reasonCode;
    return {
      id,
      label,
      tone: "unavailable",
      code,
      summary: CODE_SUMMARY[code] ?? "Код контракта опубликован в прокрутке.",
    };
  }
  if (READ_IDS.has(id)) {
    return {
      id,
      label,
      tone: "recorded",
      code: cap.mode,
      summary: READ_SUMMARY[id] ?? "Чтение записанной проекции. Это не запущенный процесс.",
    };
  }
  return {
    id,
    label,
    tone: "locked",
    code: cap.mode,
    summary: "Опубликовано только чтение записи. Запуск и управление не открываются.",
  };
}

export function capabilityCards(capabilities: Capability[]): CapabilityCardModel[] {
  const byId = new Map<string, Capability>();
  for (const capability of capabilities) {
    byId.set(capability.id, capability);
  }
  return [...byId.values()]
    .sort((left, right) => rank(left.id) - rank(right.id) || left.id.localeCompare(right.id))
    .map(toCard);
}

export function capabilityTally(cards: CapabilityCardModel[]): string {
  if (cards.length === 0) return "Возможностей в контракте: 0";
  const recorded = cards.filter((card) => card.tone === "recorded").length;
  const unavailable = cards.filter((card) => card.tone === "unavailable").length;
  const locked = cards.filter((card) => card.tone === "locked").length;
  const parts = [`Чтение записи: ${recorded}`, `Недоступно: ${unavailable}`];
  if (locked > 0) parts.push(`Закрыто: ${locked}`);
  return parts.join(" · ");
}

export function recordedStageCaption(qualification: string): string {
  const known: Record<string, string> = {
    BOUNDED_PROBE_ONLY: "ограниченный probe",
    ENGINEERING_CORPUS: "инженерный корпус",
    ENGINEERING_ONLY: "инженерная квалификация",
    FROZEN_DESIGN_NOT_ADMISSION: "замороженный design",
  };
  const kind = known[qualification] ?? "опубликованная квалификация";
  return `Зафиксированный прогон: ${kind}. Не текущий процесс и не live health.`;
}
