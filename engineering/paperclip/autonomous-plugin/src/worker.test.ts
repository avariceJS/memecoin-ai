import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@paperclipai/plugin-sdk", () => ({ definePlugin: (value: unknown) => value, runWorker: vi.fn() }));

import { registerAutonomousTools } from "./worker.js";
import type { PluginContext, PluginToolRunCurrent, ToolRunContext } from "@paperclipai/plugin-sdk";

const owner = {
  schema: "paperclip-autonomous-bridge-config-v2",
  company_id: "company-1",
  project_id: "project-1",
  worker_agent_id: "worker-1",
  reviewer_agent_id: "reviewer-1",
  source_issue_id: "issue-source",
  python_executable: "/usr/bin/python3",
};
const workerRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "worker-1", runId: "worker-run" };
const reviewerRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "reviewer-1", runId: "reviewer-run" };
const operationKey = "a".repeat(64);
const candidateSha = "b".repeat(40);
const inspectionDigest = "c".repeat(64);

function hostCurrent(run: ToolRunContext, authority: PluginToolRunCurrent["authority"]): PluginToolRunCurrent {
  return {
    runId: run.runId,
    authority,
    issue: {
      id: "issue-source", companyId: run.companyId, projectId: run.projectId,
      assigneeAgentId: "worker-1", executionRunId: run.runId,
    },
  };
}

