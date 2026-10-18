import { useId, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import styles from "./ReasonDisclosure.module.css";

type ReasonDetail = {
  label: string;
  value: string;
};

type ReasonDisclosureProps = {
  label: string;
  value: string;
  detail?: ReasonDetail | null;
};

function shorten(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

export function ReasonDisclosure({ label, value, detail = null }: ReasonDisclosureProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const skipClick = useRef(false);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    skipClick.current = true;
    setOpen((current) => !current);
    queueMicrotask(() => {
      skipClick.current = false;
    });
  }

  function onClick(event: MouseEvent<HTMLButtonElement>) {
    if (skipClick.current) {
      event.preventDefault();
      return;
    }
    setOpen((current) => !current);
  }

  return (
    <div className={styles.root}>
      <button
        type="button"
        className={styles.trigger}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${label} ${value}`}
        onClick={onClick}
        onKeyDown={onKeyDown}
      >
        {shorten(value)}
      </button>
      {open ? (
        <div id={panelId} className={styles.body}>
          <p className={styles.caption}>{label}</p>
          <p className={styles.value}>{value}</p>
          {detail ? (
            <>
              <p className={styles.caption}>{detail.label}</p>
              <p className={styles.value}>{detail.value}</p>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
