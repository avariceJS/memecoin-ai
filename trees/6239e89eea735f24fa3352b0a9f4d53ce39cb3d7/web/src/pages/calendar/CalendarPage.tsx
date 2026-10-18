import { useMemo, useState } from "react";
import { useOutletContext } from "react-router";
import type { windowSchema } from "../../api/schema";
import { QualificationBadge } from "../../components/status/QualificationBadge";
import { UnavailableState } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import styles from "./CalendarPage.module.css";
import {
  exitCodeLabel,
  statusLabel,
  statusTone,
  unpublishedCounter,
  type DevelopmentWindow,
} from "./labels";
import {
  formatAxisTick,
  formatEpochNs,
  formatIsoInstant,
  plannedAtMs,
  TIME_ZONE_LABELS,
  type CalendarTimeZone,
} from "./timeZone";
import { CalendarCorpusSections } from "../corpus";
import type { z } from "zod";

type Window = z.infer<typeof windowSchema>;

function fractionOf(ms: number, minMs: number, spanMs: number): number {
  if (spanMs <= 0) return 0;
  return Math.min(1, Math.max(0, (ms - minMs) / spanMs));
}

function markerLeft(fraction: number): string {
  return `calc(8% + ${fraction * 84}%)`;
}

function publishedDuration(
  window: Window,
  minMs: number,
  spanMs: number,
): { left: string; width: string } | null {
  if (window.access !== "DEVELOPMENT" || !window.actualStartNs || !window.finishedNs) {
    return null;
  }
  const startMs = Number(BigInt(window.actualStartNs) / 1_000_000n);
  const endMs = Number(BigInt(window.finishedNs) / 1_000_000n);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return null;
  const start = fractionOf(startMs, minMs, spanMs);
  const end = fractionOf(endMs, minMs, spanMs);
  if (end - start <= 0) return null;
  return {
    left: markerLeft(start),
    width: `${(end - start) * 84}%`,
  };
}

