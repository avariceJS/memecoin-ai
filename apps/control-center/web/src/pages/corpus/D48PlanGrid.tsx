import { useRef, useState, type KeyboardEvent } from "react";
import { Lock } from "lucide-react";
import { UnavailableState } from "../../components/status";
import type { Snapshot } from "../../api";
import styles from "./corpus.module.css";
import shared from "../shared.module.css";
import { deriveD48Plan, type D48Cell, type D48Plan } from "./d48Plan";

type Sampling = Extract<
  Snapshot["research"],
  { availability: "AVAILABLE" }
>["data"]["sampling"];

type Props = {
  sampling: Sampling;
};

export function buildPlanCellKinds(sampling: Sampling): ("development" | "reserved")[] {
  const plan = deriveD48Plan(sampling);
  if (!plan.ok) return [];
  return plan.cells.map((cell) => cell.kind);
}

function cellLabel(cell: D48Cell): string {
  if (cell.kind === "reserved") {
    return `${cell.iso}, UTC ${cell.offset}, доступ закрыт`;
  }
  return `${cell.iso}, UTC ${cell.offset}, development design, не активировано`;
}

export function D48PlanGrid({ sampling }: Props) {
  const plan = deriveD48Plan(sampling);
  const [active, setActive] = useState(0);
  const cellRefs = useRef<Array<HTMLDivElement | null>>([]);

  if (!plan.ok) {
    return (
      <section className={styles.gridPanel} aria-labelledby="d48-grid-heading">
        <h2 id="d48-grid-heading" className={shared.panelTitle}>
          Сетка D48
        </h2>
        <p className={styles.note}>
          Сутки, strata и концы плана не сходятся. Сетка не построена и план не исправлен.
        </p>
        <UnavailableState reasonCode={plan.reason} />
      </section>
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!plan.ok) return;
    const strataCount = plan.strata.length;
    const day = Math.floor(active / strataCount);
    const stratum = active % strataCount;
    let nextDay = day;
    let nextStratum = stratum;
    if (event.key === "ArrowRight") nextDay = Math.min(plan.days - 1, day + 1);
    else if (event.key === "ArrowLeft") nextDay = Math.max(0, day - 1);
    else if (event.key === "ArrowDown") nextStratum = Math.min(strataCount - 1, stratum + 1);
    else if (event.key === "ArrowUp") nextStratum = Math.max(0, stratum - 1);
    else if (event.key === "Home") nextDay = 0;
    else if (event.key === "End") nextDay = plan.days - 1;
    else return;
    event.preventDefault();
    const next = nextDay * strataCount + nextStratum;
    setActive(next);
    const node = cellRefs.current[next];
    if (node) {
      node.tabIndex = 0;
      node.focus();
      node.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    }
  }

  return (
    <section className={styles.gridPanel} aria-labelledby="d48-grid-heading">
      <h2 id="d48-grid-heading" className={shared.panelTitle}>
        Сетка D48 · {sampling.plannedWindows} запланировано
      </h2>
      <p className={styles.note}>
        {plan.months[0]?.key.slice(0, 4) ?? ""}. {plan.developmentDays} суток design,{" "}
        {plan.reservedDays} reserved. Цвет и форма, без слов в ячейке.
      </p>
      <D48Legend />
      <div
        className={styles.matrixScroll}
        data-cc-scroll="d48-day-strata"
        tabIndex={0}
        aria-label="Матрица D48, локальная прокрутка по суткам"
      >
        <div
          className={styles.matrix}
          role="grid"
          data-testid="d48-plan-grid"
          aria-rowcount={plan.strata.length}
          aria-colcount={plan.days}
          aria-label="D48 design: сутки на UTC strata"
          onKeyDown={onKeyDown}
          style={{
            gridTemplateColumns: `4.5rem repeat(${plan.days}, 28px)`,
            minWidth: `calc(4.5rem + ${plan.days} * 28px)`,
          }}
        >
          <MonthRow plan={plan} />
          <WeekRow plan={plan} />
          <StrataRows
            plan={plan}
            active={active}
            cellRefs={cellRefs}
          />
        </div>
      </div>
    </section>
  );
}

function D48Legend() {
  return (
    <ul className={styles.legend} data-testid="d48-legend">
      <li className={styles.legendItem}>
        <span className={styles.swatchDev} aria-hidden="true" />
        development design, не активировано
      </li>
      <li className={styles.legendItem}>
        <span className={styles.swatchReserved} aria-hidden="true" />
        <Lock size={12} aria-hidden="true" />
        reserved design, доступ закрыт
      </li>
    </ul>
  );
}

function MonthRow({ plan }: { plan: Extract<D48Plan, { ok: true }> }) {
  return (
    <div role="row" className={styles.gridRow} aria-hidden="true">
      <div className={styles.corner} />
      {plan.months.map((month) => (
        <div
          key={month.key}
          className={styles.monthLabel}
          style={{ gridColumn: `span ${month.dayCount}` }}
        >
          {month.label}
        </div>
      ))}
    </div>
  );
}

function WeekRow({ plan }: { plan: Extract<D48Plan, { ok: true }> }) {
  return (
    <div role="row" className={styles.gridRow} aria-hidden="true">
      <div className={styles.corner} />
      {plan.weeks.map((week) => (
        <div
          key={week.iso}
          className={styles.weekLabel}
          style={{ gridColumn: `span ${week.dayCount}` }}
          title={week.iso}
        >
          {week.label}
        </div>
      ))}
    </div>
  );
}

function StrataRows({
  plan,
  active,
  cellRefs,
}: {
  plan: Extract<D48Plan, { ok: true }>;
  active: number;
  cellRefs: { current: Array<HTMLDivElement | null> };
}) {
  return (
    <>
      {plan.strata.map((offset, stratum) => (
        <div role="row" className={styles.gridRow} key={offset}>
          <div className={styles.stratumLabel} role="rowheader">
            {offset}
          </div>
          {Array.from({ length: plan.days }, (_, day) => {
            const cell = plan.cells[day * plan.strata.length + stratum];
            if (!cell) return null;
            const label = cellLabel(cell);
            const boundary = cell.dayIndex === plan.developmentDays;
            const weekStart = cell.dayIndex % 7 === 0 && !boundary;
            return (
              <div
                key={cell.linear}
                role="gridcell"
                ref={(node) => {
                  cellRefs.current[cell.linear] = node;
                }}
                tabIndex={cell.linear === active ? 0 : -1}
                aria-selected={cell.linear === active}
                aria-rowindex={stratum + 1}
                aria-colindex={day + 1}
                aria-label={label}
                title={label}
                data-testid={`d48-cell-${cell.kind === "development" ? "dev" : "reserved"}-${cell.linear}`}
                data-kind={cell.kind}
                className={[
                  styles.cell,
                  cell.kind === "development" ? styles.cellDev : styles.cellReserved,
                  boundary ? styles.cellBoundary : "",
                  weekStart ? styles.cellWeek : "",
                ].join(" ")}
              />
            );
          })}
        </div>
      ))}
    </>
  );
}
