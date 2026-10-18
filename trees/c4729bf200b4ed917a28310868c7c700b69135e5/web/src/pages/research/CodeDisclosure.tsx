import { useId, useState } from "react";
import styles from "./research.module.css";

type CodeDisclosureProps = {
  label: string;
  codes: string[];
  scrollId: string;
};

export function CodeDisclosure({ label, codes, scrollId }: CodeDisclosureProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (codes.length === 0) return null;

  return (
    <div className={styles.disclosureBlock}>
      <button
        type="button"
        className={styles.disclosure}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        {label}
      </button>
      {open ? (
        <ul
          id={panelId}
          className={styles.codeScroll}
          data-cc-scroll={scrollId}
          tabIndex={0}
          aria-label={label}
        >
          {codes.map((code, index) => (
            <li key={`${code}-${index}`}>{code}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
