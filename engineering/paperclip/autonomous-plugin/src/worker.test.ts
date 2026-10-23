import { afterEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";

vi.mock("@paperclipai/plugin-sdk", () => ({
  definePlugin: (value: unknown) => value,
  runWorker: vi.fn(),
}));

import { registerAutonomousTools } from "./worker.js";
import manifest from "./manifest.js";
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
    description: id === "issue-worker" ? "source instructions" : "review instructions",
    projectWorkspaceId: "workspace-project",
    executionWorkspaceId: "workspace-execution",
    ...(id === "issue-reviewer" ? { parentId: reviewerParentId } : id === "issue-worker" && sourceParentId ? { parentId: sourceParentId } : {}),
    assigneeAgentId: runCtx.agentId,
    status: id === "issue-reviewer" ? reviewerStatus : "in_progress",
    executionRunId: id === "issue-reviewer" && reviewerStatus !== "in_progress" ? null : runCtx.runId,
    ...(id === "issue-reviewer" ? reviewerOverrides : {}),
  });
  const ctx = {
    config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
    executionWorkspaces: { get: vi.fn(async (id: string) => ({
      id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
      path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
      repoUrl: null, baseRef: null, branchName: null, providerType: "local", providerMetadata: null,
    })) },
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

  it("declares the finite stage tools in the native plugin manifest", () => {
    expect(manifest.tools.map((tool) => tool.name)).toEqual(expect.arrayContaining([
      "bind_source_stage",
      "deliver_source_stage_content",
      "inspect_source_stage_candidate",
      "approve_source_stage_candidate",
    ]));
  });

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

  it("CAS-activates only the source after durable binding and holds an ambiguous ACK without retry", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project", source_workspace_root: "/tmp/brain-source-workspace", source_stage_catalog: [{
        stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40),
        predecessor_stage_key: null,
        path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
        verifier_policy_id: "policy-mem8",
      }, {
        stage_key: "MEM9", workspace_id: "published", initial_base_sha: null,
        predecessor_stage_key: "MEM8",
        path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
        verifier_policy_id: "policy-mem9",
      }], python_executable: "/usr/bin/python3",
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const leadRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "lead-1", runId: "lead-run" };
    const issue = (id: string) => ({
      id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
      description: id === "source-8" ? "source instructions" : "review instructions",
      projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
      assigneeAgentId: id === "source-8" ? "worker-1" : id === "review-8" ? "reviewer-1" : "lead-1",
      status: id === "lead-issue" ? "in_progress" : id === "source-8" ? "backlog" : "blocked",
      statusVersion: id === "source-8" ? 4 : 7,
      executionRunId: id === "lead-issue" ? "lead-run" : null,
    });
    const updateStatusCompareAndSet = vi.fn(async () => { throw new Error("native_ack_unknown"); });
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local", providerMetadata: null,
      })) },
      issues: { forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => issue(id) as never),
        getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
          issueId, blockedByIssueIds: issueId === "review-8" ? ["source-8"] : [],
          runs: issueId === "lead-issue" ? [{ id: "lead-run", issueId, agentId: "lead-1", status: "running" }] : [],
        })) as never,
        getDependencyReadiness: vi.fn(async () => ({
          isDependencyReady: false, unresolvedBlockerIssueIds: ["source-8"], pendingFinalizeBlockerIssueIds: [],
        })) as never,
        updateStatusCompareAndSet,
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    let intentCalls = 0;
    const invoke = vi.fn(async (action: string) => {
      if (action === "lookup_source_stage_binding") return { found: false };
      if (action === "bind_source_stage") return { stage_binding_key: "a".repeat(64), base_sha: "b".repeat(40), state: "bound" };
      intentCalls += 1;
      return { intent: intentCalls === 1 ? "created" : "reconcile_required" };
    });
    registerAutonomousTools(ctx, invoke as never);
    const params = { stage_key: "MEM8", source_issue_id: "source-8", review_issue_id: "review-8" };
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("native_ack_unknown");
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("source_stage_activation_reconciliation_required");
    expect(updateStatusCompareAndSet).toHaveBeenCalledTimes(1);
    expect(updateStatusCompareAndSet).toHaveBeenCalledWith("source-8", "todo", 4, "company-1");
    expect(invoke.mock.calls.filter(([action]) => action === "source_stage_activation")).toHaveLength(2);
  });

  it("source content delivery touches its own task and direct lead only", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project", source_workspace_root: "/tmp/brain-source-workspace", source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ], python_executable: "/usr/bin/python3",
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const sourceRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "worker-1", runId: "source-run" };
    const issueReads: string[] = [];
    const commentTargets: string[] = [];
    const scopedOrchestration = vi.fn();
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local", providerMetadata: null,
      })) },
      issues: { forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => {
          issueReads.push(id);
          if (id !== "source-8" && id !== "lead-issue") throw new Error("sibling_issue_access_denied");
          if (id === "lead-issue") return { id, companyId: "company-1", projectId: "project-1",
            projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
            assigneeAgentId: "lead-1", status: "in_progress", executionRunId: null } as never;
          return { id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
            projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
            assigneeAgentId: "worker-1", status: "in_progress", executionRunId: "source-run" } as never;
        }),
        getOrchestration: scopedOrchestration.mockImplementation(async ({ issueId }: { issueId: string }) => ({
          runs: [{ id: "source-run", issueId, agentId: "worker-1", status: "running" }], blockedByIssueIds: [],
        })) as never,
        listComments: vi.fn(async (id: string) => {
          commentTargets.push(`list:${id}`);
          if (id !== "lead-issue") throw new Error("sibling_comment_access_denied");
          return [] as never;
        }),
        createComment: vi.fn(async (id: string) => {
          commentTargets.push(`create:${id}`);
          if (id !== "lead-issue") throw new Error("sibling_comment_access_denied");
          return {} as never;
        }),
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async () => ({ operation_key: "c".repeat(64), candidate_sha: "d".repeat(40), base_sha: "b".repeat(40), submitted_sha: "e".repeat(40), policy_digest: "f".repeat(64) }));
    registerAutonomousTools(ctx, invoke as never);
    await handlers.get("deliver_source_stage_content")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), source_issue_id: "source-8", files: contentFiles,
    }, sourceRun);
    expect(issueReads).toEqual(["source-8", "source-8", "lead-issue", "source-8"]);
    expect(commentTargets).toEqual(["list:lead-issue", "create:lead-issue"]);
    expect(scopedOrchestration).not.toHaveBeenCalled();
    expect(invoke).toHaveBeenCalledWith("deliver_source_stage_content", {
      company_id: "company-1", project_id: "project-1", worker_agent_id: "worker-1",
      run_id: "source-run", source_issue_id: "source-8", stage_key: "MEM8",
      stage_binding_key: "a".repeat(64), files: contentFiles,
    }, configPath, "/usr/bin/python3");
  });

  it("stage review derives its candidate from the binding without reading the producer sibling", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project", source_workspace_root: "/tmp/brain-source-workspace", source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ], python_executable: "/usr/bin/python3",
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const issueReads: string[] = [];
    const scopedOrchestration = vi.fn();
    const reviewerTaskRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "reviewer-1", runId: "reviewer-run" };
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local", providerMetadata: null,
      })) },
      issues: { forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => {
          issueReads.push(id);
          if (id !== "review-8" && id !== "lead-issue") throw new Error("sibling_issue_access_denied");
          if (id === "lead-issue") return { id, companyId: "company-1", projectId: "project-1",
            projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
            assigneeAgentId: "lead-1", status: "in_progress", executionRunId: null } as never;
          return { id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
            projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
            assigneeAgentId: "reviewer-1", status: "in_progress", executionRunId: "reviewer-run" } as never;
        }),
        getOrchestration: scopedOrchestration.mockImplementation(async ({ issueId }: { issueId: string }) => ({
          runs: [{ id: "reviewer-run", issueId, agentId: "reviewer-1", status: "running" }], blockedByIssueIds: ["source-8"],
        })) as never,
        getDependencyReadiness: vi.fn(async () => ({
          isDependencyReady: true, unresolvedBlockerIssueIds: [], pendingFinalizeBlockerIssueIds: [],
        })) as never,
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async () => ({ operation_key: "c".repeat(64), candidate_sha: "d".repeat(40), inspection_digest: "e".repeat(64), diff: "exact diff" }));
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("inspect_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
    }, reviewerTaskRun) as { content: string };
    expect(issueReads).toEqual(["review-8", "review-8", "lead-issue", "review-8"]);
    expect(scopedOrchestration).not.toHaveBeenCalled();
    expect(invoke).toHaveBeenCalledWith("inspect_bound_source_stage", expect.objectContaining({
      reviewer_issue_id: "review-8", stage_binding_key: "a".repeat(64), stage_key: "MEM8",
    }), configPath, "/usr/bin/python3");
    expect(result.content).toContain("exact diff");
  });
});
