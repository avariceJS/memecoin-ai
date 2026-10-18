import { useQuery } from "@tanstack/react-query";
import { fetchCapabilities, fetchSnapshot } from "./client";
import type { Capability } from "./schema";
import type { SnapshotResponse } from "./schema";

export function useSnapshot() {
  return useQuery<SnapshotResponse, Error>({
    queryKey: ["control-center", "snapshot"],
    queryFn: fetchSnapshot,
    retry: false,
  });
}

export function useCapabilities() {
  return useQuery<Capability[], Error>({
    queryKey: ["control-center", "capabilities"],
    queryFn: fetchCapabilities,
    retry: false,
  });
}
