import { Outlet, useLocation } from "react-router";
import { useCapabilities, useSnapshot } from "../api";
import { ErrorState, LoadingState } from "../components/status";
import { truncateId } from "../pages/snapshotFacts";
import { Header } from "./Header";
import { navItems } from "./navConfig";
import { Sidebar } from "./Sidebar";
import styles from "./AppShell.module.css";

function routeTitle(pathname: string): string {
  const item = navItems.find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)));
  return item?.label ?? "Observatory";
}

export function AppShell() {
  const location = useLocation();
  const snapshotQuery = useSnapshot();
  const capabilitiesQuery = useCapabilities();
  const title = routeTitle(location.pathname);

  const showBanner =
    snapshotQuery.isSuccess && snapshotQuery.data !== undefined && !snapshotQuery.isError;

  return (
    <div className={styles.root}>
      <Sidebar />
      <div className={styles.mainColumn}>
        <Header title={title} snapshotResponse={snapshotQuery.data} />
        <main className={styles.main}>
          <div className={styles.mainInner}>
            {showBanner ? (
              <div className={styles.evidenceBanner} role="status">
                <strong>RECORDED EVIDENCE</strong>
                <span className={styles.evidenceMeta}>
                  baseline {snapshotQuery.data.data.coreBaselineCommit}
                  {" · "}
                  snapshot{" "}
                  <span title={snapshotQuery.data.snapshotId}>
                    {truncateId(snapshotQuery.data.snapshotId)}
                  </span>
                </span>
              </div>
            ) : null}
            {snapshotQuery.isLoading ? (
              <LoadingState />
            ) : snapshotQuery.isError ? (
              <ErrorState error={snapshotQuery.error} onRetry={() => void snapshotQuery.refetch()} />
            ) : snapshotQuery.data ? (
              <Outlet
                context={{
                  snapshotResponse: snapshotQuery.data,
                  capabilities: capabilitiesQuery.data ?? snapshotQuery.data.data.capabilities,
                }}
              />
            ) : null}
          </div>
        </main>
      </div>
    </div>
  );
}

export type ShellOutletContext = {
  snapshotResponse: import("../api").SnapshotResponse;
  capabilities: import("../api").Capability[];
};
