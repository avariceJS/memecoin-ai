export const DESIGN_ROLE_NODES = [
  {
    id: "ui-adapter",
    title: "UI-adapter",
    note: "HTTP snapshot и capabilities. Не менеджер процессов core.",
  },
  {
    id: "published-projection",
    title: "Опубликованная проекция",
    note: "Проверенный bundle. Не live telemetry.",
  },
  {
    id: "core-runtime",
    title: "Core runtime",
    note: "Pane system без hardware и service feed.",
  },
  {
    id: "execution-surfaces",
    title: "Контуры исполнения",
    note: "Paper, live и shadow — только контракт возможностей.",
  },
] as const;
