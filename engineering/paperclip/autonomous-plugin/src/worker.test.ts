import { afterEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";

vi.mock("@paperclipai/plugin-sdk", () => ({
  definePlugin: (value: unknown) => value,
  runWorker: vi.fn(),
}));

import { registerAutonomousTools } from "./worker.js";
import type { PluginContext, ToolRunContext } from "@paperclipai/plugin-sdk";

const config = {
  schema: "paperclip-autonomous-bridge-config-v1",
  company_id: "company-1",
  project_id: "project-1",
  worker_agent_id: "worker-1",
  reviewer_agent_id: "reviewer-1",
  source_issue_id: "issue-worker",
  reviewer_issue_id: "issue-reviewer",
  python_executable: "/usr/bin/python3",
};

const workerRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "worker-1", runId: "worker-run" };
const reviewerRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "reviewer-1", runId: "reviewer-run" };
const contentFiles = [
  ["src/memecoin_brain_sources/events.py", "events = []\n"],
  ["tests/brain/test_raw_sources.py", "def test_raw_sources():\n    assert True\n"],
].map(([path_id, content]) => ({ path_id, content, size: Buffer.byteLength(content), sha256: createHash("sha256").update(content).digest("hex") }));

function fixture(
  configPath: string,
  reviewerStatus: string = "backlog",
  reviewerOverrides: Record<string, unknown> = {},
  reviewerBlockers: string[] = ["issue-worker"],
  reviewerParentId: string = "issue-worker",
  sourceParentId?: string,
) {
  const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
  const issueReads: string[] = [];
  const notes = new Map<string, string[]>();
  const updates: Array<{ issueId: string; patch: unknown }> = [];
  const lifecycle: string[] = [];
  const issue = (id: string, runCtx: ToolRunContext) => ({
    id,
    companyId: runCtx.companyId,
    projectId: runCtx.projectId,
    ...(id === "issue-reviewer" ? { parentId: reviewerParentId } : id === "issue-worker" && sourceParentId ? { parentId: sourceParentId } : {}),
    assigneeAgentId: runCtx.agentId,
    status: id === "issue-reviewer" ? reviewerStatus : "in_progress",
    executionRunId: id === "issue-reviewer" && reviewerStatus !== "in_progress" ? null : runCtx.runId,
    ...(id === "issue-reviewer" ? reviewerOverrides : {}),
  });
  const ctx = {
    config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
    issues: {
      forCurrentToolRun: () => ({
      get: vi.fn(async (id: string, _companyId: string) => {
        issueReads.push(id);
        if (id === "issue-worker") return issue(id, workerRun) as never;
        if (id === "issue-reviewer") return issue(id, reviewerRun) as never;
        return null;
      }),
      getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
          issueId,
          companyId: "company-1",
          runs: [{
            id: issueId === "issue-worker" ? workerRun.runId : reviewerRun.runId,
            issueId,
            agentId: issueId === "issue-worker" ? workerRun.agentId : reviewerRun.agentId,
            status: "running",
            invocationSource: "issue",
            triggerDetail: null,
            startedAt: new Date().toISOString(),
            finishedAt: null,
            error: null,
            createdAt: new Date().toISOString(),
          }],
          blockedByIssueIds: issueId === "issue-reviewer" ? reviewerBlockers : [],
        })) as never,
      listComments: vi.fn(async (id: string) => (notes.get(id) ?? []).map((body) => ({ body })) as never),
      createComment: vi.fn(async (id: string, body: string) => { lifecycle.push(`comment:${id}`); notes.set(id, [...(notes.get(id) ?? []), body]); return {} as never; }),
      update: vi.fn(async (issueId: string, status: string) => { const patch = { status }; lifecycle.push(`update:${issueId}:${status}`); updates.push({ issueId, patch }); return {} as never; }),
      }),
    },
    tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
  } as unknown as PluginContext;
  return { ctx, handlers, issueReads, notes, updates, lifecycle };
}

