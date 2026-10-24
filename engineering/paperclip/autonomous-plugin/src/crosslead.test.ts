import { describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

vi.mock("@paperclipai/plugin-sdk", () => ({ definePlugin: (value: unknown) => value, runWorker: vi.fn() }));

import { registerAutonomousTools } from "./worker.js";

type Options = {
  priorQaDep?: boolean;
  sourceGoal?: string;
  reviewGoal?: string;
  priorRunStatus?: string;
  changeCurrentAfterRead?: number;
  incomplete?: boolean;
  notReady?: boolean;
  currentRunAbsent?: boolean;
  retryCurrent?: boolean;
  wrongPredecessor?: boolean;
};

const operationKey = "d".repeat(64);
const digest = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");

async function probe(options: Options = {}) {
  const root = await mkdtemp(join(tmpdir(), "crosslead-readiness-"));
  const handlers = new Map<string, (params: unknown, run: unknown) => Promise<unknown>>();
  let currentReads = 0;
  const leadIssue = (id: string) => ({
    id,
    companyId: "company-1",
    projectId: id === "lead-1" ? "project-1" : "project-2",
    goalId: id === "lead-1" ? "goal-1" : "goal-2",
    assigneeAgentId: id === "lead-1" ? "sol-1" : "sol-2",
    status: id === "lead-1" ? "done" : "in_progress",
    executionRunId: id === "lead-1" ? null : "run-2",
    projectWorkspaceId: "workspace-2",
    executionWorkspaceId: "execution-2",
  });
  const childIssue = (id: string) => ({
    id,
    companyId: "company-1",
    projectId: "project-2",
    goalId: id === "source-2" ? options.sourceGoal ?? "goal-2"
      : id === "review-2" ? options.reviewGoal ?? "goal-2" : "goal-2",
    parentId: "lead-2",
    assigneeAgentId: id.includes("source") ? "worker-1" : "reviewer-1",
    status: id.includes("old") ? "done" : id === "source-2" ? "backlog" : "blocked",
    executionRunId: null,
    statusVersion: id.includes("old") ? 2 : 1,
    description: id.includes("old") ? `${id} instructions` : id,
    projectWorkspaceId: "workspace-2",
    executionWorkspaceId: "execution-2",
  });
  const readiness = (issueId: string, blockerIds: string[]) => ({
    issueId,
    blockerIssueIds: blockerIds,
    unresolvedBlockerIssueIds: options.notReady ? blockerIds : [],
    unresolvedBlockerCount: options.notReady ? blockerIds.length : 0,
    pendingFinalizeBlockerIssueIds: options.notReady ? blockerIds : [],
    allBlockersDone: !options.notReady,
    isDependencyReady: !options.notReady,
  });
  const summary = (id: string) => {
    if (id === "lead-2") currentReads += 1;
    const blockerIds = id === "lead-1" ? (options.priorQaDep ? ["prior-qa"] : [])
      : options.retryCurrent ? ["review-2-old"] : ["lead-1"];
    if (id === "lead-2" && options.changeCurrentAfterRead && currentReads >= options.changeCurrentAfterRead) {
      blockerIds.push("foreign-blocker");
    }
    const blockers = blockerIds.map((blockerId) => ({
      id: blockerId,
      status: "done",
      assigneeAgentId: blockerId === "lead-1" ? "sol-1" : "reviewer-1",
    }));
    return {
      issueId: id,
      companyId: "company-1",
      subtreeIssueIds: [id],
      relations: { [id]: { blockedBy: blockers, blocks: [] } },
      runs: id === "lead-1" ? [{
        id: "prior-run", issueId: id, agentId: "sol-1", status: options.priorRunStatus ?? "succeeded",
        finishedAt: "2026-10-24T00:00:00Z",
      }] : options.currentRunAbsent ? [] : [{
        id: "run-2", issueId: id, agentId: "sol-2", status: "running", finishedAt: null,
      }],
      dependencyReadinessComplete: !options.incomplete,
      dependencyReadiness: options.incomplete ? null : readiness(id, blockerIds),
    };
  };
  const ctx = {
    config: { get: async () => ({ ownerConfigPath: join(root, "owner.json") }) },
    issues: {
      get: async (id: string) => leadIssue(id),
      summaries: {
        getOrchestration: async ({ issueId, companyId, includeSubtree }: { issueId: string; companyId: string; includeSubtree: boolean }) => {
          expect(["lead-1", "lead-2"]).toContain(issueId);
          expect(companyId).toBe("company-1");
          expect(includeSubtree).toBe(false);
          return summary(issueId);
        },
      },
      forCurrentToolRun: () => ({
        get: async (id: string) => id === "lead-2" ? leadIssue(id) : childIssue(id),
        getOrchestration: async ({ issueId }: { issueId: string }) => ({
          issueId, companyId: "company-1", runs: [],
          blockedByIssueIds: issueId === "review-2-old" ? ["source-2-old"] : ["source-2"],
        }),
        getDependencyReadiness: async (issueId: string) => issueId === "review-2-old"
          ? readiness(issueId, ["source-2-old"])
          : ({ ...readiness(issueId, ["source-2"]), isDependencyReady: false,
            allBlockersDone: false, unresolvedBlockerIssueIds: ["source-2"], unresolvedBlockerCount: 1 }),
      }),
    },
    executionWorkspaces: {
      get: async () => ({
        id: "execution-2", companyId: "company-1", projectId: "project-2", projectWorkspaceId: "workspace-2",
        path: "/tmp/crosslead-workspace-2", cwd: "/tmp/crosslead-workspace-2", repoUrl: null, baseRef: null,
        branchName: null, providerType: "local_fs", providerMetadata: null,
      }),
    },
    tools: { register: (name: string, _declaration: unknown, handler: never) => handlers.set(name, handler) },
  };
  const oldOperationKey = "e".repeat(64);
  const oldBinding = {
    found: true, attempt: 1, operation_key: oldOperationKey, operation_state: "review_rejected",
    operation_review_approved: false, state: "active", activation_pending: false,
    source_issue_id: "source-2-old", review_issue_id: "review-2-old", stage_binding_key: "a".repeat(64),
    source_version: 1, review_version: 1, base_sha: "f".repeat(40),
    predecessor_operation_key: operationKey, predecessor_source_issue_id: "prior-source",
    predecessor_review_issue_id: "prior-qa", source_instruction_sha256: digest("source-2-old instructions"),
    review_instruction_sha256: digest("review-2-old instructions"),
  };
  const invoke = vi.fn(async (action: string, payload: Record<string, unknown>) => {
    if (action === "bind_source_stage") throw new Error("REACHED_EXACT_BRIDGE_BIND");
    if (payload.stage_key === "MEM9") {
      if (options.retryCurrent && payload.attempt === 1) return oldBinding;
      return { found: false, attempt: payload.attempt ?? 1 };
    }
    expect(payload.published_predecessor_lookup).toBe(true);
    expect(payload.successor_stage_key).toBe("MEM9");
    return {
      found: true, attempt: 3, state: "active", activation_pending: false,
      operation_key: operationKey, source_issue_id: "prior-source", review_issue_id: "prior-qa",
      candidate_sha: "a".repeat(40), source_receipt_digest: "b".repeat(64),
      canonical_candidate_sha: "c".repeat(40), canonical_receipt_digest: "e".repeat(64),
      canonical_approved_receipt_digest: "e".repeat(64),
      canonical_receipt_ref: "refs/heads/org/development",
      operation_state: "published", operation_review_approved: true, canonical_status: "published",
    };
  });
  try {
    const stage = (stage_key: string, project_id: string, lead_issue_id: string, lead_agent_id: string, goal_id: string, ordinal: number) => ({
      stage_key, project_id, lead_issue_id, lead_agent_id, goal_id,
      workspace_id: ordinal === 1 ? "source" : "published",
      initial_base_sha: ordinal === 1 ? "a".repeat(40) : null,
      predecessor_stage_key: ordinal === 1 ? null : "MEM8",
      path_ids: ["src/example.py", "tests/test_example.py"],
      verifier_policy_id: "policy-1",
      source_workspace_id: `workspace-${ordinal}`,
      source_workspace_root: `/tmp/crosslead-workspace-${ordinal}`,
    });
    await writeFile(join(root, "owner.json"), JSON.stringify({
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "sol-1",
      finite_lead_issue_id: "lead-1", python_executable: "/usr/bin/python3",
      source_stage_catalog: [
        stage("MEM8", "project-1", "lead-1", "sol-1", "goal-1", 1),
        stage("MEM9", "project-2", "lead-2", "sol-2", "goal-2", 2),
      ],
    }), { mode: 0o600 });
    registerAutonomousTools(ctx as never, invoke as never);
    await handlers.get("bind_source_stage")!({
      stage_key: "MEM9", source_issue_id: "source-2", review_issue_id: "review-2",
      ...(options.retryCurrent ? { attempt: 2, retry_of_operation_key: oldOperationKey } : {}),
      ...(options.wrongPredecessor ? { predecessor: {
        operation_key: operationKey, source_issue_id: "unadmitted-source", review_issue_id: "prior-qa",
      } } : {}),
    }, { companyId: "company-1", projectId: "project-2", agentId: "sol-2", runId: "run-2" });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

describe("owner-pinned crosslead continuation", () => {
  it("accepts an exact published retry-3 predecessor with its retained QA dependency", async () => {
    await expect(probe({ priorQaDep: true })).rejects.toThrow("REACHED_EXACT_BRIDGE_BIND");
  });

  it("accepts a crosslead current retry only with its exact rejected QA dependency", async () => {
    await expect(probe({ priorQaDep: true, retryCurrent: true })).rejects.toThrow("REACHED_EXACT_BRIDGE_BIND");
  });

  it("rejects a supplied predecessor tuple that differs from the owner journal", async () => {
    await expect(probe({ priorQaDep: true, wrongPredecessor: true }))
      .rejects.toThrow("source_stage_predecessor_not_finalized");
  });

  it.each(["sourceGoal", "reviewGoal"] as const)("rejects a foreign goal on the %s child", async (field) => {
    await expect(probe({ [field]: "foreign-goal" })).rejects.toThrow(/source_stage_(source|review)_admission_mismatch/);
  });

  it("rejects unknown predecessor run outcomes", async () => {
    await expect(probe({ priorQaDep: true, priorRunStatus: "unknown" }))
      .rejects.toThrow("source_stage_lead_summary_identity_mismatch");
  });

  it("rechecks current dependency and holds missing native readiness facts", async () => {
    await expect(probe({ priorQaDep: true, changeCurrentAfterRead: 3 }))
      .rejects.toThrow("source_stage_predecessor_not_finalized");
    await expect(probe({ priorQaDep: true, incomplete: true }))
      .rejects.not.toThrow("REACHED_EXACT_BRIDGE_BIND");
    await expect(probe({ priorQaDep: true, notReady: true }))
      .rejects.not.toThrow("REACHED_EXACT_BRIDGE_BIND");
    await expect(probe({ priorQaDep: true, currentRunAbsent: true }))
      .rejects.not.toThrow("REACHED_EXACT_BRIDGE_BIND");
  });
});
