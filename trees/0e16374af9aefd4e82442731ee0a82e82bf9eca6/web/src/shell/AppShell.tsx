import { Outlet, useLocation } from "react-router";
import { useCapabilities, useSnapshot } from "../api";
import { ErrorState, LoadingState } from "../components/status";
import { Header } from "./Header";
import { navItems } from "./navConfig";
import { Sidebar } from "./Sidebar";
import styles from "./AppShell.module.css";

function routeTitle(pathname: string): string {
  const item = navItems.find((entry) =>
    entry.to === "/" ? pathname === "/" : pathname.startsWith(entry.to),
  );
  return item?.label ?? "Observatory";
}

export function AppShell() {
  const location = useLocation();
  const snapshotQuery = useSnapshot();
  const capabilitiesQuery = useCapabilities();
  const title = routeTitle(location.pathname);

  return (
    <div className={styles.root}>
      <Sidebar />
      <div className={styles.mainColumn}>
        <Header
          title={title}
          snapshotResponse={snapshotQuery.data}
          refreshing={snapshotQuery.isFetching}
          onRefresh={() => {
            void snapshotQuery.refetch();
            void capabilitiesQuery.refetch();
          }}
        />
        <main className={styles.main} data-cc-region="main-grid">
          <div className={styles.mainInner}>
            {snapshotQuery.isLoading ? (
              <LoadingState />
            ) : snapshotQuery.isError ? (
              <ErrorState
                error={snapshotQuery.error}
                onRetry={() => {
                  void snapshotQuery.refetch();
                  void capabilitiesQuery.refetch();
                }}
              />
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
