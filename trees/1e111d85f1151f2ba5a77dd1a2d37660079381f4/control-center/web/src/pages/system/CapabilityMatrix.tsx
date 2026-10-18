import { useId, useState } from "react";
import type { Capability } from "../../api";
import { QualificationBadge } from "../../components/status";
import { CodeLine } from "./CodeLine";
import styles from "./system.module.css";
import { capabilityCards, capabilityTally, type CapabilityCardModel } from "./viewModel";

type CapabilityMatrixProps = {
  capabilities: Capability[];
};

function ToneMark({ tone }: { tone: CapabilityCardModel["tone"] }) {
  return <span className={styles.mark} data-tone={tone} aria-hidden="true" />;
}

function CapabilityCode({ card }: { card: CapabilityCardModel }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (card.code.length <= 32) {
    return <CodeLine scrollId={`capability-${card.id}`} label={`Код ${card.label}`} value={card.code} />;
  }
  return (
    <>
      <button
        type="button"
        className={styles.codeToggle}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        Код контракта
      </button>
      {open ? (
        <div id={panelId}>
          <CodeLine scrollId={`capability-${card.id}`} label={`Код ${card.label}`} value={card.code} />
        </div>
      ) : null}
    </>
  );
}

function CapabilityCard({ card }: { card: CapabilityCardModel }) {
  return (
    <li className={styles.card} data-tone={card.tone}>
      <div className={styles.cardHead}>
        <p className={styles.cardTitle}>
          <ToneMark tone={card.tone} />
          {card.label}
        </p>
        {card.tone === "recorded" ? (
          <span className={styles.recordChip}>Запись</span>
        ) : (
          <QualificationBadge kind={card.tone === "locked" ? "LOCKED" : "UNAVAILABLE"} />
        )}
      </div>
      <p className={styles.cardSummary}>{card.summary}</p>
      <CapabilityCode card={card} />
    </li>
  );
}

function CardGroup({ label, cards }: { label: string; cards: CapabilityCardModel[] }) {
  if (cards.length === 0) return null;
  return (
    <>
      <h4 className={styles.groupTitle}>{label}</h4>
      <ul className={styles.matrix} aria-label={label}>
        {cards.map((card) => (
          <CapabilityCard key={card.id} card={card} />
        ))}
      </ul>
    </>
  );
}

export function CapabilityMatrix({ capabilities }: CapabilityMatrixProps) {
  const cards = capabilityCards(capabilities);
  const recorded = cards.filter((card) => card.tone === "recorded");
  const closed = cards.filter((card) => card.tone !== "recorded");

  return (
    <section
      className={styles.matrixPanel}
      data-cc-region="capability-cards"
      aria-labelledby="capability-matrix-heading"
    >
      <div className={styles.panelHead}>
        <h3 id="capability-matrix-heading" className={styles.panelTitle}>
          Контракт возможностей
        </h3>
        <p className={styles.tally}>{capabilityTally(cards)}</p>
      </div>
      <ul className={styles.legend} aria-label="Обозначения матрицы">
        <li>
          <ToneMark tone="recorded" />
          Запись
        </li>
        <li>
          <ToneMark tone="unavailable" />
          Недоступно
        </li>
        <li>
          <ToneMark tone="locked" />
          Закрыто
        </li>
      </ul>
      {cards.length === 0 ? (
        <p className={styles.caption}>В переданном контракте нет возможностей.</p>
      ) : (
        <>
          <CardGroup label="Чтение записи" cards={recorded} />
          <CardGroup label="Закрытые контракты" cards={closed} />
        </>
      )}
    </section>
  );
}
