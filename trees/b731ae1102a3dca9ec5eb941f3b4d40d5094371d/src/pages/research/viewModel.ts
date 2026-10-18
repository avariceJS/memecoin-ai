import type { Snapshot } from "../../api";

export type ResearchData = Extract<Snapshot["research"], { availability: "AVAILABLE" }>["data"];

export type MarkKind =
  | "diamond-solid"
  | "diamond-hollow"
  | "hex-dashed"
  | "hex-double"
  | "square-gap"
  | "square-dot"
  | "unknown-ring";

export type ReviewItem = { code: string; label: string };

export type ReviewGroup = { id: string; title: string; items: ReviewItem[] };

export type CorpusStageView = {
  mark: MarkKind;
  label: string;
  summary: string;
  badge: "ENGINEERING" | "UNAVAILABLE" | null;
  codes: string[];
};

const GROUP_ORDER = ["sampling", "target", "runner", "admission", "other"] as const;

const KNOWN_REVIEWS: Record<string, { group: (typeof GROUP_ORDER)[number]; title: string; label: string }> =
  {
    prospective_sampling_binding: {
      group: "sampling",
      title: "Выборка и границы",
      label: "Привязка prospective sampling",
    },
    point_in_time_universe_scope: {
      group: "sampling",
      title: "Выборка и границы",
      label: "Границы вселенной point-in-time",
    },
    conditional_target_semantics: {
      group: "target",
      title: "Цель и точность",
      label: "Семантика условной цели",
    },
    dependence_and_precision_plan: {
      group: "target",
      title: "Цель и точность",
      label: "План зависимости и точности",
    },
    runner_conformance: {
      group: "runner",
      title: "Runner и учёт",
      label: "Соответствие runner",
    },
    exposure_ledger: {
      group: "runner",
      title: "Runner и учёт",
      label: "Журнал exposures",
    },
    formal_forecast_admission: {
      group: "admission",
      title: "Допуск прогноза",
      label: "Формальный допуск прогноза",
    },
  };

export function recordedYesNo(value: boolean): "да" | "нет" {
  return value ? "да" : "нет";
}

export function qualificationCaption(qualification: string): string {
  switch (qualification) {
    case "FROZEN_DESIGN_NOT_ADMISSION":
      return "Замороженный design, не operational admission.";
    case "ENGINEERING_CORPUS":
      return "Инженерный корпус. Это не допуск к исследованию.";
    case "ENGINEERING_ONLY":
      return "Инженерная квалификация. Это не допуск к исследованию.";
    case "BOUNDED_PROBE_ONLY":
      return "Ограниченный probe. Это не допуск к исследованию.";
    default:
      return "Подробности недоступны.";
  }
}

export function groupMissingReviews(codes: string[]): ReviewGroup[] {
  const buckets = new Map<string, ReviewGroup>();
  for (const code of codes) {
    const known = KNOWN_REVIEWS[code];
    const id = known?.group ?? "other";
    const title = known?.title ?? "Прочие коды";
    const label = known?.label ?? "Подробности недоступны";
    const group = buckets.get(id) ?? { id, title, items: [] };
    group.items.push({ code, label });
    buckets.set(id, group);
  }
  return GROUP_ORDER.flatMap((id) => {
    const group = buckets.get(id);
    return group ? [group] : [];
  });
}

export function frozenStage(frozen: boolean): { mark: MarkKind; label: string } {
  return frozen
    ? { mark: "diamond-solid", label: "Зафиксирован" }
    : { mark: "diamond-hollow", label: "Не зафиксирован" };
}

export function numericStage(blockers: number): { mark: MarkKind; label: string } {
  return blockers === 0
    ? { mark: "square-dot", label: "Числовой pass" }
    : { mark: "square-gap", label: "Есть блокеры" };
}

export function corpusStage(snapshot: Snapshot, data: ResearchData): CorpusStageView {
  const corpus = snapshot.corpus;
  const collection = data.sampling.recordedCollectionAuthorized;
  const admitted = data.sampling.admittedWindows;
  if (corpus.availability !== "AVAILABLE") {
    return {
      mark: "unknown-ring",
      label: "Неизвестно",
      summary: "Панель корпуса недоступна. Пустое место не читается как квалификация.",
      badge: "UNAVAILABLE",
      codes: corpus.reasonCode ? [corpus.reasonCode] : [],
    };
  }
  const caption = qualificationCaption(corpus.qualification);
  const collectionLine = `Сбор в записи: ${recordedYesNo(collection)}.`;
  const recordedSignal = collection || admitted > 0;
  return {
    mark: recordedSignal ? "hex-double" : "hex-dashed",
    label: recordedSignal ? "Записанный факт" : "Не квалифицирован",
    summary: recordedSignal
      ? `${caption} ${collectionLine} Допущенные окна: ${admitted}. Это не статус runner.`
      : `${caption} ${collectionLine}`,
    badge:
      corpus.qualification === "ENGINEERING_CORPUS" || corpus.qualification === "ENGINEERING_ONLY"
        ? "ENGINEERING"
        : null,
    codes: [corpus.qualification, corpus.data.researchPermission],
  };
}
