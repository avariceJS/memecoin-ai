import { Panel } from "../../components";
import { statusLabel } from "../calendar/labels";
import { shortUtc } from "./format";
import type { D43Marker } from "./selectors";
import styles from "./overview.module.css";

type WindowSummaryProps = {
  markers: D43Marker[] | null;
  runGroups: { kind: string; count: number }[] | null;
  runCount: number | null;
  activeModelChosen: boolean | null;
  marketBackboneSelected: boolean | null;
};

export function WindowSummary({
  markers,
  runGroups,
  runCount,
  activeModelChosen,
  marketBackboneSelected,
}: WindowSummaryProps) {
  const runText =
    runGroups && runGroups.length > 0
      ? runGroups.map((group) => `${group.kind} ${group.count}`).join(" · ")
      : null;

  return (
    <Panel title="Сводка окон и runs" density="compact">
      {markers && markers.length > 0 ? (
        <div className={styles.tableWrap} data-cc-scroll="overview-windows" tabIndex={0} aria-label="Сводка окон">
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">окно</th>
                <th scope="col">план UTC</th>
                <th scope="col">доступ</th>
                <th scope="col">статус</th>
                <th scope="col">старт</th>
              </tr>
            </thead>
            <tbody>
              {markers.map((marker) => (
                <tr key={marker.id}>
                  <td>{marker.index}</td>
                  <td className={styles.mono}>{marker.id}</td>
                  <td>{shortUtc(marker.plannedAt)}</td>
                  <td>{marker.access === "PROTECTED" ? "закрыт" : "development"}</td>
                  <td>
                    {marker.access === "PROTECTED"
                      ? "доступ закрыт"
                      : marker.recordedStatus
                        ? statusLabel(marker.recordedStatus)
                        : "не опубликован"}
                  </td>
                  <td>{marker.access === "PROTECTED" ? "—" : marker.actualStartNs ? "опубликован" : "не опубликован"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.note}>Окна календаря в snapshot недоступны.</p>
      )}
      <p className={styles.runLine}>
        Старт не опубликован. Длительность не строится. Инженерные runs:{" "}
        <strong>{runCount === null ? "—" : runCount}</strong>
        {runText ? ` · ${runText}` : ""}. Активная модель{" "}
        {activeModelChosen === null ? "—" : activeModelChosen ? "задана" : "не выбрана"}. Рыночный
        backbone {marketBackboneSelected === null ? "—" : marketBackboneSelected ? "выбран" : "не выбран"}.
      </p>
    </Panel>
  );
}
