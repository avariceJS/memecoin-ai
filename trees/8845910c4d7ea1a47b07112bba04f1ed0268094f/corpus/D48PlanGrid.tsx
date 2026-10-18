import type { Snapshot } from "../../api";
import styles from "./corpus.module.css";
import shared from "../shared.module.css";

type Sampling = Extract<
  Snapshot["research"],
  { availability: "AVAILABLE" }
>["data"]["sampling"];

type Props = {
  sampling: Sampling;
};

export function buildPlanCellKinds(sampling: Sampling): ("development" | "reserved")[] {
  const cells: ("development" | "reserved")[] = [];
  for (let i = 0; i < sampling.developmentWindows; i += 1) {
    cells.push("development");
  }
  for (let i = 0; i < sampling.reservedWindows; i += 1) {
    cells.push("reserved");
  }
  return cells;
}

export function D48PlanGrid({ sampling }: Props) {
  const cells = buildPlanCellKinds(sampling);
  const total = cells.length;

  return (
    <section className={styles.gridPanel} aria-labelledby="d48-grid-heading">
      <h2 id="d48-grid-heading" className={shared.panelTitle}>
        Сетка D48 · {sampling.plannedWindows} запланировано
      </h2>
      <p className={styles.note}>
        {sampling.developmentWindows} design · {sampling.reservedWindows} protected. Ячейки
        design подписаны без метрик сбора; protected не раскрывают counts и drill-down.
      </p>
      <div
        className={styles.planGrid}
        role="grid"
        aria-rowcount={Math.ceil(total / 24)}
        aria-colcount={24}
        data-testid="d48-plan-grid"
      >
        {cells.map((kind, index) => {
          if (kind === "development") {
            return (
              <div
                key={`dev-${index}`}
                className={styles.planCellDev}
                role="gridcell"
                data-testid={`d48-cell-dev-${index}`}
                aria-label={`design ячейка ${index + 1}, не активировано`}
              >
                <span className={styles.planCellDevLabel}>DESIGN</span>
                <span>не активировано</span>
              </div>
            );
          }
          return (
            <span
              key={`res-${index}`}
              className={styles.planCellReserved}
              role="status"
              tabIndex={0}
              data-testid={`d48-cell-reserved-${index}`}
              aria-label="protected design ячейка, доступ закрыт"
            >
              <span className={styles.protectedTag}>PROTECTED</span>
              <span>доступ закрыт</span>
            </span>
          );
        })}
      </div>
      <div className={styles.legend} aria-hidden="true">
        <span className={styles.legendItem}>
          <span className={styles.legendSwatchDev} /> DESIGN / не активировано
        </span>
        <span className={styles.legendItem}>
          <span className={styles.legendSwatchReserved} /> PROTECTED / доступ закрыт
        </span>
      </div>
    </section>
  );
}
