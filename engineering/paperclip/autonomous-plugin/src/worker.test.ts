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
const digest = (value: string) => createHash("sha256").update(value, "utf8").digest("hex");
const contentFiles = [
  ["src/memecoin_brain_sources/events.py", "events = []\n"],
  ["tests/brain/test_raw_sources.py", "def test_raw_sources():\n    assert True\n"],
].map(([path_id, content]) => ({ path_id, content, size: Buffer.byteLength(content), sha256: createHash("sha256").update(content).digest("hex") }));

function acceptedBaseContextNote(
  stageKey: string,
  pathIds: string[],
  stageBindingKey = "a".repeat(64),
  baseSha = "b".repeat(40),
  options: { projectId?: string; leadIssueId?: string; baseRef?: string | null; published?: boolean } = {},
) {
  const files = pathIds.map((path_id, index) => {
    const content = `accepted ${stageKey} base ${index} — café\n`;
    const size_bytes = Buffer.byteLength(content, "utf8");
    return {
      path_id, present: true, mode: "100644",
      blob_sha: createHash("sha1").update(`blob ${size_bytes}\0`).update(content, "utf8").digest("hex"),
      size_bytes, content_sha256: digest(content), content,
    };
  });
  const context: Record<string, unknown> = {
    schema: "paperclip-source-stage-accepted-base-context-v1",
    stage_key: stageKey,
    stage_binding_key: stageBindingKey,
    owner_config_digest: "c".repeat(64),
    project_id: options.projectId ?? "project-1",
    lead_issue_id: options.leadIssueId ?? "lead-issue",
    accepted_base_sha: baseSha,
    accepted_canonical_base_sha: null,
    accepted_base_ref: options.baseRef ?? null,
    repository_role: options.published ? "owner_pinned_published_base" : "owner_pinned_stage_source_base",
    path_ids: pathIds,
    files,
    total_size_bytes: files.reduce((total, file) => total + file.size_bytes, 0),
  };
  const canonical = (value: unknown): string => {
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    if (value && typeof value === "object") {
      const object = value as Record<string, unknown>;
      return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${canonical(object[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  };
  context.context_sha256 = digest(canonical(context));
  const serialized = canonical(context);
  const fields = [
    `stage_key:${stageKey}`,
    `stage_binding_key:${stageBindingKey}`,
    `accepted_base_sha:${baseSha}`,
    `accepted_base_ref:${context.accepted_base_ref}`,
    `repository_role:${context.repository_role}`,
    "context_scope:exact_owner_admitted_paths_only",
    "comparison_scope:bounded_base_content_not_full_repository_equivalence",
    "rejected_retry_seed:excluded",
    `accepted_base_context_sha256:${context.context_sha256}`,
    `accepted_base_context_json:${serialized}`,
  ];
  const idempotency_key = `${stageBindingKey}:accepted-base-context`;
  const body = [
    `brain-autonomous-bridge:${idempotency_key}`,
    `brain-source-stage:${stageBindingKey}:accepted-base-context`,
    ...fields,
    `accepted_base_context_note_sha256:${digest(fields.join("\n"))}`,
  ].join("\n");
  return { idempotency_key, body };
}

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
      repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs", providerMetadata: null,
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

  async function installConfig(value: unknown = config) {
    const { mkdtemp, writeFile } = await import("node:fs/promises");
    const { join } = await import("node:path");
    const { tmpdir } = await import("node:os");
    const dir = await mkdtemp(join(tmpdir(), "pc-bridge-test-"));
    configDir = dir;
    configPath = join(dir, "owner.json");
    await writeFile(configPath, JSON.stringify(value), { mode: 0o600 });
  }

  it("declares the finite stage tools in the native plugin manifest", () => {
    expect(manifest.tools.map((tool) => tool.name)).toEqual(expect.arrayContaining([
      "bind_source_stage",
      "deliver_source_stage_content",
      "inspect_source_stage_candidate",
      "approve_source_stage_candidate",
    ]));
    const bindTool = manifest.tools.find((tool) => tool.name === "bind_source_stage");
    expect((bindTool?.parametersSchema as { properties?: Record<string, { maximum?: number }> })
      .properties?.attempt?.maximum).toBe(3);
  });

  it("returns the program envelope only to the current Astra-created Sol program run", async () => {
    const admission = {
      missions: [{
        project_id: "senses-project", standing_issue_id: "standing-90", sol_agent_id: "sol-agent",
        astra_agent_id: "astra-agent", luna_agent_id: "luna-agent", qa_agent_ids: ["qa-agent"],
        goal_ids: ["mission-goal"], allowed_prefixes: ["src/memecoin_brain_sources/", "tests/brain/"],
        excluded_prefixes: ["src/memecoin_brain_sources/private/"], corpus_ids: ["brain"], profiles: [{
          profile_id: "senses-v1", allowed_path_ids: ["src/memecoin_brain_sources/events.py"],
          allowed_prefixes: ["src/memecoin_brain_sources/"], required_test_paths: ["tests/brain/test_raw_sources.py"],
          verifier_policy_id: "mem-senses-slot-version-v1",
        }], source_repo: "/tmp/brain-source", publisher_repo: "/tmp/brain-publisher",
        canonical_repo: "/tmp/brain-canonical", canonical_ref: "refs/heads/brain/development",
        expected_dev_ref: "refs/heads/brain/development", source_workspace_id: "senses-workspace",
        source_workspace_root: "/tmp/senses-workspace",
      }],
    };
    await installConfig({ ...config, worker_agent_id: "luna-agent", reviewer_agent_id: "qa-agent", program_admission: admission,
      accepted_source_reader: {
        projections: [{ corpus_id: "brain", repository: "/tmp/brain-projection", ref: "refs/heads/brain/development",
          allowed_roots: ["src/", "tests/"], bootstrap_projection_sha: "a".repeat(40),
          bootstrap_source_sha: "b".repeat(40), bootstrap_receipt_digest: "c".repeat(64) }],
        programs: [{ project_id: "old-project", program_issue_id: "old-program", sol_agent_id: "old-sol",
          luna_agent_id: "old-luna", qa_agent_ids: [], corpus_ids: ["brain"] }],
      } });
    const { ctx, handlers } = fixture(configPath);
    const solRun = { ...workerRun, projectId: "senses-project", agentId: "sol-agent", runId: "sol-run" };
    const lead = { id: "sol-program", companyId: "company-1", projectId: "senses-project", parentId: "astra-program",
      createdByAgentId: "astra-agent", assigneeAgentId: "sol-agent", goalId: "mission-goal", status: "in_progress", executionRunId: "sol-run",
      projectWorkspaceId: "senses-workspace", executionWorkspaceId: "dynamic-execution" };
    const parent = { id: "astra-program", companyId: "company-1", projectId: "previous-project", assigneeAgentId: "astra-agent", status: "in_progress" };
    const issueApi = ctx.issues as unknown as Record<string, unknown>;
    issueApi.list = vi.fn(async () => [lead]);
    issueApi.get = vi.fn(async (id: string) => id === parent.id ? parent : null);
    issueApi.forCurrentToolRun = () => ({
      get: vi.fn(async (id: string) => id === lead.id ? lead : null),
      getOrchestration: vi.fn(async () => ({ issueId: lead.id, companyId: "company-1", runs: [{
        id: solRun.runId, issueId: lead.id, agentId: solRun.agentId, status: "running",
      }] })),
    });
    const invoke = vi.fn(async (_action, payload) => ({
      status: "ok", project_id: payload.project_id, standing_issue_id: "standing-90", goal_id: "mission-goal",
      sol_agent_id: "sol-agent", astra_agent_id: "astra-agent", luna_agent_id: "luna-agent", qa_agent_ids: ["qa-agent"],
      allowed_prefixes: ["src/memecoin_brain_sources/", "tests/brain/"],
      excluded_prefixes: ["src/memecoin_brain_sources/private/"], corpus_ids: ["brain"], profiles: admission.missions[0].profiles,
    }));
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("get_program_source_scope")!({}, solRun as ToolRunContext) as { content: string };
    const response = JSON.parse(result.content);
    expect(response.program_issue_id).toBe("sol-program");
    expect(response.actual_parent_issue_id).toBe("astra-program");
    expect(response.profiles[0].required_test_paths).toEqual(["tests/brain/test_raw_sources.py"]);
    expect(invoke).toHaveBeenCalledWith("get_program_source_scope", {
      company_id: "company-1", project_id: "senses-project", goal_id: "mission-goal",
    }, configPath, "/usr/bin/python3");
    const proposalInvoke = vi.fn(async () => ({ status: "admitted", project_id: "senses-project", goal_id: "mission-goal",
      profile_id: "senses-v1", verifier_policy_id: "mem-senses-slot-version-v1",
      path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
      required_test_paths: ["tests/brain/test_raw_sources.py"] }));
    registerAutonomousTools(ctx, proposalInvoke as never);
    const proposed = await handlers.get("validate_program_source_proposal")!({ profile_id: "senses-v1", path_ids: [
      "src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py",
    ] }, solRun as ToolRunContext) as { content: string };
    expect(JSON.parse(proposed.content).verifier_policy_id).toBe("mem-senses-slot-version-v1");
    await expect(handlers.get("validate_program_source_proposal")!({ profile_id: "senses-v1", path_ids: [
      "src/memecoin_brain_sources/private/secret.py", "tests/brain/test_raw_sources.py",
    ] }, solRun as ToolRunContext)).rejects.toThrow("program_source_proposal_response_invalid");
    const readerInvoke = vi.fn(async (_action, payload) => ({
      status: "ok", program_issue_id: payload.program_issue_id, issue_id: payload.issue_id, corpus_id: "brain",
      projection_sha: "a".repeat(40), canonical_sha: "b".repeat(40),
      acceptance_receipt_digest: "c".repeat(64), manifest_digest: "d".repeat(64),
      result: { path: "src/memecoin_brain_sources/events.py", lines: [{ line: 1, text: "VALUE = 1" }] },
    }));
    registerAutonomousTools(ctx, readerInvoke as never);
    const read = await handlers.get("read_accepted_source")!({ corpus_id: "brain", path: "src/memecoin_brain_sources/events.py", start_line: 1, line_count: 1 }, solRun as ToolRunContext) as { content: string };
    expect(JSON.parse(read.content).program_issue_id).toBe("sol-program");
    expect(readerInvoke).toHaveBeenCalledWith("read_accepted_source", expect.objectContaining({
      project_id: "senses-project", program_issue_id: "sol-program", issue_id: "sol-program", corpus_id: "brain",
    }), configPath, "/usr/bin/python3");
    const source = { id: "dynamic-source", companyId: "company-1", projectId: "senses-project", parentId: lead.id,
      createdByAgentId: "sol-agent", assigneeAgentId: "luna-agent", goalId: "mission-goal", status: "backlog",
      executionRunId: null, statusVersion: 1, description: "Implement the selected source change",
      projectWorkspaceId: "senses-workspace", executionWorkspaceId: "dynamic-execution" };
    const review = { id: "dynamic-review", companyId: "company-1", projectId: "senses-project", parentId: lead.id,
      createdByAgentId: "sol-agent", assigneeAgentId: "qa-agent", goalId: "mission-goal", status: "blocked",
      executionRunId: null, statusVersion: 1, description: "Review source merits independently",
      projectWorkspaceId: "senses-workspace", executionWorkspaceId: "dynamic-execution" };
    const currentIssues = new Map([[lead.id, lead], [source.id, source], [review.id, review], [parent.id, parent]]);
    issueApi.forCurrentToolRun = () => ({
      get: vi.fn(async (id: string) => currentIssues.get(id) ?? null),
      getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
        issueId, companyId: "company-1", runs: [{ id: solRun.runId, issueId: lead.id, agentId: solRun.agentId, status: "running" }],
        blockedByIssueIds: issueId === review.id ? [source.id] : [],
      })),
      getDependencyReadiness: vi.fn(async () => ({ isDependencyReady: false, unresolvedBlockerIssueIds: [source.id] })),
      updateStatusCompareAndSet: vi.fn(async () => ({ status: "todo", statusVersion: 2 })),
      listComments: vi.fn(async () => []),
      createComment: vi.fn(async () => ({})),
    });
    issueApi.get = vi.fn(async (id: string) => currentIssues.get(id) ?? null);
    (ctx.executionWorkspaces as unknown as { get: unknown }).get = vi.fn(async () => ({
      projectWorkspaceId: "senses-workspace", path: "/tmp/senses-workspace", cwd: "/tmp/senses-workspace",
      repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs",
    }));
    const bindInvoke = vi.fn(async (action: string, payload: Record<string, unknown>) => {
      if (action === "validate_program_source_proposal") return { status: "admitted", project_id: "senses-project",
        goal_id: "mission-goal", profile_id: "senses-v1", verifier_policy_id: "mem-senses-slot-version-v1",
        path_ids: payload.path_ids, required_test_paths: ["tests/brain/test_raw_sources.py"] };
      if (action === "bind_program_source_stage") return { stage_key: "DYN-0123456789abcdef0123456789abcdef",
        stage_binding_key: "f".repeat(64), base_sha: "a".repeat(40), canonical_base_sha: "b".repeat(40), state: "bound" };
      if (action === "source_stage_activation" && payload.step === "intent") return { intent: "created" };
      if (action === "source_stage_activation" && payload.step === "ack") return { intent: "acknowledged" };
      throw new Error(`unexpected bridge action: ${action}`);
    });
    registerAutonomousTools(ctx, bindInvoke as never);
    const bound = await handlers.get("bind_program_source_stage")!({ profile_id: "senses-v1", corpus_id: "brain",
      path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
      source_issue_id: source.id, review_issue_id: review.id }, solRun as ToolRunContext) as { content: string };
    expect(bound.content).toContain("state:active");
    expect(bindInvoke).toHaveBeenCalledWith("bind_program_source_stage", expect.objectContaining({
      lead_issue_id: lead.id, source_issue_id: source.id, review_issue_id: review.id,
      path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
    }), configPath, "/usr/bin/python3");
    const wrongActor = { ...solRun, agentId: "luna-agent" };
    await expect(handlers.get("get_program_source_scope")!({}, wrongActor as ToolRunContext)).rejects.toThrow("program_source_scope_actor_not_admitted");
  });

  it("routes accepted-source reads only from the active run under an admitted program", async () => {
    const acceptedSourceConfig = {
      ...config,
      project_id: "mem17-project",
      finite_lead_agent_id: "mem17-lead",
      finite_lead_issue_id: "mem17-lead-issue",
      source_workspace_id: "mem17-workspace",
      source_workspace_root: "/tmp/mem17-workspace",
      source_stage_catalog: [{
        stage_key: "MEM17", workspace_id: "source", initial_base_sha: "9".repeat(40),
        predecessor_stage_key: null, project_id: "mem17-project", goal_id: "mem17-goal",
        lead_agent_id: "mem17-lead", lead_issue_id: "mem17-lead-issue",
        source_workspace_id: "mem17-workspace", source_workspace_root: "/tmp/mem17-workspace",
        path_ids: ["src/example.py", "tests/test_example.py"], verifier_policy_id: "policy-mem17",
      }],
      accepted_source_reader: {
        projections: [{
          corpus_id: "platform", repository: "/tmp/sanitized-projection", ref: "refs/heads/org/development",
          allowed_roots: ["engineering/"], bootstrap_projection_sha: "a".repeat(40),
          bootstrap_source_sha: "b".repeat(40), bootstrap_receipt_digest: "c".repeat(64),
        }],
        programs: [{
          project_id: "mem89-project", program_issue_id: "mem89-issue", sol_agent_id: workerRun.agentId,
          luna_agent_id: "luna-agent", qa_agent_ids: [], corpus_ids: ["platform"],
        }],
      },
    };
    await installConfig(acceptedSourceConfig);
    const { ctx, handlers } = fixture(configPath);
    const readerRun = { ...workerRun, projectId: "mem89-project" };
    const currentIssue = {
      id: "mem89-child", companyId: workerRun.companyId, projectId: readerRun.projectId,
      parentId: "mem89-issue", assigneeAgentId: workerRun.agentId, status: "in_progress",
      executionRunId: workerRun.runId,
    };
    const programIssue = {
      id: "mem89-issue", companyId: workerRun.companyId, projectId: readerRun.projectId,
      parentId: null, assigneeAgentId: workerRun.agentId, status: "in_progress", executionRunId: null,
    };
    const issueApi = ctx.issues as unknown as Record<string, unknown>;
    issueApi.list = vi.fn(async () => [currentIssue]);
    issueApi.get = vi.fn(async (id: string) => id === programIssue.id ? programIssue : null);
    issueApi.forCurrentToolRun = () => ({
      get: vi.fn(async (id: string) => id === currentIssue.id ? currentIssue : null),
      getOrchestration: vi.fn(async () => ({ issueId: currentIssue.id, companyId: workerRun.companyId, runs: [{
        id: workerRun.runId, issueId: currentIssue.id, agentId: workerRun.agentId, status: "running",
      }] })),
    });
    const invoke = vi.fn(async () => ({
      status: "ok", program_issue_id: "mem89-issue", issue_id: currentIssue.id, corpus_id: "platform",
      projection_sha: "d".repeat(40), canonical_sha: "e".repeat(40), acceptance_receipt_digest: "f".repeat(64),
      manifest_digest: "1".repeat(64), result: { matches: [], truncated: false, next_cursor: null },
    }));
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("search_accepted_source")!({
      corpus_id: "platform", query: "source", prefix: "engineering/", cursor: null, limit: 10,
    }, readerRun) as { content: string };
    expect(JSON.parse(result.content).issue_id).toBe(currentIssue.id);
    expect(invoke).toHaveBeenCalledWith("search_accepted_source", expect.objectContaining({
      program_issue_id: "mem89-issue", issue_id: currentIssue.id, corpus_id: "platform", run_id: workerRun.runId,
    }), configPath, "/usr/bin/python3");
    await expect(handlers.get("search_accepted_source")!({
      corpus_id: "platform", query: "source", prefix: "engineering/", cursor: null, limit: 10,
    }, { ...readerRun, agentId: "unauthorized-agent" })).rejects.toThrow("accepted_source_reader_program_not_admitted");
    currentIssue.parentId = "other-program";
    await expect(handlers.get("search_accepted_source")!({
      corpus_id: "platform", query: "source", prefix: "engineering/", cursor: null, limit: 10,
    }, readerRun)).rejects.toThrow("accepted_source_reader_program_ancestry_mismatch");
    currentIssue.parentId = "mem89-issue";
    currentIssue.executionRunId = "stale-run";
    await expect(handlers.get("search_accepted_source")!({
      corpus_id: "platform", query: "source", prefix: "engineering/", cursor: null, limit: 10,
    }, readerRun)).rejects.toThrow("accepted_source_reader_run_binding_unknown");
    expect(invoke).toHaveBeenCalledTimes(1);
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
        goal_id: "goal-1",
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
    let sourceGoalId = "foreign-goal";
    let leadGoalId = "foreign-goal";
    const issue = (id: string) => ({
      id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
      goalId: id === "lead-issue" ? leadGoalId : id === "source-8" ? sourceGoalId : "goal-1",
      description: id === "source-8" ? "source instructions" : "review instructions",
      projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
      assigneeAgentId: id === "source-8" ? "worker-1" : id === "review-8" ? "reviewer-1" : "lead-1",
      status: id === "lead-issue" ? "in_progress" : id === "source-8" ? "backlog" : "blocked",
      statusVersion: id === "source-8" ? 4 : 7,
      executionRunId: id === "lead-issue" ? "lead-run" : null,
    });
    const issueNotes = new Map<string, string[]>();
    const commentCalls: string[] = [];
    let failCommentIssue: string | null = null;
    const updateStatusCompareAndSet = vi.fn(async () => {
      commentCalls.push("cas:source-8");
      throw new Error("native_ack_unknown");
    });
    let workspaceProviderType = "local_fs";
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: workspaceProviderType, providerMetadata: null,
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
        listComments: vi.fn(async (id: string) => {
          commentCalls.push(`list:${id}`);
          return (issueNotes.get(id) ?? []).map((body) => ({ body })) as never;
        }),
        createComment: vi.fn(async (id: string, body: string) => {
          commentCalls.push(`create:${id}`);
          if (failCommentIssue === id) throw new Error("comment_create_failed");
          issueNotes.set(id, [...(issueNotes.get(id) ?? []), body]);
          return {} as never;
        }),
        updateStatusCompareAndSet,
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    let intentCalls = 0;
    const invoke = vi.fn(async (action: string) => {
      if (action === "lookup_source_stage_binding") return { found: false };
      if (action === "bind_source_stage") return {
        stage_binding_key: "a".repeat(64), base_sha: "b".repeat(40), state: "bound",
        accepted_base_context_note: acceptedBaseContextNote("MEM8", [
          "src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py",
        ]),
      };
      intentCalls += 1;
      return { intent: intentCalls === 1 ? "created" : "reconcile_required" };
    });
    registerAutonomousTools(ctx, invoke as never);
    const params = { stage_key: "MEM8", source_issue_id: "source-8", review_issue_id: "review-8" };
    await expect(handlers.get("bind_source_stage")!({
      ...params,
      path_ids: ["src/forged.py"],
      verifier_policy_id: "worker-selected-policy",
      source_workspace_root: "/tmp/worker-selected-root",
    }, leadRun)).rejects.toThrow("source_stage_parameters_invalid");
    expect(invoke).not.toHaveBeenCalled();
    await expect(handlers.get("bind_source_stage")!(params, leadRun))
      .rejects.toThrow("host_issue_run_binding_mismatch");
    expect(invoke.mock.calls.filter(([action]) => action === "bind_source_stage")).toHaveLength(0);
    leadGoalId = "goal-1";
    await expect(handlers.get("bind_source_stage")!(params, leadRun))
      .rejects.toThrow("source_stage_source_admission_mismatch");
    expect(invoke.mock.calls.filter(([action]) => action === "bind_source_stage")).toHaveLength(0);
    sourceGoalId = "goal-1";
    for (const providerType of ["local", "git_worktree", "remote_managed"]) {
      workspaceProviderType = providerType;
      await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("source_workspace_metadata_mismatch");
      expect(updateStatusCompareAndSet).not.toHaveBeenCalled();
      expect(invoke.mock.calls.filter(([action]) => action !== "lookup_source_stage_binding")).toHaveLength(0);
    }
    workspaceProviderType = "local_fs";
    await expect(handlers.get("bind_source_stage")!({
      ...params, attempt: 3, retry_of_operation_key: "f".repeat(64),
    }, leadRun)).rejects.toThrow("source_stage_retry_not_admitted");
    await expect(handlers.get("bind_source_stage")!({
      ...params, attempt: 4, retry_of_operation_key: "f".repeat(64),
    }, leadRun)).rejects.toThrow("source_stage_attempt_invalid");
    expect(invoke).toHaveBeenCalledWith("lookup_source_stage_binding", expect.objectContaining({
      stage_key: "MEM8", attempt: 2,
    }), configPath, "/usr/bin/python3");
    expect(updateStatusCompareAndSet).not.toHaveBeenCalled();
    failCommentIssue = "review-8";
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("comment_create_failed");
    expect(updateStatusCompareAndSet).not.toHaveBeenCalled();
    expect(issueNotes.get("source-8")).toHaveLength(1);
    expect(issueNotes.get("review-8")).toBeUndefined();
    expect(invoke.mock.calls.filter(([action]) => action === "source_stage_activation")).toHaveLength(0);
    failCommentIssue = null;
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("native_ack_unknown");
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("source_stage_activation_reconciliation_required");
    expect(updateStatusCompareAndSet).toHaveBeenCalledTimes(1);
    expect(updateStatusCompareAndSet).toHaveBeenCalledWith("source-8", "todo", 4, "company-1");
    expect(invoke.mock.calls.filter(([action]) => action === "source_stage_activation")).toHaveLength(2);
    for (const id of ["source-8", "review-8", "lead-issue"]) {
      expect(issueNotes.get(id)).toHaveLength(3);
      expect(issueNotes.get(id)?.[0]).toContain(`brain-autonomous-bridge:${"a".repeat(64)}:bound`);
      expect(commentCalls.indexOf(`create:${id}`)).toBeLessThan(commentCalls.indexOf("cas:source-8"));
    }
    const contextNote = issueNotes.get("source-8")?.[1] ?? "";
    expect(contextNote).toContain(`brain-autonomous-bridge:${"a".repeat(64)}:scope-context`);
    expect(contextNote).toContain("stage_key:MEM8");
    expect(contextNote).toContain(`stage_binding_key:${"a".repeat(64)}`);
    expect(contextNote).toContain(`base_sha:${"b".repeat(40)}`);
    expect(contextNote).toContain("stage_workspace_id:source");
    expect(contextNote).toContain("source_workspace_id:workspace-project");
    expect(contextNote).toContain("source_workspace_root:/tmp/brain-source-workspace");
    expect(contextNote).toContain("selected_cwd:/tmp/brain-source-workspace");
    expect(contextNote).toContain("verifier_policy_id:policy-mem8");
    expect(contextNote).toContain("path_id:src/memecoin_brain_sources/events.py");
    expect(contextNote).toContain("path_id:tests/brain/test_raw_sources.py");
    expect(contextNote).toContain("source_issue_id:source-8");
    expect(contextNote).toContain("review_issue_id:review-8");
    expect(contextNote).toContain(`source_instruction_sha256:${digest("source instructions")}`);
    expect(contextNote).toContain(`review_instruction_sha256:${digest("review instructions")}`);
    expect(contextNote).toMatch(/scope_context_sha256:[0-9a-f]{64}/);
    expect(contextNote).not.toContain("activation_ack");
    expect(contextNote).not.toContain("activation_status:active");
    expect(invoke.mock.calls.filter(([action]) => action === "source_stage_activation")).toHaveLength(2);
  });

  it("source content delivery touches its own task and direct lead only", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project", source_workspace_root: "/tmp/brain-source-workspace", source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          goal_id: "goal-1",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ], python_executable: "/usr/bin/python3",
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const sourceRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "worker-1", runId: "source-run" };
    const issueReads: string[] = [];
    const boundIssueReads: string[] = [];
    const commentTargets: string[] = [];
    const boundCommentTargets: string[] = [];
    const issueNotes = new Map<string, string[]>();
    const scopedOrchestration = vi.fn();
    let sourceDescription = "source instructions";
    let boundReviewGoal: string | null = "goal-1";
    let boundReviewParent = "lead-issue";
    let boundReviewAssignee = "reviewer-1";
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs", providerMetadata: null,
      })) },
      issues: {
        get: vi.fn(async (id: string) => {
          boundIssueReads.push(id);
          if (id !== "review-8") throw new Error("untrusted_unscoped_issue_access");
          return { id, companyId: "company-1", projectId: "project-1", parentId: boundReviewParent,
            goalId: boundReviewGoal, assigneeAgentId: boundReviewAssignee, status: "blocked" } as never;
        }),
        listComments: vi.fn(async (id: string) => {
          boundCommentTargets.push(`list:${id}`);
          if (id !== "review-8") throw new Error("untrusted_unscoped_issue_access");
          return (issueNotes.get(id) ?? []).map((body) => ({ body })) as never;
        }),
        createComment: vi.fn(async (id: string, body: string) => {
          boundCommentTargets.push(`create:${id}`);
          if (id !== "review-8") throw new Error("untrusted_unscoped_issue_access");
          issueNotes.set(id, [...(issueNotes.get(id) ?? []), body]);
          return {} as never;
        }),
        forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => {
          issueReads.push(id);
          if (id !== "source-8" && id !== "lead-issue") throw new Error("sibling_issue_access_denied");
          if (id === "lead-issue") return { id, companyId: "company-1", projectId: "project-1",
            goalId: "goal-1",
            projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
            assigneeAgentId: "lead-1", status: "in_progress", executionRunId: null } as never;
          return { id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
            goalId: "goal-1",
            projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution",
            assigneeAgentId: "worker-1", status: "in_progress", executionRunId: "source-run",
            description: sourceDescription } as never;
        }),
        getOrchestration: scopedOrchestration.mockImplementation(async ({ issueId }: { issueId: string }) => ({
          runs: [{ id: "source-run", issueId, agentId: "worker-1", status: "running" }], blockedByIssueIds: [],
        })) as never,
        listComments: vi.fn(async (id: string) => {
          commentTargets.push(`list:${id}`);
          if (id !== "lead-issue" && id !== "source-8") throw new Error("sibling_comment_access_denied");
          return (issueNotes.get(id) ?? []).map((body) => ({ body })) as never;
        }),
        createComment: vi.fn(async (id: string, body: string) => {
          commentTargets.push(`create:${id}`);
          if (id !== "lead-issue" && id !== "source-8") throw new Error("sibling_comment_access_denied");
          issueNotes.set(id, [...(issueNotes.get(id) ?? []), body]);
          return {} as never;
        }),
        }),
      },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    let deliveryCalls = 0;
    const preflightFailure = {
      status: "preflight_failed", content_check_id: "9".repeat(64), snapshot_sha256: "a".repeat(64),
      policy_digest: "b".repeat(64), failure_code: "sandboxed_verifier_failed", command_index: 0,
      returncode: 1, timed_out: false, output_truncated: false, stdout: "assertion failed", stderr: "",
      stdout_sha256: "c".repeat(64), stderr_sha256: "d".repeat(64), sandbox_profile_sha256: "e".repeat(64),
    };
    const correctedFiles = contentFiles.map((file) => ({
      ...file, content: `${file.content}# corrected\n`,
      size: Buffer.byteLength(`${file.content}# corrected\n`),
      sha256: digest(`${file.content}# corrected\n`),
    }));
    const invoke = vi.fn(async (action: string) => {
      if (action === "lookup_source_stage_binding") {
        return { found: true, stage_binding_key: "a".repeat(64), source_issue_id: "source-8", review_issue_id: "review-8",
          source_instruction_sha256: digest("source instructions"), review_instruction_sha256: digest("review instructions") };
      }
      deliveryCalls += 1;
      if (deliveryCalls === 1) return preflightFailure;
      return { operation_key: "c".repeat(64), candidate_sha: "d".repeat(40), base_sha: "b".repeat(40), submitted_sha: "e".repeat(40), policy_digest: "f".repeat(64), preflight_check_id: "8".repeat(64) };
    });
    registerAutonomousTools(ctx, invoke as never);
    const failed = await handlers.get("deliver_source_stage_content")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), source_issue_id: "source-8", files: contentFiles,
    }, sourceRun) as { content: string };
    expect(JSON.parse(failed.content)).toMatchObject({ status: "preflight_failed", content_check_id: "9".repeat(64) });
    expect(issueNotes.get("source-8")).toBeUndefined();
    expect(issueNotes.get("lead-issue")).toBeUndefined();
    expect(issueNotes.get("review-8")).toBeUndefined();
    for (const [field, value] of [["goal", "foreign-goal"], ["parent", "foreign-parent"], ["assignee", "foreign-agent"]] as const) {
      if (field === "goal") boundReviewGoal = value;
      if (field === "parent") boundReviewParent = value;
      if (field === "assignee") boundReviewAssignee = value;
      await expect(handlers.get("deliver_source_stage_content")!({
        stage_key: "MEM8", stage_binding_key: "a".repeat(64), source_issue_id: "source-8", files: contentFiles,
      }, sourceRun)).rejects.toThrow("source_stage_review_binding_mismatch");
    }
    boundReviewGoal = "goal-1";
    boundReviewParent = "lead-issue";
    boundReviewAssignee = "reviewer-1";
    const delivered = await handlers.get("deliver_source_stage_content")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), source_issue_id: "source-8", files: correctedFiles,
    }, sourceRun) as { content: string };
    expect(delivered.content).toContain(`operation_key:${"c".repeat(64)}`);
    sourceDescription = "changed source instructions";
    await expect(handlers.get("deliver_source_stage_content")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), source_issue_id: "source-8", files: contentFiles,
    }, sourceRun)).rejects.toThrow("source_stage_instruction_binding_mismatch");
    expect(issueReads.filter((id) => id === "source-8")).toHaveLength(14);
    expect(issueReads.filter((id) => id === "lead-issue")).toHaveLength(2);
    expect(commentTargets).toEqual([
      "list:source-8", "create:source-8", "list:lead-issue", "create:lead-issue",
    ]);
    expect(boundIssueReads).toEqual(["review-8", "review-8", "review-8", "review-8", "review-8", "review-8"]);
    expect(boundCommentTargets).toEqual(["list:review-8", "create:review-8"]);
    expect(issueNotes.get("source-8")?.[0]).toContain(`brain-autonomous-bridge:${"c".repeat(64)}:submitted`);
    expect(issueNotes.get("source-8")?.[0]).toContain(`stage_binding_key:${"a".repeat(64)}`);
    expect(issueNotes.get("review-8")?.[0]).toBe(issueNotes.get("source-8")?.[0]);
    expect(issueNotes.get("lead-issue")?.[0]).toBe(issueNotes.get("source-8")?.[0]);
    expect(scopedOrchestration).not.toHaveBeenCalled();
    expect(invoke).toHaveBeenCalledWith("deliver_source_stage_content", {
      company_id: "company-1", project_id: "project-1", worker_agent_id: "worker-1",
      run_id: "source-run", source_issue_id: "source-8", stage_key: "MEM8",
      stage_binding_key: "a".repeat(64), source_instruction_sha256: digest("source instructions"), files: correctedFiles,
    }, configPath, "/usr/bin/python3");
    expect(invoke.mock.calls.filter(([action]) => action === "deliver_source_stage_content")).toHaveLength(2);
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
      ], expected_dev_ref: "refs/heads/brain/development",
      canonical_repo: "/tmp/brain-canonical", canonical_ref: "refs/heads/brain/development",
      canonical_expected_tip: "f".repeat(40), python_executable: "/usr/bin/python3",
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const issueReads: string[] = [];
    const scopedOrchestration = vi.fn();
    let reviewDescription = "review instructions";
    let reviewerStatus = "in_progress";
    const issueNotes = new Map<string, string[]>();
    const commentTargets: string[] = [];
    const getDependencyReadiness = vi.fn(async () => ({
      isDependencyReady: true, unresolvedBlockerIssueIds: [], pendingFinalizeBlockerIssueIds: [],
    }));
    const reviewerTaskRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "reviewer-1", runId: "reviewer-run" };
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs", providerMetadata: null,
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
            assigneeAgentId: "reviewer-1", status: reviewerStatus, executionRunId: "reviewer-run",
            description: reviewDescription } as never;
        }),
        getOrchestration: scopedOrchestration.mockImplementation(async ({ issueId }: { issueId: string }) => ({
          runs: [{ id: "reviewer-run", issueId, agentId: "reviewer-1", status: "running" }], blockedByIssueIds: ["source-8"],
        })) as never,
        getDependencyReadiness: getDependencyReadiness as never,
        listComments: vi.fn(async (id: string) => {
          commentTargets.push(`list:${id}`);
          if (id !== "review-8" && id !== "lead-issue") throw new Error("sibling_comment_access_denied");
          return (issueNotes.get(id) ?? []).map((body) => ({ body })) as never;
        }),
        createComment: vi.fn(async (id: string, body: string) => {
          commentTargets.push(`create:${id}`);
          if (id !== "review-8" && id !== "lead-issue") throw new Error("sibling_comment_access_denied");
          issueNotes.set(id, [...(issueNotes.get(id) ?? []), body]);
          return {} as never;
        }),
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async (action: string) => action === "lookup_source_stage_binding"
      ? { found: true, stage_binding_key: "a".repeat(64), source_instruction_sha256: digest("source instructions"), review_instruction_sha256: digest("review instructions") }
      : action === "approve_bound_source_stage"
        ? { operation_key: "c".repeat(64), candidate_sha: "d".repeat(40), status: "published", evidence_digest: "f".repeat(64),
          projection_status: "published", canonical_status: "published", canonical_candidate_sha: "7".repeat(40), canonical_receipt_digest: "8".repeat(64) }
        : { operation_key: "c".repeat(64), candidate_sha: "d".repeat(40), inspection_digest: "e".repeat(64), diff: "exact diff",
          target_ref: "refs/heads/brain/development", canonical_ref: "refs/heads/brain/development",
          source_preflight: { status: "passed", content_check_id: "1".repeat(64), snapshot_sha256: "2".repeat(64),
            policy_digest: "3".repeat(64), receipt_sha256: "4".repeat(64), commands_sha256: "5".repeat(64) },
          final_verifier_phase: "pending_after_independent_QA",
          canonical_candidate_sha: "7".repeat(40), canonical_receipt_digest: "8".repeat(64), canonical_diff: "canonical diff" });
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("inspect_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
    }, reviewerTaskRun) as { content: string };
    expect(result.content).toContain('"status": "passed"');
    expect(result.content).toContain('"final_verifier_phase": "pending_after_independent_QA"');
    reviewerStatus = "blocked";
    await expect(handlers.get("inspect_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
    }, reviewerTaskRun)).rejects.toThrow("host_issue_run_binding_mismatch");
    reviewerStatus = "in_review";
    await expect(handlers.get("approve_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
      approved: true, evidence: { inspection_digest: "f".repeat(64), canonical_candidate_sha: "7".repeat(40), canonical_receipt_digest: "8".repeat(64), rationale: "approved" },
    }, reviewerTaskRun)).rejects.toThrow("host_issue_run_binding_mismatch");
    reviewerStatus = "in_progress";
    reviewDescription = "changed review instructions";
    await expect(handlers.get("inspect_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
    }, reviewerTaskRun)).rejects.toThrow("source_stage_instruction_binding_mismatch");
    await expect(handlers.get("approve_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
      approved: true, evidence: { inspection_digest: "f".repeat(64), canonical_candidate_sha: "7".repeat(40), canonical_receipt_digest: "8".repeat(64), rationale: "approved" },
    }, reviewerTaskRun)).rejects.toThrow("source_stage_instruction_binding_mismatch");
    reviewDescription = "review instructions";
    expect(issueReads).not.toContain("source-8");
    expect(scopedOrchestration).not.toHaveBeenCalled();
    expect(getDependencyReadiness).not.toHaveBeenCalled();
    expect(invoke).toHaveBeenCalledWith("lookup_source_stage_binding", {
      company_id: "company-1", project_id: "project-1", lead_agent_id: "lead-1", lead_issue_id: "lead-issue",
      stage_key: "MEM8", review_issue_id: "review-8", stage_binding_key: "a".repeat(64),
    }, configPath, "/usr/bin/python3");
    expect(invoke).toHaveBeenCalledWith("inspect_bound_source_stage", expect.objectContaining({
      reviewer_issue_id: "review-8", stage_binding_key: "a".repeat(64), stage_key: "MEM8",
      review_instruction_sha256: digest("review instructions"),
    }), configPath, "/usr/bin/python3");
    expect(result.content).toContain("exact diff");
    expect(invoke.mock.calls.filter(([action]) => action === "inspect_bound_source_stage")).toHaveLength(1);
    expect(invoke.mock.calls.filter(([action]) => action === "approve_bound_source_stage")).toHaveLength(0);
    const approval = await handlers.get("approve_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8",
      approved: true, evidence: { inspection_digest: "e".repeat(64), canonical_candidate_sha: "7".repeat(40), canonical_receipt_digest: "8".repeat(64), rationale: "approved" },
    }, reviewerTaskRun) as { content: string };
    expect(approval.content).toContain("status:published");
    expect(issueNotes.get("review-8")?.[0]).toContain(`brain-autonomous-bridge:${"c".repeat(64)}:stage-reviewed`);
    expect(issueNotes.get("review-8")?.[0]).toContain(`evidence_digest:${"f".repeat(64)}`);
    expect(issueNotes.get("review-8")?.[0]).toContain("projection_status:published");
    expect(issueNotes.get("review-8")?.[0]).toContain("canonical_status:published");
    expect(issueNotes.get("review-8")?.[0]).toContain(`canonical_candidate_sha:${"7".repeat(40)}`);
    expect(issueNotes.get("review-8")?.[0]).toContain(`canonical_receipt_digest:${"8".repeat(64)}`);
    expect(commentTargets).toEqual(["list:review-8", "create:review-8", "list:lead-issue", "create:lead-issue"]);
    expect(issueReads).not.toContain("source-8");
  });

  it("selects the exact same-project lead lane and holds an incomplete predecessor summary", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project",
      source_workspace_root: "/tmp/brain-source-workspace", python_executable: "/usr/bin/python3",
      source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          project_id: "project-1", goal_id: null, lead_agent_id: "lead-1", lead_issue_id: "lead-issue",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          project_id: "project-1", goal_id: null, lead_agent_id: "lead-2", lead_issue_id: "lead-issue-2",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ],
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const leadRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "lead-2", runId: "lead-run" };
    const issueReads: string[] = [];
    const commentCalls: string[] = [];
    const controllerIssueReads: string[] = [];
    const controllerSummaryReads: string[] = [];
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      issues: {
        get: vi.fn(async (id: string) => { controllerIssueReads.push(id); return id === "lead-issue-2" ? {
          id, companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-2",
          goalId: null, status: "in_progress", executionRunId: "lead-run",
        } : id === "lead-issue" ? {
          id, companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-1",
          goalId: null, status: "done", executionRunId: null,
        } : null; }) as never,
        summaries: { getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => { controllerSummaryReads.push(issueId); return ({
          issueId, companyId: "company-1", subtreeIssueIds: [issueId],
          relations: { [issueId]: { blockedBy: issueId === "lead-issue-2"
            ? [{ id: "lead-issue", identifier: null, title: "predecessor", status: "done", priority: "high", assigneeAgentId: "lead-1", assigneeUserId: null }]
            : [], blocks: [] } }, approvals: [], runs: issueId === "lead-issue-2"
            ? [{ id: "lead-run", issueId, agentId: "lead-2", status: "running", invocationSource: "assignment",
                triggerDetail: null, startedAt: null, finishedAt: null, error: null, createdAt: "2026-01-01T00:00:00Z" }]
            : [],
          dependencyReadinessComplete: issueId !== "lead-issue",
          dependencyReadiness: issueId === "lead-issue" ? null : { issueId, blockerIssueIds: ["lead-issue"],
            unresolvedBlockerIssueIds: [], unresolvedBlockerCount: 0,
            pendingFinalizeBlockerIssueIds: [], allBlockersDone: true, isDependencyReady: true },
          costs: { costCents: 0, inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, billingCode: null },
          openBudgetIncidents: [], invocationBlocks: [],
        }); }) as never },
        forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => {
          issueReads.push(id);
          if (id === "lead-issue-2") return { id, companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-2",
            goalId: null, status: "in_progress", executionRunId: "lead-run", projectWorkspaceId: "workspace-project",
            executionWorkspaceId: "exec-workspace" } as never;
          if (id === "source-9") return { id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue-2",
            goalId: null,
            assigneeAgentId: "worker-1", status: "backlog", statusVersion: 1, executionRunId: null,
            description: "source", projectWorkspaceId: "workspace-project", executionWorkspaceId: "exec-workspace" } as never;
          if (id === "review-9") return { id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue-2",
            goalId: null,
            assigneeAgentId: "reviewer-1", status: "blocked", statusVersion: 1, executionRunId: null,
            description: "review", projectWorkspaceId: "workspace-project", executionWorkspaceId: "exec-workspace" } as never;
          throw new Error("unexpected_child_read");
        }),
        getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
          issueId, runs: [{ id: "lead-run", issueId, agentId: "lead-2", status: "running" }],
          blockedByIssueIds: issueId === "review-9" ? ["source-9"] : ["lead-issue"],
        })) as never,
        getDependencyReadiness: vi.fn(async (issueId: string) => issueId === "review-9"
          ? ({ issueId, isDependencyReady: false, blockerIssueIds: ["source-9"],
            unresolvedBlockerIssueIds: ["source-9"], unresolvedBlockerCount: 1, pendingFinalizeBlockerIssueIds: [], allBlockersDone: false })
          : ({ issueId, isDependencyReady: true, blockerIssueIds: ["lead-issue"],
            unresolvedBlockerIssueIds: [], unresolvedBlockerCount: 0, pendingFinalizeBlockerIssueIds: [], allBlockersDone: true })) as never,
        listComments: vi.fn(async (id: string) => { commentCalls.push(`list:${id}`); return [] as never; }),
        createComment: vi.fn(async (id: string) => { commentCalls.push(`create:${id}`); return {} as never; }),
      }) },
      executionWorkspaces: { get: vi.fn(async () => ({ projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace", repoUrl: null,
        baseRef: null, branchName: null, providerType: "local_fs" })) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async (_action: string, payload: Record<string, unknown>) => payload.stage_key === "MEM8" ? ({
      found: true, attempt: 2, stage_binding_key: "a".repeat(64), base_sha: "c".repeat(40), state: "active",
      operation_key: "d".repeat(64), source_issue_id: "changed-source", review_issue_id: "review-8",
      candidate_sha: "b".repeat(40), source_receipt_digest: "c".repeat(64),
      canonical_candidate_sha: "d".repeat(40), canonical_receipt_digest: "e".repeat(64),
      canonical_approved_receipt_digest: "e".repeat(64),
      canonical_receipt_ref: "refs/heads/org/development",
      operation_state: "published", operation_review_approved: true, canonical_status: "published",
      source_instruction_sha256: "e".repeat(64),
      review_instruction_sha256: "f".repeat(64), activation_pending: false,
    }) : ({ found: false, attempt: 1 }));
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("bind_source_stage")!({
      stage_key: "MEM9", source_issue_id: "source-9", review_issue_id: "review-9", attempt: 1,
      predecessor: { operation_key: "d".repeat(64), source_issue_id: "changed-source", review_issue_id: "review-8" },
    }, leadRun)).rejects.toThrow("source_stage_lead_summary_identity_mismatch");
    expect(issueReads).not.toContain("lead-issue");
    expect(controllerIssueReads).toEqual(["lead-issue-2", "lead-issue"]);
    expect(controllerSummaryReads).toEqual(controllerIssueReads);
    expect(invoke).toHaveBeenCalledTimes(2);
    expect(invoke).toHaveBeenCalledWith("lookup_source_stage_binding", {
      company_id: "company-1", project_id: "project-1", lead_agent_id: "lead-2", lead_issue_id: "lead-issue-2",
      stage_key: "MEM9", source_issue_id: "source-9", review_issue_id: "review-9", attempt: 1,
    }, configPath, "/usr/bin/python3");
    expect(commentCalls).toEqual([]);
  });

  it("persists a canonical QA rejection receipt from the real controller result shape", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project",
      source_workspace_root: "/tmp/brain-source-workspace", canonical_repo: "/tmp/brain-canonical",
      canonical_ref: "refs/heads/brain/development", canonical_expected_tip: "f".repeat(40),
      source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ], python_executable: "/usr/bin/python3",
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const reviewerRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "reviewer-1", runId: "reviewer-run" };
    const comments = new Map<string, string[]>();
    const issue = (id: string) => id === "review-8"
      ? { id, companyId: "company-1", projectId: "project-1", parentId: "lead-issue", assigneeAgentId: "reviewer-1",
        status: "in_progress", executionRunId: "reviewer-run", description: "review instructions",
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution" }
      : { id, companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-1",
        status: "in_progress", executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution" };
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs", providerMetadata: null,
      })) },
      issues: { forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => issue(id) as never),
        listComments: vi.fn(async (id: string) => (comments.get(id) ?? []).map((body) => ({ body })) as never),
        createComment: vi.fn(async (id: string, body: string) => {
          comments.set(id, [...(comments.get(id) ?? []), body]);
          return {} as never;
        }),
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async (action: string) => action === "lookup_source_stage_binding"
      ? { found: true, stage_binding_key: "a".repeat(64), source_instruction_sha256: digest("source instructions"), review_instruction_sha256: digest("review instructions") }
      : { status: "review_rejected", candidate_sha: "d".repeat(40), operation_key: "c".repeat(64), evidence_digest: "f".repeat(64) });
    registerAutonomousTools(ctx, invoke as never);
    const result = await handlers.get("approve_source_stage_candidate")!({
      stage_key: "MEM8", stage_binding_key: "a".repeat(64), review_issue_id: "review-8", approved: false,
      evidence: { inspection_digest: "e".repeat(64), canonical_candidate_sha: "7".repeat(40), canonical_receipt_digest: "8".repeat(64), rationale: "incorrect boundary expectation" },
    }, reviewerRun) as { content: string };
    expect(result.content).toContain("status:review_rejected");
    const note = comments.get("review-8")?.[0] ?? "";
    expect(note).toContain(`brain-autonomous-bridge:${"c".repeat(64)}:stage-reviewed`);
    expect(note).toContain("status:review_rejected");
    expect(note).toContain(`canonical_candidate_sha:${"7".repeat(40)}`);
    expect(note).toContain(`canonical_receipt_digest:${"8".repeat(64)}`);
    expect(note).toContain("canonical_publication:not_attempted_review_rejected");
    expect(note).not.toContain("canonical_status:");
    expect(comments.get("lead-issue")?.[0]).toBe(note);
  });

  it("backfills the bound note to source and QA history on active replay without activating again", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project",
      source_workspace_root: "/tmp/brain-source-workspace", python_executable: "/usr/bin/python3",
      source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ],
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const leadRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "lead-1", runId: "lead-run" };
    const issues: Record<string, Record<string, unknown>> = {
      "lead-issue": { id: "lead-issue", companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-1",
        status: "in_progress", executionRunId: "lead-run" },
      "source-8": { id: "source-8", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "worker-1", description: "source instructions" },
      "review-8": { id: "review-8", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "reviewer-1", description: "review instructions" },
    };
    const issueNotes = new Map<string, string[]>();
    let currentHistoryIssue = "lead-issue";
    const currentIssues = () => ({
      get: vi.fn(async (id: string) => {
        if (currentHistoryIssue !== "lead-issue" && id !== currentHistoryIssue) throw new Error("sibling_issue_access_denied");
        return issues[id] as never;
      }),
      getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
        issueId, runs: [{ id: "lead-run", issueId, agentId: "lead-1", status: "running" }], blockedByIssueIds: [],
      })) as never,
      listComments: vi.fn(async (id: string) => {
        if (currentHistoryIssue !== "lead-issue" && id !== currentHistoryIssue) throw new Error("sibling_comment_access_denied");
        return (issueNotes.get(id) ?? []).map((body) => ({ body })) as never;
      }),
      createComment: vi.fn(async (id: string, body: string) => {
        if (currentHistoryIssue !== "lead-issue" && id !== currentHistoryIssue) throw new Error("sibling_comment_access_denied");
        issueNotes.set(id, [...(issueNotes.get(id) ?? []), body]);
        return {} as never;
      }),
    });
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      issues: { forCurrentToolRun: currentIssues },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const sourceHash = digest("source instructions");
    const reviewHash = digest("review instructions");
    const invoke = vi.fn(async () => ({
      found: true, attempt: 1, stage_binding_key: "a".repeat(64), base_sha: "b".repeat(40), state: "active",
      predecessor_operation_key: null, predecessor_source_issue_id: null, predecessor_review_issue_id: null,
      source_instruction_sha256: sourceHash, review_instruction_sha256: reviewHash, activation_pending: false,
      accepted_base_context_note: acceptedBaseContextNote("MEM8", [
        "src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py",
      ]),
    }));
    registerAutonomousTools(ctx, invoke as never);
    const params = { stage_key: "MEM8", source_issue_id: "source-8", review_issue_id: "review-8" };
    await handlers.get("bind_source_stage")!(params, leadRun);
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(issueNotes.get("source-8")).toHaveLength(3);
    expect(issueNotes.get("review-8")).toHaveLength(3);
    const note = issueNotes.get("source-8")?.[0] ?? "";
    const scopeNote = issueNotes.get("source-8")?.[1] ?? "";
    expect(note).toBe([
      `brain-autonomous-bridge:${"a".repeat(64)}:bound`,
      `brain-source-stage:${"a".repeat(64)}:bound`,
      "stage_key:MEM8",
      `stage_binding_key:${"a".repeat(64)}`,
      `base_sha:${"b".repeat(40)}`,
      "source_issue_id:source-8",
      "review_issue_id:review-8",
      `source_instruction_sha256:${sourceHash}`,
      `review_instruction_sha256:${reviewHash}`,
    ].join("\n"));
    const acceptedBaseNote = issueNotes.get("source-8")?.[2] ?? "";
    expect(acceptedBaseNote).toContain(`brain-autonomous-bridge:${"a".repeat(64)}:accepted-base-context`);
    expect(acceptedBaseNote).toContain("comparison_scope:bounded_base_content_not_full_repository_equivalence");
    expect(acceptedBaseNote).toContain("rejected_retry_seed:excluded");
    expect(acceptedBaseNote).toContain("accepted MEM8 base 0 — café");
    expect(acceptedBaseNote).not.toContain("activation_ack");

    currentHistoryIssue = "source-8";
    const sourceHistory = await currentIssues().listComments("source-8", "company-1");
    expect(sourceHistory.map((entry) => entry.body)).toEqual([note, scopeNote, acceptedBaseNote]);
    await expect(currentIssues().listComments("review-8", "company-1")).rejects.toThrow("sibling_comment_access_denied");
    currentHistoryIssue = "review-8";
    const reviewHistory = await currentIssues().listComments("review-8", "company-1");
    expect(reviewHistory.map((entry) => entry.body)).toEqual([note, scopeNote, acceptedBaseNote]);
    await expect(currentIssues().listComments("source-8", "company-1")).rejects.toThrow("sibling_comment_access_denied");

    currentHistoryIssue = "lead-issue";
    await handlers.get("bind_source_stage")!(params, leadRun);
    expect(issueNotes.get("source-8")).toHaveLength(3);
    expect(issueNotes.get("review-8")).toHaveLength(3);
    expect(issueNotes.get("lead-issue")).toHaveLength(3);
    expect(invoke).toHaveBeenCalledTimes(2);
    expect(invoke.mock.calls.every(([action]) => action === "lookup_source_stage_binding")).toBe(true);
    expect(invoke.mock.calls.some(([action]) => action === "source_stage_activation")).toBe(false);
    issueNotes.set("review-8", [`${note}\nconflicting replay content`]);
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("paperclip_evidence_replay_mismatch");
    expect(issueNotes.get("source-8")).toHaveLength(3);
    expect(issueNotes.get("review-8")).toHaveLength(1);
    expect(invoke).toHaveBeenCalledTimes(3);
    issueNotes.set("review-8", [note, scopeNote, `${acceptedBaseNote}\nconflicting accepted base`]);
    await expect(handlers.get("bind_source_stage")!(params, leadRun)).rejects.toThrow("paperclip_evidence_replay_mismatch");
    expect(issueNotes.get("source-8")).toHaveLength(3);
    expect(issueNotes.get("review-8")).toHaveLength(3);
    expect(invoke).toHaveBeenCalledTimes(4);
  });

  it("holds stage two when the native lead still has a pending QA finalization blocker", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project",
      source_workspace_root: "/tmp/brain-source-workspace", python_executable: "/usr/bin/python3",
      source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ],
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const leadRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "lead-1", runId: "lead-run" };
    const rows: Record<string, Record<string, unknown>> = {
      "lead-issue": { id: "lead-issue", companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-1",
        status: "in_progress", statusVersion: 10, executionRunId: "lead-run", projectWorkspaceId: "workspace-project",
        executionWorkspaceId: "workspace-execution", description: "lead" },
      "source-8": { id: "source-8", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "worker-1", status: "done", statusVersion: 4, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "prior source" },
      "review-8": { id: "review-8", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "reviewer-1", status: "done", statusVersion: 5, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "prior QA" },
      "source-9": { id: "source-9", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "worker-1", status: "backlog", statusVersion: 1, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "next source" },
      "review-9": { id: "review-9", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "reviewer-1", status: "blocked", statusVersion: 2, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "next QA" },
    };
    const updateStatusCompareAndSet = vi.fn();
    const getDependencyReadiness = vi.fn(async (id: string) => id === "review-9"
      ? { isDependencyReady: false, unresolvedBlockerIssueIds: ["source-9"], pendingFinalizeBlockerIssueIds: [], blockerIssueIds: ["source-9"] }
      : id === "review-8"
        ? { isDependencyReady: true, unresolvedBlockerIssueIds: [], pendingFinalizeBlockerIssueIds: [], blockerIssueIds: ["source-8"] }
        : { isDependencyReady: true, unresolvedBlockerIssueIds: [], pendingFinalizeBlockerIssueIds: ["review-8"], blockerIssueIds: ["review-8"] });
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs", providerMetadata: null,
      })) },
      issues: { forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => rows[id] as never),
        getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
          blockedByIssueIds: issueId === "lead-issue" ? ["review-8"] : issueId === "review-9" ? ["source-9"] : ["source-8"],
          runs: issueId === "lead-issue" ? [{ id: "lead-run", issueId, agentId: "lead-1", status: "running" }] : [],
        })) as never,
        getDependencyReadiness: getDependencyReadiness as never,
        updateStatusCompareAndSet,
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async (action: string) => action === "lookup_source_stage_binding"
      ? { found: false }
      : { stage_binding_key: "a".repeat(64), base_sha: "b".repeat(40), state: "bound" });
    registerAutonomousTools(ctx, invoke as never);
    await expect(handlers.get("bind_source_stage")!({
      stage_key: "MEM9", source_issue_id: "source-9", review_issue_id: "review-9",
      predecessor: { operation_key: "c".repeat(64), source_issue_id: "source-8", review_issue_id: "review-8" },
    }, leadRun)).rejects.toThrow("source_stage_predecessor_not_finalized");
    await expect(handlers.get("bind_source_stage")!({
      stage_key: "MEM9", source_issue_id: "source-9", review_issue_id: "review-9",
      attempt: 3, retry_of_operation_key: "c".repeat(64),
      predecessor: { operation_key: "c".repeat(64), source_issue_id: "source-8", review_issue_id: "review-8" },
    }, leadRun)).rejects.toThrow("source_stage_retry_not_admitted");
    await expect(handlers.get("bind_source_stage")!({
      stage_key: "MEM9", source_issue_id: "source-9", review_issue_id: "review-9",
      attempt: 4, retry_of_operation_key: "c".repeat(64),
      predecessor: { operation_key: "c".repeat(64), source_issue_id: "source-8", review_issue_id: "review-8" },
    }, leadRun)).rejects.toThrow("source_stage_attempt_invalid");
    expect(getDependencyReadiness).toHaveBeenCalledWith("lead-issue", "company-1");
    expect(invoke.mock.calls.filter(([action]) => action === "bind_source_stage")).toHaveLength(0);
    expect(updateStatusCompareAndSet).not.toHaveBeenCalled();
  });

  it("binds a stage-two retry while retaining its current QA blocker and rechecks predecessor finality", async () => {
    await installConfig();
    const finiteConfig = {
      schema: "paperclip-autonomous-bridge-config-v1", company_id: "company-1", project_id: "project-1",
      worker_agent_id: "worker-1", reviewer_agent_id: "reviewer-1", finite_lead_agent_id: "lead-1",
      finite_lead_issue_id: "lead-issue", source_workspace_id: "workspace-project",
      source_workspace_root: "/tmp/brain-source-workspace", python_executable: "/usr/bin/python3",
      source_stage_catalog: [
        { stage_key: "MEM8", workspace_id: "source", initial_base_sha: "b".repeat(40), predecessor_stage_key: null,
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem8" },
        { stage_key: "MEM9", workspace_id: "published", initial_base_sha: null, predecessor_stage_key: "MEM8",
          path_ids: ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"], verifier_policy_id: "policy-mem9" },
      ],
    };
    const { writeFile } = await import("node:fs/promises");
    await writeFile(configPath, JSON.stringify(finiteConfig), { mode: 0o600 });
    const leadRun: ToolRunContext = { companyId: "company-1", projectId: "project-1", agentId: "lead-1", runId: "lead-run" };
    const rows: Record<string, Record<string, unknown>> = {
      "lead-issue": { id: "lead-issue", companyId: "company-1", projectId: "project-1", assigneeAgentId: "lead-1",
        status: "in_progress", statusVersion: 20, executionRunId: "lead-run", projectWorkspaceId: "workspace-project",
        executionWorkspaceId: "workspace-execution", description: "lead" },
      "source-8": { id: "source-8", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "worker-1", status: "done", statusVersion: 5, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "stage one source" },
      "review-8": { id: "review-8", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "reviewer-1", status: "done", statusVersion: 6, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "stage one QA" },
      "source-9-old": { id: "source-9-old", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "worker-1", status: "done", statusVersion: 4, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "old stage two source" },
      "review-9-old": { id: "review-9-old", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "reviewer-1", status: "done", statusVersion: 5, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "old stage two QA" },
      "source-9": { id: "source-9", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "worker-1", status: "backlog", statusVersion: 1, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "new stage two source" },
      "review-9": { id: "review-9", companyId: "company-1", projectId: "project-1", parentId: "lead-issue",
        assigneeAgentId: "reviewer-1", status: "blocked", statusVersion: 2, executionRunId: null,
        projectWorkspaceId: "workspace-project", executionWorkspaceId: "workspace-execution", description: "new stage two QA" },
    };
    const oldOperationKey = "c".repeat(64);
    const predecessorOperationKey = "d".repeat(64);
    const oldStageBinding = {
      found: true, attempt: 1, operation_key: oldOperationKey, operation_state: "review_rejected",
      operation_review_approved: false, state: "active", activation_pending: false,
      source_issue_id: "source-9-old", review_issue_id: "review-9-old", stage_binding_key: "a".repeat(64),
      source_version: 1, review_version: 2, base_sha: "e".repeat(40),
      predecessor_operation_key: predecessorOperationKey, predecessor_source_issue_id: "source-8",
      predecessor_review_issue_id: "review-8",
      source_instruction_sha256: digest("old stage two source"),
      review_instruction_sha256: digest("old stage two QA"),
    };
    const commentNotes = new Map<string, string[]>();
    const updateStatusCompareAndSet = vi.fn(async () => ({ status: "todo", statusVersion: 2 }));
    let predecessorSourceFinalized = false;
    let retryLeadReady = false;
    const readiness = vi.fn(async (id: string) => {
      if (id === "review-9") return { isDependencyReady: false, unresolvedBlockerIssueIds: ["source-9"],
        pendingFinalizeBlockerIssueIds: [], blockerIssueIds: ["source-9"] };
      if (id === "review-8") return { isDependencyReady: predecessorSourceFinalized,
        unresolvedBlockerIssueIds: predecessorSourceFinalized ? [] : ["source-8"],
        pendingFinalizeBlockerIssueIds: [], blockerIssueIds: ["source-8"] };
      if (id === "review-9-old") return { isDependencyReady: true, unresolvedBlockerIssueIds: [],
        pendingFinalizeBlockerIssueIds: [], blockerIssueIds: ["source-9-old"] };
      return { isDependencyReady: retryLeadReady, unresolvedBlockerIssueIds: retryLeadReady ? [] : ["review-9-old"],
        pendingFinalizeBlockerIssueIds: retryLeadReady ? [] : ["review-9-old"], blockerIssueIds: ["review-9-old"] };
    });
    const ctx = {
      config: { get: vi.fn(async () => ({ ownerConfigPath: configPath })) },
      executionWorkspaces: { get: vi.fn(async (id: string) => ({
        id, companyId: "company-1", projectId: "project-1", projectWorkspaceId: "workspace-project",
        path: "/tmp/brain-source-workspace", cwd: "/tmp/brain-source-workspace",
        repoUrl: null, baseRef: null, branchName: null, providerType: "local_fs", providerMetadata: null,
      })) },
      issues: { forCurrentToolRun: () => ({
        get: vi.fn(async (id: string) => rows[id] as never),
        getOrchestration: vi.fn(async ({ issueId }: { issueId: string }) => ({
          issueId,
          blockedByIssueIds: issueId === "lead-issue" ? ["review-9-old"]
            : issueId === "review-9" ? ["source-9"]
              : issueId === "review-9-old" ? ["source-9-old"] : ["source-8"],
          runs: issueId === "lead-issue" ? [{ id: "lead-run", issueId, agentId: "lead-1", status: "running" }] : [],
        })) as never,
        getDependencyReadiness: readiness as never,
        listComments: vi.fn(async (id: string) => (commentNotes.get(id) ?? []).map((body) => ({ body })) as never),
        createComment: vi.fn(async (id: string, body: string) => {
          commentNotes.set(id, [...(commentNotes.get(id) ?? []), body]);
          return {} as never;
        }),
        updateStatusCompareAndSet,
      }) },
      tools: { register: (name: string, _decl: unknown, handler: (params: unknown, runCtx: ToolRunContext) => Promise<unknown>) => handlers.set(name, handler) },
    } as unknown as PluginContext;
    const handlers = new Map<string, (params: unknown, runCtx: ToolRunContext) => Promise<unknown>>();
    const invoke = vi.fn(async (action: string, payload: Record<string, unknown>) => {
      if (action === "lookup_source_stage_binding") {
        return payload.attempt === 2 ? { found: false, attempt: 2 } : oldStageBinding;
      }
      if (action === "bind_source_stage") return {
        stage_binding_key: "f".repeat(64), base_sha: "e".repeat(40), state: "bound",
        accepted_base_context_note: acceptedBaseContextNote("MEM9", [
          "src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py",
        ], "f".repeat(64), "e".repeat(40), { published: true }),
        ...(payload.attempt === 2 ? {
          retry_of_operation_key: oldOperationKey,
          retry_of_candidate_sha: "c".repeat(40),
          retry_workspace_snapshot_sha256: "d".repeat(64),
          retry_workspace_artifact_sha256: "a".repeat(64),
        } : {}),
      };
      return payload.step === "intent" ? { intent: "created" } : { acknowledged: true };
    });
    registerAutonomousTools(ctx, invoke as never);
    const params = {
      stage_key: "MEM9", source_issue_id: "source-9", review_issue_id: "review-9",
      attempt: 2, retry_of_operation_key: oldOperationKey,
      predecessor: { operation_key: predecessorOperationKey, source_issue_id: "source-8", review_issue_id: "review-8" },
    };
    await expect(handlers.get("bind_source_stage")!(params, leadRun))
      .rejects.toThrow("source_stage_predecessor_not_finalized");
    expect(invoke.mock.calls.filter(([action]) => action === "bind_source_stage")).toHaveLength(0);
    predecessorSourceFinalized = true;
    await expect(handlers.get("bind_source_stage")!(params, leadRun))
      .rejects.toThrow("source_stage_retry_not_finalized");
    expect(invoke.mock.calls.filter(([action]) => action === "bind_source_stage")).toHaveLength(0);
    retryLeadReady = true;
    const result = await handlers.get("bind_source_stage")!(params, leadRun) as { content: string };
    expect(result.content).toContain("state:active");
    expect(invoke).toHaveBeenCalledWith("bind_source_stage", expect.objectContaining({
      stage_key: "MEM9", attempt: 2, retry_of_operation_key: oldOperationKey,
      predecessor_operation_key: predecessorOperationKey,
      predecessor_source_issue_id: "source-8", predecessor_review_issue_id: "review-8",
      predecessor_dependency_ready: true, retry_of_dependency_ready: true,
    }), configPath, "/usr/bin/python3");
    expect(readiness).toHaveBeenCalledWith("review-8", "company-1");
    expect(readiness).toHaveBeenCalledWith("lead-issue", "company-1");
    expect(readiness).toHaveBeenCalledWith("review-9-old", "company-1");
    expect(updateStatusCompareAndSet).toHaveBeenCalledWith("source-9", "todo", 1, "company-1");
    expect(commentNotes.get("source-9")?.[0]).toContain(`brain-autonomous-bridge:${"f".repeat(64)}:bound`);
  });
});
