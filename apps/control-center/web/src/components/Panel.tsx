import { useId, type ReactNode } from "react";
import styles from "./Panel.module.css";

type PanelProps = {
  children: ReactNode;
  title?: string;
  density?: "default" | "inline" | "compact";
  className?: string;
  label?: string;
};

export function Panel({ children, title, density = "default", className, label }: PanelProps) {
  const titleId = useId();
  const classes = [
    styles.panel,
    density === "inline" ? styles.inline : "",
    density === "compact" ? styles.compact : "",
    className,
  ]
    .filter((value) => Boolean(value))
    .join(" ");

  return (
    <section
      className={classes}
      aria-label={title ? undefined : label}
      aria-labelledby={title ? titleId : undefined}
    >
      {title ? (
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
      ) : null}
      {children}
    </section>
  );
}
