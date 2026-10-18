export type NavItem = {
  to: string;
  label: string;
  shortLabel?: string;
  group: "primary" | "locked";
};

export const navItems: NavItem[] = [
  { to: "/", label: "Обзор", group: "primary" },
  { to: "/calendar", label: "Календарь", group: "primary" },
  { to: "/research", label: "Исследование", group: "primary" },
  { to: "/models", label: "Модели", group: "primary" },
  { to: "/sources", label: "Источники", group: "primary" },
  { to: "/system", label: "Система", group: "primary" },
  { to: "/replay", label: "Replay", group: "locked" },
  { to: "/trading", label: "Торговля", group: "locked" },
];
