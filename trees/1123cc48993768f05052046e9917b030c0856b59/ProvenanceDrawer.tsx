import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { Snapshot } from "../../api";
import { SourceStampsTable } from "./SourceStampsTable";
import styles from "./sources.module.css";

type ProvenanceDrawerProps = {
  snapshot: Snapshot;
};

const FOCUSABLE = 'button, [href], [tabindex]:not([tabindex="-1"])';

export function ProvenanceDrawer({ snapshot }: ProvenanceDrawerProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const dialogDomId = useId();
  const wasOpen = useRef(false);

  useEffect(() => {
    if (!open) {
      if (wasOpen.current) {
        wasOpen.current = false;
        triggerRef.current?.focus();
      }
      return;
    }
    wasOpen.current = true;
    const dialog = dialogRef.current;
    dialog?.focus();

    function focusable(): HTMLElement[] {
      if (!dialog) return [];
      return [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (element) => !element.hasAttribute("disabled"),
      );
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const active = document.activeElement;
      if (active === dialog) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  function onTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setOpen(true);
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={styles.textButton}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? dialogDomId : undefined}
        onClick={() => setOpen(true)}
        onKeyDown={onTriggerKeyDown}
      >
        Штампы и хеши
      </button>
      {open
        ? createPortal(
            <div
              className={styles.backdrop}
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) setOpen(false);
              }}
            >
              <div
                ref={dialogRef}
                id={dialogDomId}
                className={styles.drawer}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                tabIndex={-1}
              >
                <div className={styles.drawerHead}>
                  <h2 id={titleId} className={styles.drawerTitle}>
                    Штампы проекции
                  </h2>
                  <button type="button" className={styles.textButton} onClick={() => setOpen(false)}>
                    Закрыть
                  </button>
                </div>
                <p className={styles.summary}>
                  PINNED_BYTES — целостность зафиксированных байтов. Это не канал поступления данных.
                  Baseline {snapshot.coreBaselineCommit}.
                </p>
                <SourceStampsTable snapshot={snapshot} />
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
