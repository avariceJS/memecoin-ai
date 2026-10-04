import {
  CalendarRange,
  Cpu,
  Database,
  FlaskConical,
  History,
  LayoutDashboard,
  Lock,
  Server,
  type LucideIcon,
} from "lucide-react";

export type NavGroupId = "evidence" | "research" | "system" | "future";

export type NavItem = {
  to: string;
  label: string;
  group: NavGroupId;
  icon: LucideIcon;
  locked?: boolean;
};

export const navGroups: { id: NavGroupId; label: string }[] = [
  { id: "evidence", label: "Свидетельства" },
  { id: "research", label: "Исследование" },
  { id: "system", label: "Система" },
  { id: "future", label: "Дальше" },
];

export const navItems: NavItem[] = [
  { to: "/", label: "Обзор", group: "evidence", icon: LayoutDashboard },
  { to: "/calendar", label: "Календарь", group: "evidence", icon: CalendarRange },
  { to: "/sources", label: "Источники", group: "evidence", icon: Database },
  { to: "/research", label: "Исследование", group: "research", icon: FlaskConical },
  { to: "/models", label: "Модели", group: "research", icon: Cpu },
  { to: "/system", label: "Система", group: "system", icon: Server },
  { to: "/replay", label: "Replay", group: "future", icon: History, locked: true },
  { to: "/trading", label: "Торговля", group: "future", icon: Lock, locked: true },
];
