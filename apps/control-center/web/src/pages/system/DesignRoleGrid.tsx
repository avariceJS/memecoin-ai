import { DESIGN_ROLE_NODES } from "./futureSurfaces";
import styles from "./system.module.css";

export function DesignRoleGrid() {
  return (
    <section className={styles.panel} aria-labelledby="design-roles-heading">
      <h3 id="design-roles-heading" className={styles.panelTitle}>
        Роли узлов
      </h3>
      <p className={styles.caption}>
        Статическая схема. Не связанные live-машины и не обнаружение topology.
      </p>
      <ul className={styles.designGrid}>
        {DESIGN_ROLE_NODES.map((node) => (
          <li key={node.id} className={styles.designCard}>
            <div className={styles.designHead}>
              <p className={styles.designTitle}>{node.title}</p>
              <span className={styles.designTag}>DESIGN</span>
            </div>
            <p className={styles.caption}>{node.note}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
