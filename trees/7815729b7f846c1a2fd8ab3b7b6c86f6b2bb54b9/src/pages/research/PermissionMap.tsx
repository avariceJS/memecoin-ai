import { useId } from "react";
import type { Snapshot } from "../../api";
import { QualificationBadge, type ProvenanceBadgeKind } from "../../components/status";
import { CodeDisclosure } from "./CodeDisclosure";
import { LockMark, StageMark } from "./marks";
import styles from "./research.module.css";
import {
  corpusStage,
  frozenStage,
  numericStage,
  qualificationCaption,
  recordedYesNo,
  type ResearchData,
} from "./viewModel";

type PermissionMapProps = {
  data: ResearchData;
  qualification: string;
  snapshot: Snapshot;
  provenance: ProvenanceBadgeKind;
};

export function PermissionMap({ data, qualification, snapshot, provenance }: PermissionMapProps) {
  const titleId = useId();
  const frozenTitle = useId();
  const corpusTitle = useId();
  const numericTitle = useId();
  const admissionTitle = useId();
  const runnerTitle = useId();
  const holdoutTitle = useId();
  const recordedTitle = useId();
  const engineeringTitle = useId();
  const stripTitle = useId();
  const frozen = frozenStage(data.recordedProtocolFrozen);
  const numeric = numericStage(data.numericBlockers);
  const corpus = corpusStage(snapshot, data);

  return (
    <section className={styles.map} aria-labelledby={titleId} data-cc-region="research-gates">
      <div className={styles.mapHead}>
        <h2 id={titleId} className={styles.mapTitle}>
          Независимые разрешения
        </h2>
        <QualificationBadge kind={provenance} />
      </div>
      <p className={styles.lede}>
        Что зафиксировано, чего не хватает и какое действие отсутствует. Стадии независимы: знак ≠
        значит, что предыдущая не открывает следующую.
      </p>
      <div className={styles.plot}>
        <div className={styles.stageRow}>
          <article className={styles.stage} aria-labelledby={frozenTitle} data-stage="frozen-spec">
            <h3 id={frozenTitle}>Frozen spec</h3>
            <div className={styles.stateRow}>
              <span className={`${styles.tone} ${styles.toneFrozen}`}>
                <StageMark kind={frozen.mark} />
              </span>
              <p className={styles.stateLabel}>{frozen.label}</p>
            </div>
            <p className={styles.studyId} aria-label="Study id">
              {data.specId}
            </p>
            <p className={styles.summary}>{qualificationCaption(qualification)}</p>
            <CodeDisclosure
              label="Код квалификации"
              scrollId="research-spec-qualification"
              codes={[qualification, `recordedProtocolFrozen=${data.recordedProtocolFrozen}`]}
            />
          </article>
          <span className={styles.junction} aria-hidden="true">
            ≠
          </span>
          <article className={styles.stage} aria-labelledby={corpusTitle} data-stage="qualified-corpus">
            <h3 id={corpusTitle}>Квалификация корпуса</h3>
            <div className={styles.stateRow}>
              <span
                className={`${styles.tone} ${corpus.badge === "UNAVAILABLE" ? styles.toneUnknown : styles.toneCorpus}`}
              >
                <StageMark kind={corpus.mark} />
              </span>
              <p className={styles.stateLabel}>{corpus.label}</p>
              {corpus.badge ? <QualificationBadge kind={corpus.badge} /> : null}
            </div>
            <p className={styles.summary}>{corpus.summary}</p>
            <CodeDisclosure label="Коды корпуса" scrollId="research-corpus-codes" codes={corpus.codes} />
          </article>
          <span className={styles.junction} aria-hidden="true">
            ≠
          </span>
          <article className={styles.stage} aria-labelledby={numericTitle} data-stage="numeric-checks">
            <h3 id={numericTitle}>Числовые проверки</h3>
            <div className={styles.stateRow}>
              <span
                className={`${styles.tone} ${numeric.mark === "square-dot" ? styles.tonePass : styles.toneBlockers}`}
              >
                <StageMark kind={numeric.mark} />
              </span>
              <p className={styles.stateLabel}>{numeric.label}</p>
            </div>
            <dl className={styles.pair}>
              <div>
                <dt>Проверки</dt>
                <dd>{data.numericChecks}</dd>
              </div>
              <div>
                <dt>Блокеры</dt>
                <dd>{data.numericBlockers}</dd>
              </div>
            </dl>
            <p className={styles.summary}>Не открывает допуск.</p>
          </article>
          <span className={styles.junction} aria-hidden="true">
            ≠
          </span>
          <article
            className={`${styles.stage} ${styles.stageLocked}`}
            aria-labelledby={admissionTitle}
            data-stage="admission"
          >
            <h3 id={admissionTitle}>Допуск</h3>
            <div className={styles.stateRow}>
              <span className={`${styles.tone} ${styles.toneLock}`}>
                <LockMark />
              </span>
              <QualificationBadge kind={data.admission} />
            </div>
            <p className={styles.summary}>
              Записанный запуск: {recordedYesNo(data.recordedResearchLaunchAuthorized)}. Оболочка не
              открывает обучение.
            </p>
            <CodeDisclosure
              label="Флаг запуска"
              scrollId="research-launch-flag"
              codes={[`recordedResearchLaunchAuthorized=${data.recordedResearchLaunchAuthorized}`]}
            />
          </article>
        </div>
        <p className={styles.rule}>
          Ни зафиксированный spec, ни записанный факт корпуса, ни числовой pass не публикуют статус
          runner и не открывают обучение.
        </p>
        <div className={styles.consequence}>
          <article className={styles.consequenceCard} aria-labelledby={runnerTitle} data-stage="runner">
            <h3 id={runnerTitle}>Runner</h3>
            <div className={styles.stateRow}>
              <span className={`${styles.tone} ${styles.toneUnknown}`}>
                <StageMark kind="unknown-ring" />
              </span>
              <p className={styles.stateLabel}>Неизвестно</p>
            </div>
            <p className={styles.summary}>
              Отдельный статус runner не опубликован и не следует из других стадий.
            </p>
          </article>
          <article
            className={`${styles.consequenceCard} ${styles.holdout}`}
            aria-labelledby={holdoutTitle}
            data-stage="holdout"
          >
            <h3 id={holdoutTitle}>Holdout</h3>
            <div className={styles.stateRow}>
              <span className={styles.hatchSwatch} aria-hidden="true" />
              <QualificationBadge kind={data.holdout} />
            </div>
            <p className={styles.summary}>
              Globally unseen: <span className={styles.factStrong}>{data.globallyUnseenHoldout}</span>
            </p>
            <p className={styles.releaseLine}>
              <span>Записанные релизы</span>
              <strong>{data.recordedHoldoutReleases}</strong>
            </p>
            <p className={styles.summary}>
              Релизы не являются exposures. Число релизов не доказывает globally unseen.
            </p>
            <p className={styles.summary}>
              Записанный релиз: {recordedYesNo(data.recordedHoldoutReleaseAuthorized)}.
            </p>
            <CodeDisclosure
              label="Флаг релиза"
              scrollId="research-release-flag"
              codes={[`recordedHoldoutReleaseAuthorized=${data.recordedHoldoutReleaseAuthorized}`]}
            />
          </article>
        </div>
        <div className={styles.counterGroup} role="group" aria-labelledby={stripTitle}>
          <p id={stripTitle} className={styles.stripLabel}>
            Раздельные счётчики
          </p>
          <div className={styles.counterStrip}>
            <article className={styles.countBlock} aria-labelledby={recordedTitle}>
              <h3 id={recordedTitle}>Записанные research fits</h3>
              <p className={styles.countValue}>
                {data.recordedResearchFits}/{data.fitBudget}
              </p>
              <p className={styles.summary}>Знаменатель — бюджет spec. Это не engineering fits.</p>
            </article>
            <p className={styles.vs} aria-label="Счётчики не складываются">
              <span aria-hidden="true">≠</span>
            </p>
            <article className={styles.countBlock} aria-labelledby={engineeringTitle}>
              <div className={styles.countTitleRow}>
                <h3 id={engineeringTitle}>Engineering fits</h3>
                <QualificationBadge kind="ENGINEERING" />
              </div>
              <p className={styles.countValue}>{data.engineeringFits}</p>
              <p className={styles.summary}>Smoke. Не бюджет и не записанные research fits.</p>
            </article>
          </div>
        </div>
      </div>
      <p className={styles.legendCaption}>Формы состояний. Это ключ, не итог.</p>
      <ul className={styles.legend} aria-label="Легенда форм, не итог стадий">
        <li>
          <span className={`${styles.tone} ${styles.toneFrozen}`}>
            <StageMark kind="diamond-solid" />
          </span>
          Зафиксирован
        </li>
        <li>
          <span className={`${styles.tone} ${styles.toneFrozen}`}>
            <StageMark kind="diamond-hollow" />
          </span>
          Не зафиксирован
        </li>
        <li>
          <span className={`${styles.tone} ${styles.toneCorpus}`}>
            <StageMark kind="hex-dashed" />
          </span>
          Не квалифицирован
        </li>
        <li>
          <span className={`${styles.tone} ${styles.toneCorpus}`}>
            <StageMark kind="hex-double" />
          </span>
          Записанный факт
        </li>
        <li>
          <span className={`${styles.tone} ${styles.toneBlockers}`}>
            <StageMark kind="square-gap" />
          </span>
          Есть блокеры
        </li>
        <li>
          <span className={`${styles.tone} ${styles.tonePass}`}>
            <StageMark kind="square-dot" />
          </span>
          Числовой pass
        </li>
        <li>
          <span className={`${styles.tone} ${styles.toneLock}`}>
            <LockMark />
          </span>
          LOCKED
        </li>
        <li>
          <span className={`${styles.tone} ${styles.toneUnknown}`}>
            <StageMark kind="unknown-ring" />
          </span>
          Неизвестно
        </li>
        <li>
          <span className={styles.hatchSwatch} aria-hidden="true" />
          PROTECTED
        </li>
      </ul>
    </section>
  );
}
