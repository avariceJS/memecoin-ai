import { useEffect, useId, useState } from "react";
import { NavLink } from "react-router";
import { Lock } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { navGroups, navItems, type NavItem } from "./navConfig";
import styles from "./Sidebar.module.css";

function useNarrowRail(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 768px)");
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return narrow;
}

function BrandMark() {
  return (
    <svg className={styles.mark} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <circle cx="12" cy="12" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="12" cy="12" r="2.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M12 3.2v2.15M12 18.65v2.15M3.2 12h2.15M18.65 12h2.15"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function NavEntry({ item, narrow }: { item: NavItem; narrow: boolean }) {
  const Icon = item.icon;
  const link = (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      aria-label={item.label}
      className={({ isActive }) =>
        [styles.link, item.locked ? styles.linkLocked : "", isActive ? styles.linkActive : ""]
          .filter(Boolean)
          .join(" ")
      }
    >
      <span className={styles.iconWrap}>
        <Icon className={styles.icon} size={20} strokeWidth={1.75} aria-hidden />
        {item.locked && item.to !== "/trading" ? (
          <Lock className={styles.lock} size={11} strokeWidth={2.25} aria-hidden />
        ) : null}
      </span>
      <span className={styles.label} title={item.label}>
        {item.label}
      </span>
    </NavLink>
  );

  if (!narrow) return link;

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{link}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className={styles.tooltip} side="right" sideOffset={8}>
          {item.label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function Sidebar() {
  const narrow = useNarrowRail();
  const labelPrefix = useId();

  return (
    <aside className={styles.rail} aria-label="Навигация Observatory" data-cc-region="sidebar">
      <div className={styles.brand}>
        <BrandMark />
        <div className={styles.brandText}>
          <p className={styles.brandTitle}>Memecoin AI</p>
          <p className={styles.brandSub}>Observatory</p>
        </div>
      </div>
      <Tooltip.Provider delayDuration={280}>
        <nav className={styles.nav} aria-label="Разделы">
          {navGroups.map((group) => {
            const labelId = `${labelPrefix}-${group.id}`;
            const items = navItems.filter((item) => item.group === group.id);
            return (
              <div key={group.id} className={styles.group} role="group" aria-labelledby={labelId}>
                <p id={labelId} className={styles.groupLabel}>
                  {group.label}
                </p>
                {items.map((item) => (
                  <NavEntry key={item.to} item={item} narrow={narrow} />
                ))}
              </div>
            );
          })}
        </nav>
      </Tooltip.Provider>
    </aside>
  );
}
