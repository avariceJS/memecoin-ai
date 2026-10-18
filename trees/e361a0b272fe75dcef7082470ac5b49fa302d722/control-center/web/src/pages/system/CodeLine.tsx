import styles from "./system.module.css";

type CodeLineProps = {
  scrollId: string;
  label: string;
  value: string;
};

export function CodeLine({ scrollId, label, value }: CodeLineProps) {
  return (
    <div
      className={styles.codeScroll}
      data-cc-scroll={scrollId}
      tabIndex={0}
      aria-label={label}
    >
      {value}
    </div>
  );
}
