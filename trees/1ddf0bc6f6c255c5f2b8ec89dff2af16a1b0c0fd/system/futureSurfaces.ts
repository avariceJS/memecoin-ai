/** Future execution / control surfaces shown on the System boundaries page. */
export const FUTURE_SURFACE_IDS = [
  "predictions",
  "paper",
  "live",
  "shadow",
  "emergencyStop",
  "replay",
] as const;

export type FutureSurfaceId = (typeof FUTURE_SURFACE_IDS)[number];

export const DESIGN_ROLE_NODES = [
  {
    id: "ui-adapter",
    title: "UI read-only adapter",
    note: "HTTP snapshot/capabilities; не core process manager.",
  },
  {
    id: "published-projection",
    title: "Published observatory projection",
    note: "Reviewed bundle; не live telemetry feed.",
  },
  {
    id: "core-runtime",
    title: "Core runtime",
    note: "Pane system без hardware/service feed в v1.",
  },
  {
    id: "execution-surfaces",
    title: "Execution surfaces",
    note: "Paper/live/shadow — только capability contract.",
  },
] as const;
