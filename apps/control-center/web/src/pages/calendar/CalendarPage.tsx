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
  formatEpochNs,
  formatIsoInstant,
  plannedAtMs,
  TIME_ZONE_LABELS,
  type CalendarTimeZone,
} from "./timeZone";
import type { z } from "zod";

type Window = z.infer<typeof windowSchema>;

function lanePosition(plannedAt: string, minMs: number, spanMs: number): number {
  if (spanMs <= 0) return 0;
  return ((plannedAtMs(plannedAt) - minMs) / spanMs) * 100;
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

function fillClass(tone: "ok" | "wait" | "bad" | "protected"): string {
  if (tone === "ok") return styles.fillOk;
  if (tone === "wait") return styles.fillWait;
  if (tone === "bad") return styles.fillBad;
  return styles.fillProtected;
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

  const selectedDev = useMemo(() => {
    const w = windows.find((win) => win.index === selectedIndex);
    if (!w || w.access !== "DEVELOPMENT") return null;
    return w;
  }, [windows, selectedIndex]);

  if (calendarPane.availability !== "AVAILABLE") {
    return (
      <UnavailableState reasonCode={calendarPane.reasonCode ?? "CALENDAR_UNAVAILABLE"} />
    );
  }

  const holdout = calendarPane.data;

  return (
    <>
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

      <div className={styles.mainGrid}>
        <section className={styles.timelineWrap} aria-labelledby="timeline-heading">
          <h2 id="timeline-heading" className={styles.panelTitle}>
            D43 timeline ({TIME_ZONE_LABELS[zone]})
          </h2>
          <div className={styles.axis} aria-hidden="true">
            {[0, 0.5, 1].map((t) => {
              const ms = timeSpan.minMs + timeSpan.spanMs * t;
              const label = new Intl.DateTimeFormat("ru-RU", {
                timeZone: zone,
                dateStyle: "short",
                timeStyle: "short",
              }).format(new Date(ms));
              return (
                <span
                  key={t}
                  className={styles.axisTick}
                  style={{ left: `${t * 100}%` }}
                >
                  {label}
                </span>
              );
            })}
          </div>
          <div className={styles.laneList}>
            {windows.map((w) => {
              const pos = lanePosition(w.plannedAt, timeSpan.minMs, timeSpan.spanMs);
              if (w.access === "PROTECTED") {
                return (
                  <div
                    key={w.id}
                    className={styles.laneProtected}
                    data-testid={`protected-row-${w.index}`}
                  >
                    <span className={styles.laneIndex}>#{w.index}</span>
                    <div>
                      <div className={styles.mono}>{w.id}</div>
                      <div>{formatIsoInstant(w.plannedAt, zone)}</div>
                      <div>access: PROTECTED</div>
                      <div className={styles.laneTrack} aria-hidden="true">
                        <span
                          className={`${styles.laneFill} ${fillClass("protected")}`}
                          style={{ left: `${Math.min(pos, 98)}%`, width: "8%" }}
                        />
                      </div>
                    </div>
                    <span
                      className={styles.protectedChip}
                      tabIndex={0}
                      role="status"
                    >
                      доступ закрыт
                    </span>
                  </div>
                );
              }

              const tone = statusTone(w.recordedStatus);
              return (
                <button
                  key={w.id}
                  type="button"
                  className={styles.laneBtn}
                  aria-selected={selectedIndex === w.index}
                  onClick={() => setSelectedIndex(w.index)}
                >
                  <span className={styles.laneIndex}>#{w.index}</span>
                  <div>
                    <div className={styles.mono}>{w.id}</div>
                    <div>{formatIsoInstant(w.plannedAt, zone)}</div>
                    <div className={styles.laneTrack} aria-hidden="true">
                      <span
                        className={`${styles.laneFill} ${fillClass(tone)}`}
                        style={{ left: `${Math.min(pos, 98)}%`, width: "8%" }}
                      />
                    </div>
                  </div>
                  <span className={toneClass(tone)}>{statusLabel(w.recordedStatus)}</span>
                </button>
              );
            })}
          </div>
        </section>

        <aside
          className={styles.detailPanel}
          aria-labelledby="detail-heading"
          role="complementary"
        >
          <h2 id="detail-heading" className={styles.panelTitle}>
            Детали окна
          </h2>
          {selectedDev ? (
            <WindowDetail window={selectedDev} zone={zone} />
          ) : (
            <p className={styles.detailEmpty}>
              Выберите development-окно в timeline или таблице. Protected-окна не
              раскрывают метрики.
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
                      <span role="status" tabIndex={0}>
                        доступ закрыт
                      </span>
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
    </>
  );
}
