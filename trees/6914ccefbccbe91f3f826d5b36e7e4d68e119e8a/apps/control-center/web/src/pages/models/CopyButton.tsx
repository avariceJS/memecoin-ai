import { useEffect, useRef, useState } from "react";
import styles from "./models.module.css";

type CopyButtonProps = {
  value: string;
};

async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error("clipboard_unavailable");
}

export function CopyButton({ value }: CopyButtonProps) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  async function onCopy() {
    try {
      await copyText(value);
      setState("copied");
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setState("idle"), 2000);
    } catch {
      setState("failed");
    }
  }

  const visible = state === "copied" ? "Скопировано" : state === "failed" ? "Не скопировано" : "Копия";
  const action = state === "copied" ? "Скопировано" : state === "failed" ? "Не скопировано" : "Копировать";

  return (
    <button type="button" className={styles.copyBtn} aria-label={`${action} ${value}`} onClick={() => void onCopy()}>
      {visible}
    </button>
  );
}
