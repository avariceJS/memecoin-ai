import { Link } from "react-router";
import { Panel } from "../../components";
import { QualificationBadge, type ProvenanceBadgeKind } from "../../components/status";
import { CodeDisclosure } from "./CodeDisclosure";
import styles from "./research.module.css";
import { groupMissingReviews, type ResearchData } from "./viewModel";

type DetailsProps = {
  data: ResearchData;
  provenance: ProvenanceBadgeKind;
};

function DemoMark({ provenance }: { provenance: ProvenanceBadgeKind }) {
  if (provenance !== "DEMO") return null;
  return <QualificationBadge kind="DEMO" />;
}

function ReviewGroups({ codes }: { codes: string[] }) {
  const groups = groupMissingReviews(codes);
  if (groups.length === 0) {
    return <p className={styles.summary}>В проекции нет списка review.</p>;
  }
  return (
    <div className={styles.groupGrid}>
      {groups.map((group) => (
        <section key={group.id} className={styles.group} aria-labelledby={`review-${group.id}`}>
          <h3 id={`review-${group.id}`} className={styles.groupTitle}>
            {group.title}
          </h3>
          <ul className={styles.groupList}>
            {group.items.map((item) => (
              <li key={item.code}>{item.label}</li>
            ))}
          </ul>
          <CodeDisclosure
            label={`Сырые коды, ${group.title}`}
            scrollId={`research-codes-${group.id}`}
            codes={group.items.map((item) => item.code)}
          />
        </section>
      ))}
    </div>
  );
}

export function ResearchDetails({ data, provenance }: DetailsProps) {
  const sampling = data.sampling;
  const bounds = [
    `anchorNs=${sampling.anchorNs}`,
    `developmentEndNs=${sampling.developmentEndNs}`,
    `holdoutEndNs=${sampling.holdoutEndNs}`,
  ];

  return (
    <div className={styles.lower}>
      <Panel title="Обязательные review" density="compact" className={styles.stackPanel}>
        <DemoMark provenance={provenance} />
        <p className={styles.summary}>
          Список обязательных review из frozen spec. Отдельный статус закрытия по каждому коду не
          опубликован.
        </p>
        <p className={styles.summary}>Пунктов: {data.missingReviews.length}. Это не счётчик числовых блокеров.</p>
        <ReviewGroups codes={data.missingReviews} />
      </Panel>
      <div className={styles.sideStack}>
        <Panel title="План выборки" density="compact" className={styles.stackPanel}>
          <DemoMark provenance={provenance} />
          <p className={styles.summary}>Публичные счётчики плана. Полная сетка — на календаре.</p>
          <dl className={styles.samplingGrid}>
            <div>
              <dt>Запланировано</dt>
              <dd>{sampling.plannedWindows}</dd>
            </div>
            <div>
              <dt>Development</dt>
              <dd>{sampling.developmentWindows}</dd>
            </div>
            <div>
              <dt>Reserved</dt>
              <dd>{sampling.reservedWindows}</dd>
            </div>
            <div>
              <dt>Допущенные</dt>
              <dd>{sampling.admittedWindows}</dd>
            </div>
          </dl>
          <p className={styles.offsetCaption}>Смещения UTC</p>
          <ul className={styles.offsetRow} data-cc-scroll="research-utc-offsets" tabIndex={0} aria-label="Смещения UTC">
            {sampling.utcOffsets.map((offset, index) => (
              <li key={`${offset}-${index}`}>{offset}</li>
            ))}
          </ul>
          <CodeDisclosure label="Границы плана" scrollId="research-plan-bounds" codes={bounds} />
          <Link to="/calendar" className={styles.calendarLink}>
            Открыть календарь
          </Link>
        </Panel>
        <Panel title="Ledger / event export" density="compact" className={styles.stackPanel}>
          <DemoMark provenance={provenance} />
          <QualificationBadge kind="UNAVAILABLE" />
          <p className={styles.summary}>
            Нет approved read contract в snapshot. Запрос интерфейса: proposal CC-P001 — безопасный
            operational metadata export (не raw tree, не запуск из UI).
          </p>
        </Panel>
      </div>
    </div>
  );
}
