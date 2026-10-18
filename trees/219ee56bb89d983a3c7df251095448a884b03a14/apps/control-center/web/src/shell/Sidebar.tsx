import { NavLink } from "react-router";
import { navItems } from "./navConfig";
import styles from "./Sidebar.module.css";

export function Sidebar() {
  const primary = navItems.filter((item) => item.group === "primary");
  const locked = navItems.filter((item) => item.group === "locked");

  return (
    <aside className={styles.rail} aria-label="Навигация Observatory">
      <div className={styles.brand}>
        <p className={styles.brandTitle}>Observatory</p>
        <p className={styles.brandSub}>Control Center</p>
      </div>
      <nav className={styles.nav}>
        {primary.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              [styles.link, isActive ? styles.linkActive : ""].filter(Boolean).join(" ")
            }
            aria-label={item.label}
          >
            <span className={styles.dot} aria-hidden />
            <span className={styles.labelFull}>{item.label}</span>
          </NavLink>
        ))}
        <p className={styles.groupLabel}>Недоступно</p>
        {locked.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              [styles.link, styles.linkLocked, isActive ? styles.linkActive : ""]
                .filter(Boolean)
                .join(" ")
            }
            aria-label={item.label}
          >
            <span className={styles.dot} aria-hidden />
            <span className={styles.labelFull}>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