export function WindowDetail({
  window: w,
  zone,
}: {
  window: DevelopmentWindow;
  zone: CalendarTimeZone;
}) {
  return (
    <dl className={styles.detailDl}>
      <div className={styles.detailRow}>
        <dt>Окно</dt>
        <dd>
          #{w.index} · <span className={styles.mono}>{w.id}</span>
        </dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Статус</dt>
        <dd className={toneClass(statusTone(w.recordedStatus))}>{statusLabel(w.recordedStatus)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>План (plannedAt)</dt>
        <dd>{formatIsoInstant(w.plannedAt, zone)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Фактический старт</dt>
        <dd>
          {w.actualStartNs
            ? formatEpochNs(w.actualStartNs, zone)
            : "старт не опубликован"}
        </dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Завершение (finishedNs)</dt>
        <dd>
          {w.finishedNs ? formatEpochNs(w.finishedNs, zone) : "не опубликовано"}
        </dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Первый receipt</dt>
        <dd>
          {w.firstReceiptAt
            ? formatIsoInstant(w.firstReceiptAt, zone)
            : "не опубликовано"}
        </dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Последний receipt</dt>
        <dd>
          {w.lastReceiptAt
            ? formatIsoInstant(w.lastReceiptAt, zone)
            : "не опубликовано"}
        </dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Snapshots</dt>
        <dd>
          {unpublishedCounter(w.snapshots)}
          {w.recordedStatus === "pending" ? ` (план ${w.plannedSnapshots})` : null}
        </dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Usable</dt>
        <dd>{unpublishedCounter(w.usable)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Mature decisions</dt>
        <dd>{unpublishedCounter(w.matureDecisions)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Scenarios</dt>
        <dd>{unpublishedCounter(w.scenarios)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Corpus exit</dt>
        <dd>{exitCodeLabel("corpus", w.corpusExitCode)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>QA exit</dt>
        <dd>{exitCodeLabel("qa", w.qaExitCode)}</dd>
      </div>
      <div className={styles.detailRow}>
        <dt>Execution / collection exit</dt>
        <dd>{exitCodeLabel("execution", w.executionOrCollectionExitCode)}</dd>
      </div>
      {w.recordedSealSha256 ? (
        <div className={styles.detailRow}>
          <dt>Recorded seal</dt>
          <dd className={styles.mono}>{w.recordedSealSha256}</dd>
        </div>
      ) : null}
    </dl>
  );
}

function toneClass(tone: "ok" | "wait" | "bad"): string {
  if (tone === "ok") return styles.statusOk;
  if (tone === "wait") return styles.statusWait;
  return styles.statusBad;
}

function markerName(window: Window): string {
  if (window.access === "PROTECTED") {
    return `#${window.index} ${window.id}, доступ закрыт`;
  }
  const start = window.actualStartNs ? "старт опубликован" : "старт не опубликован";
  return `#${window.index} ${window.id}, ${statusLabel(window.recordedStatus)}, ${start}`;
}

function dotClass(window: Window): string {
  if (window.access === "PROTECTED") return styles.dotProtected;
  const tone = statusTone(window.recordedStatus);
  if (tone === "ok") return styles.dotOk;
  if (tone === "wait") return styles.dotWait;
  return styles.dotBad;
}

export function CalendarPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const calendarPane = snapshotResponse.data.calendar;
  const [zone, setZone] = useState<CalendarTimeZone>("UTC");
  const [selectedIndex, setSelectedIndex] = useState<number>(0);

  const windows = useMemo(() => {
    if (calendarPane.availability !== "AVAILABLE") return [] as Window[];
    return calendarPane.data.windows;
  }, [calendarPane]);

  const timeSpan = useMemo(() => {
    if (windows.length === 0) return { minMs: 0, spanMs: 1 };
    const times = windows.map((w) => plannedAtMs(w.plannedAt));
    const minMs = Math.min(...times);
    const maxMs = Math.max(...times);
    return { minMs, spanMs: Math.max(maxMs - minMs, 1) };
  }, [windows]);

  const selected = useMemo(
    () => windows.find((win) => win.index === selectedIndex) ?? null,
    [windows, selectedIndex],
  );
  const selectedDev = selected && selected.access === "DEVELOPMENT" ? selected : null;
  const selectedProtected = selected && selected.access === "PROTECTED" ? selected : null;

  if (calendarPane.availability !== "AVAILABLE") {
    return (
      <UnavailableState reasonCode={calendarPane.reasonCode ?? "CALENDAR_UNAVAILABLE"} />
    );
  }

  const holdout = calendarPane.data;

  const axisStart = timeSpan.minMs;
  const axisMid = timeSpan.minMs + timeSpan.spanMs / 2;
  const axisEnd = timeSpan.minMs + timeSpan.spanMs;

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <div className={styles.badgeRow}>
          <QualificationBadge kind="REAL EVIDENCE" />
          <span className={styles.holdoutNote}>ENGINEERING_CORPUS</span>
          <QualificationBadge kind="ENGINEERING" />
          <span className={styles.holdoutNote}>
            qualification {calendarPane.qualification} · holdout{" "}
            {formatEpochNs(holdout.holdoutStartNs, zone)} —{" "}
            {formatEpochNs(holdout.holdoutEndNs, zone)} ({TIME_ZONE_LABELS[zone]})
          </span>
        </div>
        <div
          className={styles.tzSwitch}
          role="group"
          aria-label="Часовой пояс календаря"
        >
          {(["UTC", "Europe/Moscow"] as const).map((tz) => (
            <button
              key={tz}
              type="button"
              className={styles.tzBtn}
              aria-pressed={zone === tz}
              onClick={() => setZone(tz)}
            >
              {TIME_ZONE_LABELS[tz]}
            </button>
          ))}
        </div>
      </header>

      <CalendarCorpusSections snapshot={snapshotResponse.data} />

      <div className={styles.mainGrid}>
        <section className={styles.timelineWrap} aria-labelledby="timeline-heading">
          <h2 id="timeline-heading" className={styles.panelTitle}>
            D43 · маркеры ({TIME_ZONE_LABELS[zone]})
          </h2>
          <p className={styles.question}>
            Маркеры по plannedAt. Полоса длительности — только если опубликованы старт и
            завершение.
          </p>
          <div className={styles.plot}>
            <div className={styles.axis} aria-hidden="true">
              <span>{formatAxisTick(axisStart, zone)}</span>
              <span>{formatAxisTick(axisMid, zone)}</span>
              <span>{formatAxisTick(axisEnd, zone)}</span>
            </div>
            <div className={styles.markerLane} role="group" aria-label="Маркеры D43 по plannedAt">
              <div className={styles.axisLine} />
              {windows.map((w) => {
                const duration = publishedDuration(w, timeSpan.minMs, timeSpan.spanMs);
                return duration ? (
                  <span
                    key={`${w.id}-duration`}
                    className={styles.duration}
                    data-duration="true"
                    style={{ left: duration.left, width: duration.width }}
                  />
                ) : null;
              })}
              {windows.map((w) => {
                const left = markerLeft(
                  fractionOf(plannedAtMs(w.plannedAt), timeSpan.minMs, timeSpan.spanMs),
                );
                const above = w.index % 2 === 1;
                return (
                  <button
                    key={w.id}
                    type="button"
                    className={styles.marker}
                    style={{ left }}
                    aria-pressed={selectedIndex === w.index}
                    aria-label={markerName(w)}
                    data-testid={
                      w.access === "PROTECTED" ? `protected-row-${w.index}` : `d43-marker-${w.index}`
                    }
                    onClick={() => setSelectedIndex(w.index)}
                  >
                    <span className={styles.capSlot}>{above ? `#${w.index}` : ""}</span>
                    <span className={`${styles.dot} ${dotClass(w)}`} />
                    <span className={styles.capSlot}>{above ? "" : `#${w.index}`}</span>
                  </button>
                );
              })}
            </div>
            <ul className={styles.markerLegend}>
              <li>
                <span className={`${styles.legendDot} ${styles.dotOk}`} /> завершено
              </li>
              <li>
                <span className={`${styles.legendDot} ${styles.dotWait}`} /> ожидание
              </li>
              <li>
                <span className={`${styles.legendDot} ${styles.dotBad}`} /> ошибка
              </li>
              <li>
                <span className={`${styles.legendDot} ${styles.dotProtected}`} /> доступ закрыт
              </li>
            </ul>
          </div>
        </section>

        <aside
          className={styles.detailPanel}
          data-cc-region="window-details"
          aria-labelledby="detail-heading"
          role="complementary"
        >
          <h2 id="detail-heading" className={styles.panelTitle}>
            Детали окна
          </h2>
          {selectedDev ? (
            <WindowDetail window={selectedDev} zone={zone} />
          ) : selectedProtected ? (
            <dl className={styles.detailDl}>
              <div className={styles.detailRow}>
                <dt>Окно</dt>
                <dd>
                  #{selectedProtected.index} ·{" "}
                  <span className={styles.mono}>{selectedProtected.id}</span>
                </dd>
              </div>
              <div className={styles.detailRow}>
                <dt>План (plannedAt)</dt>
                <dd>{formatIsoInstant(selectedProtected.plannedAt, zone)}</dd>
              </div>
              <div className={styles.detailRow}>
                <dt>Доступ</dt>
                <dd>доступ закрыт</dd>
              </div>
            </dl>
          ) : (
            <p className={styles.detailEmpty}>
              Выберите окно на оси. Protected-окна не раскрывают метрики.
            </p>
          )}
        </aside>
      </div>

      <section className={styles.tablePanel} aria-labelledby="table-heading">
        <h2 id="table-heading" className={styles.panelTitle}>
          Сводка окон (recorded)
        </h2>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">id</th>
              <th scope="col">plannedAt</th>
              <th scope="col">access</th>
              <th scope="col">status</th>
              <th scope="col">snapshots</th>
              <th scope="col">usable</th>
              <th scope="col">mature</th>
            </tr>
          </thead>
          <tbody>
            {windows.map((w) => {
              if (w.access === "PROTECTED") {
                return (
                  <tr key={w.id} data-testid={`protected-table-${w.index}`}>
                    <td>{w.index}</td>
                    <td className={styles.mono}>{w.id}</td>
                    <td>{formatIsoInstant(w.plannedAt, zone)}</td>
                    <td>PROTECTED</td>
                    <td colSpan={4}>
                      <span role="status">доступ закрыт</span>
                    </td>
                  </tr>
                );
              }

              const tone = statusTone(w.recordedStatus);
              return (
                <tr key={w.id}>
                  <td>{w.index}</td>
                  <td className={styles.mono}>{w.id}</td>
                  <td>{formatIsoInstant(w.plannedAt, zone)}</td>
                  <td>DEVELOPMENT</td>
                  <td className={toneClass(tone)}>{statusLabel(w.recordedStatus)}</td>
                  <td>
                    {unpublishedCounter(w.snapshots)}
                    {w.recordedStatus === "pending"
                      ? ` / план ${w.plannedSnapshots}`
                      : ""}
                  </td>
                  <td>{unpublishedCounter(w.usable)}</td>
                  <td>{unpublishedCounter(w.matureDecisions)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