describe("native completion review bridge tools", () => {
  let configDir = "";
  afterEach(async () => {
    if (configDir) {
      const { rm } = await import("node:fs/promises");
      await rm(configDir, { recursive: true, force: true });
      configDir = "";
    }
  });

  async function setup(current: PluginToolRunCurrent) {
    const { mkdtemp, writeFile } = await import("node:fs/promises");
    const { join } = await import("node:path");
    const { tmpdir } = await import("node:os");
    configDir = await mkdtemp(join(tmpdir(), "pc-native-review-"));
    const configPath = join(configDir, "owner.json");
    await writeFile(configPath, JSON.stringify(owner), { mode: 0o600 });
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const comments: string[] = [];
    const updates: unknown[] = [];
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      issues: {
        forCurrentToolRun: () => ({
          getCurrent: vi.fn(async () => current),
          listComments: vi.fn(async () => comments.map((body) => ({ body }))),
          createComment: vi.fn(async (_id: string, body: string) => { comments.push(body); return {}; }),
          update: vi.fn(async (...args: unknown[]) => { updates.push(args); return {}; }),
        }),
      },
      tools: { register: (name: string, _schema: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    return { ctx, handlers, comments, updates };
  }

  it("submits with host-derived worker identity and records an operation note on the source issue", async () => {
    const { ctx, handlers, comments, updates } = await setup(hostCurrent(workerRun, { kind: "assignee" }));
    const invoke = vi.fn(async () => ({ operation_key: operationKey, candidate_sha: candidateSha, base_sha: "d".repeat(40), submitted_sha: candidateSha, policy_digest: "e".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("submit_candidate")!({ base_sha: "d".repeat(40), submitted_sha: candidateSha }, workerRun);
    expect(invoke).toHaveBeenCalledWith("submit_candidate", { run_id: "worker-run", base_sha: "d".repeat(40), submitted_sha: candidateSha }, expect.any(String), "/usr/bin/python3");
    expect(comments[0]).toContain(`operation_key:${operationKey}`);
    expect(comments[0]).toContain(`worker_run_id:${workerRun.runId}`);
    expect(updates).toEqual([]);
  });

  it("requires the host's native review assignment and exact submitted candidate", async () => {
    const assignment = { kind: "native_completion_review", sourceRunId: workerRun.runId, interactionId: "interaction-1", decisionId: "decision-1", status: "pending" } as const;
    const { ctx, handlers, comments, updates } = await setup(hostCurrent(reviewerRun, assignment));
    comments.push([`brain-autonomous-bridge:${operationKey}:submitted`, `operation_key:${operationKey}`, `candidate_sha:${candidateSha}`, `worker_run_id:${workerRun.runId}`].join("\n"));
    const invoke = vi.fn(async () => ({ operation_key: operationKey, candidate_sha: candidateSha, inspection_digest: inspectionDigest, diff: "exact diff" }));
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("inspect_candidate")!({ operation_key: operationKey, candidate_sha: candidateSha }, reviewerRun) as { content: string };
    expect(invoke).toHaveBeenCalledWith("inspect_candidate", {
      reviewer_run_id: reviewerRun.runId, source_run_id: workerRun.runId,
      interaction_id: "interaction-1", decision_id: "decision-1", operation_key: operationKey, candidate_sha: candidateSha,
    }, expect.any(String), "/usr/bin/python3");
    expect(result.content).toContain("exact diff");
    expect(updates).toEqual([]);
  });

  it("custodies review evidence without changing issue status or waking a task", async () => {
    const assignment = { kind: "native_completion_review", sourceRunId: workerRun.runId, interactionId: "interaction-1", decisionId: "decision-1", status: "accepted" } as const;
    const { ctx, handlers, comments, updates } = await setup(hostCurrent(reviewerRun, assignment));
    comments.push([`brain-autonomous-bridge:${operationKey}:submitted`, `operation_key:${operationKey}`, `candidate_sha:${candidateSha}`, `worker_run_id:${workerRun.runId}`].join("\n"));
    const evidence = { inspection_digest: inspectionDigest, verdict: "accepted" };
    const invoke = vi.fn(async () => ({ status: "published", candidate_sha: candidateSha, evidence_digest: "f".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("approve_candidate")!({ operation_key: operationKey, candidate_sha: candidateSha, evidence }, reviewerRun);
    expect(invoke).toHaveBeenCalledWith("approve_candidate", expect.objectContaining({
      reviewer_run_id: reviewerRun.runId, source_run_id: workerRun.runId,
      interaction_id: "interaction-1", decision_id: "decision-1", native_verdict: "accepted", evidence,
    }), expect.any(String), "/usr/bin/python3");
    expect(comments).toHaveLength(1);
    expect(updates).toEqual([]);
  });

  it("records a resolved rejection without publishing or writing to the producer task", async () => {
    const assignment = { kind: "native_completion_review", sourceRunId: workerRun.runId, interactionId: "interaction-1", decisionId: "decision-1", status: "rejected" } as const;
    const { ctx, handlers, comments, updates } = await setup(hostCurrent(reviewerRun, assignment));
    comments.push([`brain-autonomous-bridge:${operationKey}:submitted`, `operation_key:${operationKey}`, `candidate_sha:${candidateSha}`, `worker_run_id:${workerRun.runId}`].join("\n"));
    const invoke = vi.fn(async () => ({ status: "review_rejected", candidate_sha: candidateSha, evidence_digest: "f".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("approve_candidate")!({ operation_key: operationKey, candidate_sha: candidateSha, evidence: { inspection_digest: inspectionDigest } }, reviewerRun);
    expect(invoke).toHaveBeenCalledWith("approve_candidate", expect.objectContaining({ native_verdict: "rejected" }), expect.any(String), "/usr/bin/python3");
    expect(comments).toHaveLength(1);
    expect(updates).toEqual([]);
  });

  it("requires a resolved native verdict and rejects caller-selected decisions", async () => {
    const pending = { kind: "native_completion_review", sourceRunId: workerRun.runId, interactionId: "interaction-1", decisionId: "decision-1", status: "pending" } as const;
    const { ctx, handlers, comments } = await setup(hostCurrent(reviewerRun, pending));
    comments.push([`brain-autonomous-bridge:${operationKey}:submitted`, `operation_key:${operationKey}`, `candidate_sha:${candidateSha}`, `worker_run_id:${workerRun.runId}`].join("\n"));
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("approve_candidate")!({ operation_key: operationKey, candidate_sha: candidateSha, evidence: { inspection_digest: inspectionDigest } }, reviewerRun)).rejects.toThrow("native_reviewer_context_mismatch");
    await expect(handlers.get("approve_candidate")!({ operation_key: operationKey, candidate_sha: candidateSha, approved: true, evidence: { inspection_digest: inspectionDigest } }, reviewerRun)).rejects.toThrow("tool_parameters_invalid");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("rejects a stale producer run against the exact submitted note before bridge calls", async () => {
    const stale = { kind: "native_completion_review", sourceRunId: "old-worker-run", interactionId: "interaction-1", decisionId: "decision-1", status: "pending" } as const;
    const { ctx, handlers, comments } = await setup(hostCurrent(reviewerRun, stale));
    comments.push([`brain-autonomous-bridge:${operationKey}:submitted`, `operation_key:${operationKey}`, `candidate_sha:${candidateSha}`, `worker_run_id:${workerRun.runId}`].join("\n"));
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("inspect_candidate")!({ operation_key: operationKey, candidate_sha: candidateSha }, reviewerRun)).rejects.toThrow("submitted_candidate_context_mismatch");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("rejects a host issue whose persisted execution run differs from the invocation", async () => {
    const current = hostCurrent(workerRun, { kind: "assignee" });
    current.issue.executionRunId = "old-run";
    const { ctx, handlers } = await setup(current);
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("submit_candidate")!({ base_sha: "d".repeat(40), submitted_sha: candidateSha }, workerRun)).rejects.toThrow("worker_actor_binding_mismatch");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("rejects a host-selected issue outside the owner-pinned source task", async () => {
    const current = hostCurrent(workerRun, { kind: "assignee" });
    current.issue.id = "other-issue";
    const { ctx, handlers } = await setup(current);
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("submit_candidate")!({ base_sha: "d".repeat(40), submitted_sha: candidateSha }, workerRun)).rejects.toThrow("worker_actor_binding_mismatch");
    expect(invoke).not.toHaveBeenCalled();
  });
});
