import { Lock } from "lucide-react";
import type { MarkKind } from "./viewModel";
import styles from "./research.module.css";

const hex =
  "12,3 19.8,7.5 19.8,16.5 12,21 4.2,16.5 4.2,7.5";
const hexInner = "12,6.2 17.2,9.1 17.2,14.9 12,17.8 6.8,14.9 6.8,9.1";

export function StageMark({ kind }: { kind: MarkKind }) {
  return (
    <svg viewBox="0 0 24 24" className={styles.mark} aria-hidden="true">
      {kind === "diamond-solid" ? (
        <path d="M12 2.2 21.8 12 12 21.8 2.2 12 Z" fill="currentColor" />
      ) : null}
      {kind === "diamond-hollow" ? (
        <path
          d="M12 3 20.8 12 12 21 3.2 12 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
        />
      ) : null}
      {kind === "hex-dashed" ? (
        <polygon
          points={hex}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeDasharray="3 2"
          strokeLinejoin="round"
        />
      ) : null}
      {kind === "hex-double" ? (
        <>
          <polygon points={hex} fill="none" stroke="currentColor" strokeWidth="1.6" />
          <polygon points={hexInner} fill="none" stroke="currentColor" strokeWidth="1.2" />
        </>
      ) : null}
      {kind === "square-gap" ? (
        <rect
          x="3.5"
          y="3.5"
          width="17"
          height="17"
          rx="1.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeDasharray="10 6"
          strokeLinejoin="round"
        />
      ) : null}
      {kind === "square-dot" ? (
        <>
          <rect
            x="3.5"
            y="3.5"
            width="17"
            height="17"
            rx="1.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          />
          <circle cx="12" cy="12" r="2.2" fill="currentColor" />
        </>
      ) : null}
      {kind === "unknown-ring" ? (
        <>
          <circle
            cx="12"
            cy="12"
            r="8"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeDasharray="2 2.4"
          />
          <text
            x="12"
            y="16"
            textAnchor="middle"
            fontSize="11"
            fontWeight="600"
            fill="currentColor"
          >
            ?
          </text>
        </>
      ) : null}
    </svg>
  );
}

export function LockMark() {
  return <Lock className={styles.mark} size={28} strokeWidth={1.75} aria-hidden />;
}
