import type { RouteObject } from "react-router";
import { AppShell } from "./shell/AppShell";
import { OverviewPage } from "./pages/OverviewPage";
import { CalendarPage } from "./pages/calendar/CalendarPage";
import {
  ModelsPage,
  ReplayPage,
  SystemPage,
  TradingPage,
} from "./pages/PlaceholderPages";
import { ResearchPage } from "./pages/research";
import { SourcesPage } from "./pages/sources";

export const appRoutes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "calendar", element: <CalendarPage /> },
      { path: "research", element: <ResearchPage /> },
      { path: "models", element: <ModelsPage /> },
      { path: "sources", element: <SourcesPage /> },
      { path: "system", element: <SystemPage /> },
      { path: "replay", element: <ReplayPage /> },
      { path: "trading", element: <TradingPage /> },
    ],
  },
];
