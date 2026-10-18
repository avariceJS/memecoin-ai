import { useState, type KeyboardEvent } from "react";
import { Panel } from "../../components";
import { UnavailableState } from "../../components/status";
import { statusLabel } from "../calendar/labels";
import { shortUtc } from "./format";
import type { D43Marker } from "./selectors";
import styles from "./overview.module.css";

type D43TimelineProps = {
  markers: D43Marker[] | null;
  unavailableReason: string | null;
};

function toneClass(marker: D43Marker): string {
  if (marker.access === "PROTECTED") return styles.toneProtected;
  if (marker.recordedStatus === "completed") return styles.toneOk;
  if (marker.recordedStatus === "pending") return styles.toneWait;
  return styles.toneBad;
}

function markerName(marker: D43Marker): string {
  if (marker.access === "PROTECTED") {
    return `${marker.id}, PROTECTED, доступ закрыт. Маркер plannedAt, длительность не показана.`;
  }
  const status = marker.recordedStatus ? statusLabel(marker.recordedStatus) : "статус не опубликован";
  const start = marker.actualStartNs ? "старт опубликован" : "старт не опубликован";
  return `${marker.id}, DEVELOPMENT, ${status}, ${start}. Маркер plannedAt, длительность не показана.`;
}

function placeOf(index: number): "above" | "below" {
  return index % 2 === 0 ? "below" : "above";
}

export function D43Timeline({ markers, unavailableReason }: D43TimelineProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);

  if (!markers) {
    return <UnavailableState reasonCode={unavailableReason ?? "CALENDAR_UNAVAILABLE"} />;
  }

  const selected = markers.find((marker) => marker.index === selectedIndex) ?? markers[0] ?? null;
  const first = markers[0];
  const last = markers[markers.length - 1];
  const mid =
    first && last
      ? new Date((new Date(first.plannedAt).getTime() + new Date(last.plannedAt).getTime()) / 2).toISOString()
      : null;

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    if (!markers) return;
    const position = markers.findIndex((marker) => marker.index === index);
    const next = markers[position + (event.key === "ArrowRight" ? 1 : -1)];
    if (!next) return;
    setSelectedIndex(next.index);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`[data-marker-index="${next.index}"]`)
      ?.focus();
  }

  return (
    <Panel title="Окна D43" className={styles.heroPanel}>
      <div data-cc-region="overview-chart" className={styles.plotFrame}>
      <p className={styles.question}>
        {markers.length} маркеров по plannedAt. Ось не рынок и не длительность.
      </p>
      <div className={styles.plot}>
        {first && last && mid ? (
          <div className={styles.axis} aria-hidden="true">
            <span className={`${styles.tick} ${styles.tickStart}`} style={{ left: "12%" }}>
              {shortUtc(first.plannedAt)}
            </span>
            <span className={`${styles.tick} ${styles.tickMid}`} style={{ left: "50%" }}>
              {shortUtc(mid)}
            </span>
            <span className={`${styles.tick} ${styles.tickEnd}`} style={{ left: "88%" }}>
              {shortUtc(last.plannedAt)}
            </span>
          </div>
        ) : null}
        <div className={styles.lane} role="group" aria-label="Маркеры окон D43">
          <div className={styles.axisLine} />
          {markers.map((marker) => {
            const place = placeOf(marker.index);
            const caption = <span className={styles.caption}>{marker.id}</span>;
            const stem = <span className={styles.stem} aria-hidden="true" />;
            return (
              <button
                key={marker.id}
                type="button"
                data-marker-index={marker.index}
                className={styles.marker}
                style={{ left: `calc(12% + ${marker.position} * 76%)` }}
                aria-pressed={selected?.index === marker.index}
                aria-label={markerName(marker)}
                onClick={() => setSelectedIndex(marker.index)}
                onKeyDown={(event) => onKeyDown(event, marker.index)}
              >
                <span className={styles.upper}>
                  {place === "above" ? caption : null}
                  {place === "above" ? stem : null}
                </span>
                <span className={`${styles.dot} ${toneClass(marker)}`} />
                <span className={styles.lower}>
                  {place === "below" ? stem : null}
                  {place === "below" ? caption : null}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      </div>
      <ul className={styles.legend}>
        <li className={styles.legendItem}>
          <span className={`${styles.swatch} ${styles.toneOk}`} /> завершено
        </li>
        <li className={styles.legendItem}>
          <span className={`${styles.swatch} ${styles.toneWait}`} /> ожидание
        </li>
        <li className={styles.legendItem}>
          <span className={`${styles.swatch} ${styles.toneProtected}`} /> доступ закрыт
        </li>
      </ul>
    </Panel>
  );
}