describe("Paperclip autonomous bridge tools", () => {
  let configPath = "";
  let configDir = "";
  afterEach(async () => {
    if (configDir) {
      const { rm } = await import("node:fs/promises");
      await rm(configDir, { recursive: true, force: true }).catch(() => undefined);
      configPath = "";
      configDir = "";
    }
  });

  async function installConfig() {
    const { mkdtemp, writeFile } = await import("node:fs/promises");
    const { join } = await import("node:path");
    const { tmpdir } = await import("node:os");
    const dir = await mkdtemp(join(tmpdir(), "pc-bridge-test-"));
    configDir = dir;
    configPath = join(dir, "owner.json");
    await writeFile(configPath, JSON.stringify(config), { mode: 0o600 });
  }

  it("uses only host context and delivers bounded source contents", async () => {
    await installConfig();
    const { ctx, handlers, issueReads, notes, updates, lifecycle } = fixture(configPath);
    const invoke = vi.fn(async () => ({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40), base_sha: "c".repeat(40), submitted_sha: "d".repeat(40), policy_digest: "e".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    expect(handlers.has("submit_candidate")).toBe(false);
    const result = await handlers.get("deliver_source_content")!({ base_sha: "c".repeat(40), files: contentFiles }, workerRun) as { content: string };
    expect(issueReads).toEqual(["issue-worker", "issue-reviewer", "issue-reviewer"]);
    expect(invoke).toHaveBeenCalledWith("deliver_source_content", expect.objectContaining({
      company_id: "company-1", project_id: "project-1", worker_agent_id: "worker-1", run_id: "worker-run", source_issue_id: "issue-worker",
      files: contentFiles,
    }), configPath, "/usr/bin/python3");
    expect(result.content).toBe(`operation_key:${"a".repeat(64)}\ncandidate_sha:${"b".repeat(40)}`);
    expect(notes.get("issue-reviewer")?.[0]).toContain(`candidate_sha:${"b".repeat(40)}`);
    expect(updates).toEqual([{ issueId: "issue-reviewer", patch: { status: "todo" } }]);
    expect(lifecycle).toEqual([
      "comment:issue-reviewer",
      "update:issue-reviewer:todo",
    ]);
  });

  it("rejects a reviewer with the worker identity before invoking the bridge", async () => {
    await installConfig();
    const { ctx, handlers, issueReads } = fixture(configPath);
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("approve_candidate")!({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40), approved: true, evidence: { inspection_digest: "c".repeat(64) } }, workerRun)).rejects.toThrow("reviewer_actor_binding_mismatch");
    expect(issueReads).toEqual([]);
    expect(invoke).not.toHaveBeenCalled();
  });

  it("does not submit or wake an incorrectly assigned or already active reviewer issue", async () => {
    await installConfig();
    const { ctx, handlers } = fixture(configPath, "in_progress");
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("deliver_source_content")!({ base_sha: "c".repeat(40), files: contentFiles }, workerRun))
      .rejects.toThrow("reviewer_issue_wake_target_mismatch");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("allows an owner-bound sibling reviewer and verifies both nominated parents", async () => {
    await installConfig();
    const { readFile, writeFile } = await import("node:fs/promises");
    const configured = { ...config, reviewer_parent_issue_id: "issue-parent" };
    await writeFile(configPath, JSON.stringify(configured), { mode: 0o600 });
    const { ctx, handlers } = fixture(configPath, "backlog", {}, ["issue-worker"], "issue-parent", "issue-parent");
    const invoke = vi.fn(async () => ({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("deliver_source_content")!({ base_sha: "c".repeat(40), files: [] }, workerRun);
    expect(invoke).toHaveBeenCalledTimes(1);

    const unrelated = fixture(configPath, "backlog", {}, ["issue-worker"], "issue-parent", "other-parent");
    const unrelatedInvoke = vi.fn();
    registerAutonomousTools(unrelated.ctx, unrelatedInvoke as never);
    await expect(unrelated.handlers.get("deliver_source_content")!({ base_sha: "c".repeat(40), files: [] }, workerRun))
      .rejects.toThrow("reviewer_issue_wake_target_mismatch");
    expect(unrelatedInvoke).not.toHaveBeenCalled();
    expect(await readFile(configPath, "utf8")).toContain("reviewer_parent_issue_id");
  });

  it("rejects reviewer issues outside the owner-pinned company/project/assignee", async () => {
    await installConfig();
    const { ctx, handlers } = fixture(configPath, "backlog", { assigneeAgentId: "unrelated-agent" });
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("deliver_source_content")!({ base_sha: "c".repeat(40), files: contentFiles }, workerRun))
      .rejects.toThrow("reviewer_issue_wake_target_mismatch");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("checks the exact native source blocker before invoking the bridge", async () => {
    await installConfig();
    const { ctx, handlers, notes, updates } = fixture(configPath, "backlog", {}, ["issue-worker", "unrelated-blocker"]);
    const invoke = vi.fn();
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("deliver_source_content")!({ base_sha: "c".repeat(40), files: contentFiles }, workerRun))
      .rejects.toThrow("reviewer_issue_wake_target_mismatch");
    expect(invoke).not.toHaveBeenCalled();
    expect(notes.get("issue-reviewer")).toBeUndefined();
    expect(updates).toEqual([]);
  });

  it("lets only the configured reviewer inspect an exact candidate in its bound issue run", async () => {
    await installConfig();
    const { ctx, handlers, issueReads } = fixture(configPath, "in_progress");
    const diff = "diff --git a/engineering/example.py b/engineering/example.py\n+trusted";
    const invoke = vi.fn(async () => ({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40), inspection_digest: "c".repeat(64), diff }));
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("inspect_candidate")!({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40) }, reviewerRun) as { content: string };
    expect(issueReads).toEqual(["issue-reviewer"]);
    expect(invoke).toHaveBeenCalledWith("inspect_candidate", expect.objectContaining({
      reviewer_agent_id: "reviewer-1", reviewer_issue_id: "issue-reviewer", reviewer_run_id: "reviewer-run",
      operation_key: "a".repeat(64), candidate_sha: "b".repeat(40),
    }), configPath, "/usr/bin/python3");
    expect(result.content).toContain(diff);
    await expect(handlers.get("inspect_candidate")!({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40) }, workerRun)).rejects.toThrow("reviewer_actor_binding_mismatch");
  });

  it("requires the SDK issue executionRunId and orchestration run summary to match the host run", async () => {
    const { assertRunBoundIssue } = await import("./worker.js");
    const ctx = { issues: {
      forCurrentToolRun: () => ({
      get: vi.fn(async () => ({
        id: "issue-worker", companyId: "company-1", projectId: "project-1", assigneeAgentId: "worker-1", status: "in_progress",
        executionRunId: "some-other-run",
      })),
      getOrchestration: vi.fn(async () => ({ runs: [{ id: "worker-run", issueId: "issue-worker", agentId: "worker-1", status: "running" }] })),
      }),
    } } as unknown as PluginContext;
    await expect(assertRunBoundIssue(ctx, workerRun, "issue-worker")).rejects.toThrow("host_issue_run_binding_mismatch");
  });

  it("persists exact reviewer evidence without changing producer or reviewer status", async () => {
    await installConfig();
    const { ctx, handlers, notes, updates } = fixture(configPath, "in_progress");
    const evidence = { inspection_digest: "d".repeat(64), inspected_paths: ["engineering/example.py"], verdict: "accepted" };
    const invoke = vi.fn(async () => ({ status: "published", candidate_sha: "b".repeat(40), evidence_digest: "c".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("approve_candidate")!({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40), approved: true, evidence }, reviewerRun);
    expect(notes.get("issue-worker")?.[0]).toContain(`evidence:${JSON.stringify(evidence)}`);
    expect(updates).toEqual([]);
  });

  it("preserves producer completion after rejection without automatic dispatch", async () => {
    await installConfig();
    const { ctx, handlers, updates } = fixture(configPath, "in_progress");
    const invoke = vi.fn(async () => ({ status: "review_rejected", candidate_sha: "b".repeat(40), evidence_digest: "c".repeat(64), operation_key: "a".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("approve_candidate")!({ operation_key: "a".repeat(64), candidate_sha: "b".repeat(40), approved: false, evidence: { inspection_digest: "d".repeat(64), reason: "needs correction" } }, reviewerRun);
    expect(updates).toEqual([]);
  });
});
