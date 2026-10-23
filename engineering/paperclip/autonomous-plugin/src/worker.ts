import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { constants, openSync, closeSync, fstatSync, readFileSync, lstatSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { isAbsolute } from "node:path";
import { definePlugin, runWorker } from "@paperclipai/plugin-sdk";
import type { PluginContext, ToolRunContext, ToolResult } from "@paperclipai/plugin-sdk";
import manifest from "./manifest.js";

const BRIDGE = fileURLToPath(new URL("../../../paperclip_autonomous_bridge.py", import.meta.url));
const MAX_OUTPUT = 200_000;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SHA_RE = /^[0-9a-f]{40,64}$/;
const KEY_RE = /^[0-9a-f]{64}$/;
const CONFIG_SCHEMA = "paperclip-autonomous-bridge-config-v1";

type ToolAction = "deliver_source_content" | "deliver_source_stage_content" | "bind_source_stage" | "lookup_source_stage_binding" | "source_stage_activation" | "inspect_bound_source_stage" | "approve_bound_source_stage" | "inspect_candidate" | "approve_candidate";
type BridgeResult = Record<string, unknown>;
type BridgeInvoker = (
  action: ToolAction,
  payload: Record<string, unknown>,
  configPath: string,
  pythonExecutable: string,
) => Promise<BridgeResult>;
type OwnerConfig = {
  company_id: string;
  project_id: string;
  worker_agent_id: string;
  reviewer_agent_id: string;
  source_issue_id?: string;
  reviewer_issue_id?: string;
  reviewer_parent_issue_id?: string;
  finite_lead_agent_id?: string;
  finite_lead_issue_id?: string;
  source_stage_catalog?: Array<{ stage_key: string; workspace_id: string; initial_base_sha: string | null; predecessor_stage_key: string | null; path_ids: string[]; verifier_policy_id: string }>;
  canonical_repo?: string;
  canonical_ref?: string;
  canonical_expected_tip?: string;
  source_workspace_id?: string;
  source_workspace_root?: string;
  python_executable: string;
  configPath: string;
};

function fail(code: string): never {
  throw new Error(code);
}

function instructionDigest(description: unknown): string {
  if (typeof description !== "string" || !description.trim()) fail("source_stage_instruction_missing");
  return createHash("sha256").update(description, "utf8").digest("hex");
}

export async function assertRunBoundIssue(
  ctx: Pick<PluginContext, "issues">,
  runCtx: ToolRunContext,
  issueId: string,
  allowedStatuses: string[] = ["in_progress"],
): Promise<void> {
  if (![runCtx.companyId, runCtx.projectId, runCtx.agentId, runCtx.runId].every((id) => typeof id === "string" && ID_RE.test(id))) {
    fail("host_run_context_invalid");
  }
  const scopedIssues = ctx.issues.forCurrentToolRun();
  const [issue, orchestration] = await Promise.all([
    scopedIssues.get(issueId, runCtx.companyId),
    scopedIssues.getOrchestration({ issueId, companyId: runCtx.companyId }),
  ]);
  const boundRun = orchestration.runs.find((run: { id: string; issueId: string | null; agentId: string; status: string }) => run.id === runCtx.runId);
  if (!issue || issue.id !== issueId || issue.companyId !== runCtx.companyId || issue.projectId !== runCtx.projectId
      || issue.assigneeAgentId !== runCtx.agentId || issue.executionRunId !== runCtx.runId || !allowedStatuses.includes(issue.status)
      || !boundRun || boundRun.issueId !== issueId || boundRun.agentId !== runCtx.agentId || boundRun.status !== "running") {
    fail("host_issue_run_binding_mismatch");
  }
}

async function readOwnerConfig(ctx: Pick<PluginContext, "config">, companyId: string): Promise<OwnerConfig> {
  const pluginConfig = await ctx.config.get(companyId);
  const configPath = pluginConfig.ownerConfigPath;
  if (typeof configPath !== "string" || !isAbsolute(configPath)) fail("owner_bridge_config_path_unavailable");
  let fd: number | undefined;
  try {
    const before = lstatSync(configPath);
    if (!before.isFile() || before.isSymbolicLink() || (before.mode & 0o077) !== 0 || before.uid !== process.getuid?.()) {
      fail("owner_bridge_config_permissions_invalid");
    }
    fd = openSync(configPath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    const opened = fstatSync(fd);
    if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino || opened.size > 128_000) {
      fail("owner_bridge_config_changed");
    }
    const raw = readFileSync(fd, "utf8");
    const after = fstatSync(fd);
    if (raw.length !== opened.size || after.dev !== opened.dev || after.ino !== opened.ino || after.mtimeMs !== opened.mtimeMs) {
      fail("owner_bridge_config_changed");
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const keys = ["company_id", "project_id", "worker_agent_id", "reviewer_agent_id"] as const;
    const hasAnyLegacyPair = parsed.source_issue_id !== undefined || parsed.reviewer_issue_id !== undefined;
    const hasLegacyPair = typeof parsed.source_issue_id === "string" && typeof parsed.reviewer_issue_id === "string";
    const finite = typeof parsed.finite_lead_agent_id === "string" && typeof parsed.finite_lead_issue_id === "string"
      && Array.isArray(parsed.source_stage_catalog) && parsed.source_stage_catalog.length === 2;
    const canonicalFields = ["canonical_repo", "canonical_ref", "canonical_expected_tip"];
    const hasCanonical = canonicalFields.some((key) => parsed[key] !== undefined);
    const canonicalValid = !hasCanonical || (typeof parsed.canonical_repo === "string" && isAbsolute(parsed.canonical_repo)
      && parsed.canonical_ref === "refs/heads/brain/development" && typeof parsed.canonical_expected_tip === "string"
      && SHA_RE.test(parsed.canonical_expected_tip));
    const workspaceValid = !finite || (typeof parsed.source_workspace_id === "string" && ID_RE.test(parsed.source_workspace_id)
      && typeof parsed.source_workspace_root === "string" && isAbsolute(parsed.source_workspace_root)
      && !parsed.source_workspace_root.split("/").includes("..") && parsed.source_workspace_root !== "/");
    if (parsed.schema !== CONFIG_SCHEMA || keys.some((key) => typeof parsed[key] !== "string" || !ID_RE.test(parsed[key] as string))
        || (hasAnyLegacyPair && !hasLegacyPair)
        || (!hasLegacyPair && !finite)
        || (hasLegacyPair && (!ID_RE.test(parsed.source_issue_id as string) || !ID_RE.test(parsed.reviewer_issue_id as string)))
        || (finite && (!ID_RE.test(parsed.finite_lead_agent_id as string) || !ID_RE.test(parsed.finite_lead_issue_id as string)))
        || !canonicalValid || (hasCanonical && !finite) || !workspaceValid
        || typeof parsed.python_executable !== "string" || !isAbsolute(parsed.python_executable)) {
      fail("owner_bridge_config_invalid");
    }
    if ("reviewer_parent_issue_id" in parsed
        && (typeof parsed.reviewer_parent_issue_id !== "string" || !ID_RE.test(parsed.reviewer_parent_issue_id))) {
      fail("owner_bridge_config_invalid");
    }
    return {
      ...Object.fromEntries(keys.map((key) => [key, parsed[key]])),
      ...(hasLegacyPair ? { source_issue_id: parsed.source_issue_id as string, reviewer_issue_id: parsed.reviewer_issue_id as string } : {}),
      ...(finite ? {
        finite_lead_agent_id: parsed.finite_lead_agent_id as string,
        finite_lead_issue_id: parsed.finite_lead_issue_id as string,
        source_stage_catalog: parsed.source_stage_catalog as OwnerConfig["source_stage_catalog"],
      } : {}),
      ...(hasCanonical ? {
        canonical_repo: parsed.canonical_repo as string,
        canonical_ref: parsed.canonical_ref as string,
        canonical_expected_tip: parsed.canonical_expected_tip as string,
      } : {}),
      ...(finite ? {
        source_workspace_id: parsed.source_workspace_id as string,
        source_workspace_root: parsed.source_workspace_root as string,
      } : {}),
      ...(typeof parsed.reviewer_parent_issue_id === "string"
        ? { reviewer_parent_issue_id: parsed.reviewer_parent_issue_id }
        : {}),
      python_executable: parsed.python_executable,
      configPath,
    } as OwnerConfig;
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("owner_bridge_")) throw error;
    return fail("owner_bridge_config_unreadable");
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

async function assertPinnedSourceWorkspace(
  ctx: Pick<PluginContext, "issues" | "executionWorkspaces">,
  owner: OwnerConfig,
  issueIds: string[],
): Promise<void> {
  if (!owner.source_workspace_id || !owner.source_workspace_root) fail("source_workspace_not_owner_configured");
  const issues = await Promise.all(issueIds.map((id) => readNativeIssue(ctx, id, owner.company_id)));
  const executionIds = new Set<string>();
  for (const issue of issues) {
    const workspaceIssue = issue as typeof issue & { projectWorkspaceId?: string | null; executionWorkspaceId?: string | null };
    if (workspaceIssue.projectWorkspaceId !== owner.source_workspace_id
        || typeof workspaceIssue.executionWorkspaceId !== "string") fail("source_workspace_issue_binding_mismatch");
    executionIds.add(workspaceIssue.executionWorkspaceId);
  }
  if (executionIds.size !== 1) fail("source_workspace_execution_binding_mismatch");
  const workspace = await ctx.executionWorkspaces.get([...executionIds][0], owner.company_id);
  if (!workspace || workspace.projectWorkspaceId !== owner.source_workspace_id
      || workspace.path !== owner.source_workspace_root || workspace.cwd !== owner.source_workspace_root
      || workspace.repoUrl !== null || workspace.baseRef !== null || workspace.branchName !== null
      || workspace.providerType !== "local_fs") {
    fail("source_workspace_metadata_mismatch");
  }
}

function privateBridgeInvoker(): BridgeInvoker {
  return (action, payload, configPath, pythonExecutable) => new Promise((resolve, reject) => {
    const child = spawn(pythonExecutable, ["-I", "-S", "-B", BRIDGE, configPath], {
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: { PATH: "/usr/bin:/bin", LANG: "C", LC_ALL: "C", PYTHONUTF8: "1" },
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;
    const finish = (error?: Error, value?: BridgeResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(value ?? {});
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(new Error("bridge_timeout"));
    }, 120_000);
    for (const [stream, chunks] of [[child.stdout, stdout], [child.stderr, stderr]] as const) {
      stream.on("data", (chunk: Buffer) => {
        outputBytes += chunk.length;
        if (outputBytes > MAX_OUTPUT) {
          child.kill("SIGKILL");
          finish(new Error("bridge_output_limit"));
        } else chunks.push(chunk);
      });
    }
    child.on("error", () => finish(new Error("bridge_spawn_failed")));
    child.stdin.on("error", () => finish(new Error("bridge_stdin_failed")));
    child.on("close", (code) => {
      if (settled) return;
      let response: unknown;
      try {
        response = JSON.parse(Buffer.concat(stdout).toString("utf8"));
      } catch {
        finish(new Error("bridge_response_invalid"));
        return;
      }
      const envelope = response as { ok?: unknown; result?: unknown; error?: unknown };
      if (code !== 0 || envelope.ok !== true || typeof envelope.result !== "object" || envelope.result === null) {
        const reason = typeof envelope.error === "string" ? envelope.error : (stderr.length ? "bridge_failed" : "bridge_rejected");
        finish(new Error(reason));
        return;
      }
      finish(undefined, envelope.result as BridgeResult);
    });
    child.stdin.end(JSON.stringify({ action, payload }));
  });
}

function validateParams(action: ToolAction, value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("tool_parameters_invalid");
  const params = value as Record<string, unknown>;
  const keys = action === "deliver_source_content" ? ["base_sha", "files"]
    : action === "inspect_candidate" ? ["operation_key", "candidate_sha"]
      : ["operation_key", "candidate_sha", "approved", "evidence"];
  if (Object.keys(params).some((key) => !keys.includes(key)) || keys.some((key) => !(key in params))) fail("tool_parameters_invalid");
  if (action === "deliver_source_content") {
    if (typeof params.base_sha !== "string" || !SHA_RE.test(params.base_sha)
        || !Array.isArray(params.files) || Buffer.byteLength(JSON.stringify(params.files), "utf8") > 1_600_000) fail("source_delivery_invalid");
  } else if (action === "inspect_candidate") {
    if (typeof params.operation_key !== "string" || !KEY_RE.test(params.operation_key)
        || typeof params.candidate_sha !== "string" || !SHA_RE.test(params.candidate_sha)) fail("candidate_identity_invalid");
  } else if (typeof params.operation_key !== "string" || !KEY_RE.test(params.operation_key)
      || typeof params.candidate_sha !== "string" || !SHA_RE.test(params.candidate_sha)
      || typeof params.approved !== "boolean" || !params.evidence || typeof params.evidence !== "object"
      || Array.isArray(params.evidence)
      || typeof (params.evidence as Record<string, unknown>).inspection_digest !== "string"
      || !KEY_RE.test((params.evidence as Record<string, string>).inspection_digest)
      || Buffer.byteLength(JSON.stringify(params.evidence), "utf8") > 16_000) {
    fail("review_evidence_invalid");
  }
  return params;
}

async function persistIssueNote(
  ctx: Pick<PluginContext, "issues">,
  issueId: string,
  companyId: string,
  idempotencyKey: string,
  body: string,
): Promise<void> {
  const scopedIssues = ctx.issues.forCurrentToolRun();
  const comments = await scopedIssues.listComments(issueId, companyId);
  const marker = `brain-autonomous-bridge:${idempotencyKey}`;
  if (!body.split("\n").includes(marker)) fail("paperclip_evidence_marker_missing");
  const existing = comments.filter((comment) => comment.body.split("\n").includes(marker));
  if (existing.length > 1) fail("paperclip_evidence_replay_duplicate");
  if (existing.some((comment) => comment.body !== body)) fail("paperclip_evidence_replay_mismatch");
  if (existing.length === 0) {
    await scopedIssues.createComment(issueId, body, companyId);
  }
}

function sourceStageBoundNote(
  stageKey: string,
  stageBindingKey: string,
  baseSha: string,
  sourceIssueId: string,
  reviewIssueId: string,
  sourceInstructionSha256: string,
  reviewInstructionSha256: string,
  attempt = 1,
  retryOfOperationKey: string | null = null,
): { idempotencyKey: string; body: string } {
  const idempotencyKey = `${stageBindingKey}:bound`;
  return {
    idempotencyKey,
    body: [
      `brain-autonomous-bridge:${idempotencyKey}`,
      `brain-source-stage:${stageBindingKey}:bound`,
      `stage_key:${stageKey}`,
      ...(attempt === 1 ? [] : [`attempt:${attempt}`]),
      ...(retryOfOperationKey === null ? [] : [`retry_of_operation_key:${retryOfOperationKey}`]),
      `stage_binding_key:${stageBindingKey}`,
      `base_sha:${baseSha}`,
      `source_issue_id:${sourceIssueId}`,
      `review_issue_id:${reviewIssueId}`,
      `source_instruction_sha256:${sourceInstructionSha256}`,
      `review_instruction_sha256:${reviewInstructionSha256}`,
    ].join("\n"),
  };
}

async function persistSourceStageBoundNotes(
  ctx: Pick<PluginContext, "issues">,
  owner: OwnerConfig,
  note: { idempotencyKey: string; body: string },
  sourceIssueId: string,
  reviewIssueId: string,
  sourceInstructionSha256: string,
  reviewInstructionSha256: string,
  includeLead: boolean,
): Promise<void> {
  const [source, review] = await Promise.all([
    readNativeIssue(ctx, sourceIssueId, owner.company_id),
    readNativeIssue(ctx, reviewIssueId, owner.company_id),
  ]);
  if (source.projectId !== owner.project_id || source.parentId !== owner.finite_lead_issue_id
      || source.assigneeAgentId !== owner.worker_agent_id
      || review.projectId !== owner.project_id || review.parentId !== owner.finite_lead_issue_id
      || review.assigneeAgentId !== owner.reviewer_agent_id
      || instructionDigest(source.description) !== sourceInstructionSha256
      || instructionDigest(review.description) !== reviewInstructionSha256) {
    fail("source_stage_instruction_binding_mismatch");
  }
  await persistIssueNote(ctx, sourceIssueId, owner.company_id, note.idempotencyKey, note.body);
  await persistIssueNote(ctx, reviewIssueId, owner.company_id, note.idempotencyKey, note.body);
  if (includeLead && owner.finite_lead_issue_id) {
    await persistIssueNote(ctx, owner.finite_lead_issue_id, owner.company_id, note.idempotencyKey, note.body);
  }
}

async function validateReviewerWakeTarget(
  ctx: Pick<PluginContext, "issues">,
  owner: OwnerConfig,
): Promise<void> {
  if (!owner.source_issue_id || !owner.reviewer_issue_id) fail("legacy_reviewer_route_not_owner_configured");
  const scopedIssues = ctx.issues.forCurrentToolRun();
  const explicitParent = owner.reviewer_parent_issue_id;
  const [issue, orchestration, sourceIssue] = await Promise.all([
    scopedIssues.get(owner.reviewer_issue_id, owner.company_id),
    scopedIssues.getOrchestration({ issueId: owner.reviewer_issue_id, companyId: owner.company_id }),
    explicitParent ? scopedIssues.get(owner.source_issue_id, owner.company_id) : Promise.resolve(null),
  ]);
  if (!issue || issue.id !== owner.reviewer_issue_id || issue.companyId !== owner.company_id
      || issue.projectId !== owner.project_id || issue.assigneeAgentId !== owner.reviewer_agent_id
      || issue.parentId !== (explicitParent ?? owner.source_issue_id)
      || (explicitParent !== undefined && (!sourceIssue || sourceIssue.id !== owner.source_issue_id
        || sourceIssue.companyId !== owner.company_id || sourceIssue.projectId !== owner.project_id
        || sourceIssue.parentId !== explicitParent))
      || !["backlog", "todo"].includes(issue.status)
      || issue.executionRunId !== null || orchestration.blockedByIssueIds.length !== 1
      || orchestration.blockedByIssueIds[0] !== owner.source_issue_id) {
    fail("reviewer_issue_wake_target_mismatch");
  }
}

function requireSourceStage(owner: OwnerConfig, stageKey: string) {
  const stage = owner.source_stage_catalog?.find((item) => item.stage_key === stageKey);
  if (!stage || !owner.finite_lead_issue_id || !owner.finite_lead_agent_id) fail("source_stage_not_owner_admitted");
  return stage;
}

type StagePredecessor = { operation_key: string; source_issue_id: string; review_issue_id: string };

function readStagePredecessor(
  stage: NonNullable<OwnerConfig["source_stage_catalog"]>[number],
  raw: unknown,
): StagePredecessor | null {
  if (stage.predecessor_stage_key === null) {
    if (raw !== undefined) fail("source_stage_unexpected_predecessor");
    return null;
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("source_stage_predecessor_required");
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).length !== 3 || typeof value.operation_key !== "string" || !KEY_RE.test(value.operation_key)
      || typeof value.source_issue_id !== "string" || !ID_RE.test(value.source_issue_id)
      || typeof value.review_issue_id !== "string" || !ID_RE.test(value.review_issue_id)) {
    fail("source_stage_predecessor_invalid");
  }
  return {
    operation_key: value.operation_key,
    source_issue_id: value.source_issue_id,
    review_issue_id: value.review_issue_id,
  };
}

async function readBoundStage(
  invoke: BridgeInvoker,
  owner: OwnerConfig,
  configPath: string,
  stageKey: string,
  issueSide: "source_issue_id" | "review_issue_id",
  issueId: string,
  stageBindingKey: string,
): Promise<BridgeResult> {
  if (!owner.finite_lead_agent_id || !owner.finite_lead_issue_id) fail("source_stage_not_owner_admitted");
  const binding = await invoke("lookup_source_stage_binding", {
    company_id: owner.company_id,
    project_id: owner.project_id,
    lead_agent_id: owner.finite_lead_agent_id,
    lead_issue_id: owner.finite_lead_issue_id,
    stage_key: stageKey,
    stage_binding_key: stageBindingKey,
    [issueSide]: issueId,
  }, configPath, owner.python_executable);
  if (binding.found !== true || binding.stage_binding_key !== stageBindingKey
      || typeof binding.source_instruction_sha256 !== "string" || !KEY_RE.test(binding.source_instruction_sha256)
      || typeof binding.review_instruction_sha256 !== "string" || !KEY_RE.test(binding.review_instruction_sha256)) {
    fail("source_stage_binding_unavailable");
  }
  return binding;
}

async function assertBoundInstruction(
  invoke: BridgeInvoker,
  owner: OwnerConfig,
  stageKey: string,
  issueSide: "source_issue_id" | "review_issue_id",
  issue: { id: string; description?: string | null },
  stageBindingKey: string,
): Promise<string> {
  if (typeof issue.description !== "string" || !issue.description.trim()) fail("source_stage_instruction_missing");
  const binding = await readBoundStage(
    invoke, owner, owner.configPath, stageKey, issueSide, issue.id, stageBindingKey,
  );
  const digest = instructionDigest(issue.description);
  const field = issueSide === "source_issue_id" ? "source_instruction_sha256" : "review_instruction_sha256";
  if (binding[field] !== digest) fail("source_stage_instruction_binding_mismatch");
  return digest;
}

async function readNativeIssue(ctx: Pick<PluginContext, "issues">, issueId: string, companyId: string) {
  const issue = await ctx.issues.forCurrentToolRun().get(issueId, companyId);
  if (!issue || issue.id !== issueId || issue.companyId !== companyId) fail("source_stage_issue_unavailable");
  return issue;
}

async function assertStageCurrentRun(
  ctx: Pick<PluginContext, "issues">,
  runCtx: ToolRunContext,
  issueId: string,
  allowedStatuses: string[] = ["in_progress"],
): Promise<void> {
  if (![runCtx.companyId, runCtx.projectId, runCtx.agentId, runCtx.runId].every(
    (value) => typeof value === "string" && ID_RE.test(value),
  )) fail("host_run_context_invalid");
  const issue = await readNativeIssue(ctx, issueId, runCtx.companyId);
  if (
    issue.projectId !== runCtx.projectId
    || issue.assigneeAgentId !== runCtx.agentId
    || issue.executionRunId !== runCtx.runId
    || !allowedStatuses.includes(issue.status)
  ) fail("host_issue_run_binding_mismatch");
}

async function validateStagePair(
  ctx: Pick<PluginContext, "issues">,
  owner: OwnerConfig,
  stage: NonNullable<OwnerConfig["source_stage_catalog"]>[number],
  sourceIssueId: string,
  reviewIssueId: string,
) {
  const scoped = ctx.issues.forCurrentToolRun();
  const [source, review, orchestration] = await Promise.all([
    readNativeIssue(ctx, sourceIssueId, owner.company_id),
    readNativeIssue(ctx, reviewIssueId, owner.company_id),
    scoped.getOrchestration({ issueId: reviewIssueId, companyId: owner.company_id }),
  ]);
  if (source.projectId !== owner.project_id || source.parentId !== owner.finite_lead_issue_id
      || source.assigneeAgentId !== owner.worker_agent_id || source.status !== "backlog"
      || source.executionRunId !== null || !Number.isSafeInteger(source.statusVersion)
      || typeof source.description !== "string" || !source.description.trim()) fail("source_stage_source_admission_mismatch");
  if (review.projectId !== owner.project_id || review.parentId !== owner.finite_lead_issue_id
      || review.assigneeAgentId !== owner.reviewer_agent_id || review.status !== "blocked"
      || review.executionRunId !== null || !Number.isSafeInteger(review.statusVersion)
      || typeof review.description !== "string" || !review.description.trim()
      || orchestration.blockedByIssueIds.length !== 1 || orchestration.blockedByIssueIds[0] !== sourceIssueId) {
    fail("source_stage_review_admission_mismatch");
  }
  const readiness = await scoped.getDependencyReadiness(reviewIssueId, owner.company_id);
  if (readiness.isDependencyReady !== false || !readiness.unresolvedBlockerIssueIds.includes(sourceIssueId)) {
    fail("source_stage_review_dependency_not_blocked");
  }
  return { source, review };
}

async function validateStageRetry(
  ctx: Pick<PluginContext, "issues">,
  owner: OwnerConfig,
  stageKey: string,
  retryOfOperationKey: string,
  source: { id: string },
  review: { id: string },
  predecessor: StagePredecessor | null,
  invoke: BridgeInvoker,
): Promise<BridgeResult> {
  if (!owner.finite_lead_issue_id || !owner.finite_lead_agent_id) fail("source_stage_not_owner_admitted");
  const prior = await invoke("lookup_source_stage_binding", {
    company_id: owner.company_id,
    project_id: owner.project_id,
    lead_agent_id: owner.finite_lead_agent_id,
    lead_issue_id: owner.finite_lead_issue_id,
    stage_key: stageKey,
    attempt: 1,
  }, owner.configPath, owner.python_executable);
  if (prior.found !== true || prior.attempt !== 1 || prior.operation_key !== retryOfOperationKey
      || prior.operation_state !== "review_rejected" || prior.operation_review_approved !== false
      || prior.state !== "active" || prior.activation_pending === true
      || typeof prior.source_issue_id !== "string" || typeof prior.review_issue_id !== "string"
      || prior.source_issue_id === source.id || prior.review_issue_id === review.id
      || typeof prior.stage_binding_key !== "string" || !KEY_RE.test(prior.stage_binding_key)
      || typeof prior.source_version !== "number" || !Number.isSafeInteger(prior.source_version)
      || typeof prior.review_version !== "number" || !Number.isSafeInteger(prior.review_version)
      || (owner.canonical_repo && prior.canonical_status !== "prepared")
      || prior.predecessor_operation_key !== (predecessor?.operation_key ?? null)
      || prior.predecessor_source_issue_id !== (predecessor?.source_issue_id ?? null)
      || prior.predecessor_review_issue_id !== (predecessor?.review_issue_id ?? null)) {
    fail("source_stage_retry_not_admitted");
  }
  const [oldSource, oldReview] = await Promise.all([
    readNativeIssue(ctx, prior.source_issue_id, owner.company_id),
    readNativeIssue(ctx, prior.review_issue_id, owner.company_id),
  ]);
  const scoped = ctx.issues.forCurrentToolRun();
  const [oldReviewOrchestration, oldReviewReadiness, leadOrchestration, leadReadiness] = await Promise.all([
    scoped.getOrchestration({ issueId: oldReview.id, companyId: owner.company_id }),
    scoped.getDependencyReadiness(oldReview.id, owner.company_id),
    scoped.getOrchestration({ issueId: owner.finite_lead_issue_id, companyId: owner.company_id }),
    scoped.getDependencyReadiness(owner.finite_lead_issue_id, owner.company_id),
  ]);
  if (oldSource.projectId !== owner.project_id || oldSource.parentId !== owner.finite_lead_issue_id
      || oldSource.assigneeAgentId !== owner.worker_agent_id || oldSource.status !== "done"
      || oldSource.executionRunId !== null || !Number.isSafeInteger(oldSource.statusVersion)
      || (oldSource.statusVersion as number) <= prior.source_version
      || instructionDigest(oldSource.description) !== prior.source_instruction_sha256
      || oldReview.projectId !== owner.project_id || oldReview.parentId !== owner.finite_lead_issue_id
      || oldReview.assigneeAgentId !== owner.reviewer_agent_id || oldReview.status !== "done"
      || oldReview.executionRunId !== null || !Number.isSafeInteger(oldReview.statusVersion)
      || (oldReview.statusVersion as number) <= prior.review_version
      || instructionDigest(oldReview.description) !== prior.review_instruction_sha256
      || oldReviewOrchestration.blockedByIssueIds.length !== 1
      || oldReviewOrchestration.blockedByIssueIds[0] !== oldSource.id
      || oldReviewReadiness.isDependencyReady !== true
      || oldReviewReadiness.unresolvedBlockerIssueIds.length !== 0
      || oldReviewReadiness.blockerIssueIds.length !== 1
      || oldReviewReadiness.blockerIssueIds[0] !== oldSource.id
      || oldReviewReadiness.pendingFinalizeBlockerIssueIds.length !== 0
      || leadOrchestration.blockedByIssueIds.length !== 1
      || leadOrchestration.blockedByIssueIds[0] !== oldReview.id
      || leadReadiness.isDependencyReady !== true
      || leadReadiness.unresolvedBlockerIssueIds.length !== 0
      || leadReadiness.pendingFinalizeBlockerIssueIds.length !== 0) {
    fail("source_stage_retry_not_finalized");
  }
  return {
    source_status: oldSource.status,
    source_version: oldSource.statusVersion,
    review_status: oldReview.status,
    review_version: oldReview.statusVersion,
    dependency_ready: true,
  };
}

async function activateSourceStage(
  ctx: Pick<PluginContext, "issues">,
  invoke: BridgeInvoker,
  owner: OwnerConfig,
  configPath: string,
  stageBindingKey: string,
  source: { id: string; status: string; statusVersion: number },
) {
  const intent = await invoke("source_stage_activation", {
    step: "intent", stage_binding_key: stageBindingKey, issue_id: source.id,
    observed_status: source.status, observed_version: source.statusVersion,
  }, configPath, owner.python_executable);
  if (intent.intent !== "created") fail("source_stage_activation_reconciliation_required");
  const updated = await ctx.issues.forCurrentToolRun().updateStatusCompareAndSet(
    source.id, "todo", source.statusVersion, owner.company_id,
  );
  if (updated.status !== "todo" || updated.statusVersion !== source.statusVersion + 1) {
    fail("source_stage_activation_ack_unconfirmed");
  }
  await invoke("source_stage_activation", {
    step: "ack", stage_binding_key: stageBindingKey, issue_id: source.id,
    mutation_status: updated.status, mutation_status_version: updated.statusVersion,
  }, configPath, owner.python_executable);
}

async function validateStageReviewRun(
  ctx: Pick<PluginContext, "issues">,
  runCtx: ToolRunContext,
  owner: OwnerConfig,
  stageKey: string,
  reviewIssueId: string,
) {
  const stage = requireSourceStage(owner, stageKey);
  if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id
      || runCtx.agentId !== owner.reviewer_agent_id) fail("reviewer_actor_binding_mismatch");
  await assertStageCurrentRun(ctx, runCtx, reviewIssueId);
  const review = await readNativeIssue(ctx, reviewIssueId, owner.company_id);
  if (review.projectId !== owner.project_id || review.parentId !== owner.finite_lead_issue_id
      || review.assigneeAgentId !== owner.reviewer_agent_id || review.status !== "in_progress"
      || review.executionRunId !== runCtx.runId) {
    fail("source_stage_review_binding_mismatch");
  }
  return { stage, review };
}

export function registerAutonomousTools(ctx: PluginContext, invoke: BridgeInvoker): void {
  ctx.tools.register("bind_source_stage", {
    displayName: "Bind Finite Brain Source Stage",
    description: "Bind the owner-admitted native source and review tasks to one finite content stage, then CAS-activate the source task last.",
    parametersSchema: {
      type: "object",
      properties: {
        stage_key: { type: "string" }, source_issue_id: { type: "string" }, review_issue_id: { type: "string" },
        attempt: { type: "integer", minimum: 1, maximum: 2 }, retry_of_operation_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
        predecessor: { type: "object", properties: {
          operation_key: { type: "string" }, source_issue_id: { type: "string" }, review_issue_id: { type: "string" },
        }, required: ["operation_key", "source_issue_id", "review_issue_id"], additionalProperties: false },
      },
      required: ["stage_key", "source_issue_id", "review_issue_id"], additionalProperties: false,
    },
  }, async (raw, runCtx): Promise<ToolResult> => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("tool_parameters_invalid");
    const params = raw as Record<string, unknown>;
    if (Object.keys(params).some((key) => !["stage_key", "source_issue_id", "review_issue_id", "predecessor", "attempt", "retry_of_operation_key"].includes(key))
        || typeof params.stage_key !== "string" || !ID_RE.test(params.stage_key)
        || typeof params.source_issue_id !== "string" || !ID_RE.test(params.source_issue_id)
        || typeof params.review_issue_id !== "string" || !ID_RE.test(params.review_issue_id)) fail("source_stage_parameters_invalid");
    const attempt = Object.prototype.hasOwnProperty.call(params, "attempt") ? params.attempt : 1;
    if (typeof attempt !== "number" || !Number.isSafeInteger(attempt) || (attempt !== 1 && attempt !== 2)
        || (attempt === 1 && Object.prototype.hasOwnProperty.call(params, "retry_of_operation_key"))
        || (attempt === 2 && (typeof params.retry_of_operation_key !== "string" || !KEY_RE.test(params.retry_of_operation_key)))) {
      fail("source_stage_attempt_invalid");
    }
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    const stage = requireSourceStage(owner, params.stage_key);
    const predecessorRequest = readStagePredecessor(stage, params.predecessor);
    if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id
        || runCtx.agentId !== owner.finite_lead_agent_id || !owner.finite_lead_issue_id) fail("source_stage_lead_binding_mismatch");
    await assertRunBoundIssue(ctx, runCtx, owner.finite_lead_issue_id);
    const existing = await invoke("lookup_source_stage_binding", {
      company_id: owner.company_id, project_id: owner.project_id,
      lead_agent_id: owner.finite_lead_agent_id, lead_issue_id: owner.finite_lead_issue_id,
      stage_key: stage.stage_key, source_issue_id: params.source_issue_id,
      review_issue_id: params.review_issue_id, attempt,
    }, owner.configPath, owner.python_executable);
    if (existing.found === true) {
      if (typeof existing.stage_binding_key !== "string" || !KEY_RE.test(existing.stage_binding_key)
          || typeof existing.base_sha !== "string" || !SHA_RE.test(existing.base_sha)
          || existing.attempt !== attempt
          || (attempt === 2 && existing.retry_of_operation_key !== params.retry_of_operation_key)) fail("bridge_result_invalid");
      if (existing.predecessor_operation_key !== (predecessorRequest?.operation_key ?? null)
          || existing.predecessor_source_issue_id !== (predecessorRequest?.source_issue_id ?? null)
          || existing.predecessor_review_issue_id !== (predecessorRequest?.review_issue_id ?? null)) {
        fail("source_stage_bind_replay_mismatch");
      }
      if (existing.activation_pending === true) fail("source_stage_activation_reconciliation_required");
      if (existing.state === "active") {
        if (typeof existing.source_instruction_sha256 !== "string" || !KEY_RE.test(existing.source_instruction_sha256)
            || typeof existing.review_instruction_sha256 !== "string" || !KEY_RE.test(existing.review_instruction_sha256)) {
          fail("source_stage_binding_unavailable");
        }
        const note = sourceStageBoundNote(
          stage.stage_key, existing.stage_binding_key, existing.base_sha, params.source_issue_id, params.review_issue_id,
          existing.source_instruction_sha256, existing.review_instruction_sha256,
          attempt, attempt === 2 ? String(existing.retry_of_operation_key) : null,
        );
        await persistSourceStageBoundNotes(
          ctx, owner, note, params.source_issue_id, params.review_issue_id,
          existing.source_instruction_sha256, existing.review_instruction_sha256, true,
        );
        return { content: `stage_key:${stage.stage_key}\nstage_binding_key:${existing.stage_binding_key}\nbase_sha:${existing.base_sha}\nstate:active` };
      }
      if (existing.state !== "bound") fail("source_stage_activation_reconciliation_required");
    } else if (existing.found !== false) fail("bridge_result_invalid");
    const { source, review } = await validateStagePair(
      ctx, owner, stage, params.source_issue_id, params.review_issue_id,
    );
    await assertPinnedSourceWorkspace(
      ctx, owner, [owner.finite_lead_issue_id, source.id, review.id],
    );
    let predecessor = {
      operation_key: null as string | null, source_issue_id: null as string | null,
      review_issue_id: null as string | null, source_status: null as string | null,
      source_version: null as number | null, review_status: null as string | null,
      review_version: null as number | null, dependency_ready: null as boolean | null,
    };
    if (stage.predecessor_stage_key !== null) {
      const prior = predecessorRequest;
      if (!prior) fail("source_stage_predecessor_required");
      const [priorSource, priorReview] = await Promise.all([
        readNativeIssue(ctx, prior.source_issue_id, owner.company_id),
        readNativeIssue(ctx, prior.review_issue_id, owner.company_id),
      ]);
      const scoped = ctx.issues.forCurrentToolRun();
      const [priorReviewReadiness, leadOrchestration, leadReadiness] = await Promise.all([
        scoped.getDependencyReadiness(prior.review_issue_id, owner.company_id),
        scoped.getOrchestration({ issueId: owner.finite_lead_issue_id, companyId: owner.company_id }),
        scoped.getDependencyReadiness(owner.finite_lead_issue_id, owner.company_id),
      ]);
      const predecessorFinalized = leadOrchestration.blockedByIssueIds.length === 1
        && leadOrchestration.blockedByIssueIds[0] === priorReview.id
        && leadReadiness.isDependencyReady === true
        && leadReadiness.unresolvedBlockerIssueIds.length === 0
        && leadReadiness.pendingFinalizeBlockerIssueIds.length === 0;
      if (priorSource.projectId !== owner.project_id || priorSource.parentId !== owner.finite_lead_issue_id
          || priorSource.assigneeAgentId !== owner.worker_agent_id || priorSource.status !== "done"
          || !Number.isSafeInteger(priorSource.statusVersion) || !Number.isSafeInteger(priorReview.statusVersion)
          || priorSource.executionRunId !== null || priorReview.projectId !== owner.project_id
          || priorReview.parentId !== owner.finite_lead_issue_id || priorReview.assigneeAgentId !== owner.reviewer_agent_id
          || priorReview.status !== "done" || priorReview.executionRunId !== null
          || priorReviewReadiness.isDependencyReady !== true || priorReviewReadiness.unresolvedBlockerIssueIds.length !== 0
          || priorReviewReadiness.blockerIssueIds.length !== 1 || priorReviewReadiness.blockerIssueIds[0] !== priorSource.id
          || priorReviewReadiness.pendingFinalizeBlockerIssueIds.length !== 0
          || !predecessorFinalized) fail("source_stage_predecessor_not_finalized");
      predecessor = {
        operation_key: prior.operation_key,
        source_issue_id: prior.source_issue_id,
        review_issue_id: prior.review_issue_id,
        source_status: priorSource.status,
        source_version: priorSource.statusVersion as number,
        review_status: priorReview.status,
        review_version: priorReview.statusVersion as number,
        dependency_ready: predecessorFinalized,
      };
    }
    const retryValidation = attempt === 2
      ? await validateStageRetry(
        ctx, owner, stage.stage_key, params.retry_of_operation_key as string,
        source, review,
        predecessor.operation_key === null ? null : {
          operation_key: predecessor.operation_key,
          source_issue_id: predecessor.source_issue_id!,
          review_issue_id: predecessor.review_issue_id!,
        },
        invoke,
      )
      : null;
    const sourceInstructionSha256 = instructionDigest(source.description);
    const reviewInstructionSha256 = instructionDigest(review.description);
    const result = await invoke("bind_source_stage", {
      company_id: owner.company_id, project_id: owner.project_id,
      lead_agent_id: owner.finite_lead_agent_id, lead_issue_id: owner.finite_lead_issue_id,
      lead_run_id: runCtx.runId, stage_key: stage.stage_key,
      source_issue_id: source.id, source_version: source.statusVersion,
      review_issue_id: review.id, review_version: review.statusVersion,
      source_instruction_sha256: sourceInstructionSha256,
      review_instruction_sha256: reviewInstructionSha256,
      predecessor_operation_key: predecessor.operation_key,
      predecessor_source_status: predecessor.source_status,
      predecessor_source_version: predecessor.source_version,
      predecessor_review_status: predecessor.review_status,
      predecessor_review_version: predecessor.review_version,
      predecessor_source_issue_id: predecessor.source_issue_id,
      predecessor_review_issue_id: predecessor.review_issue_id,
      predecessor_dependency_ready: predecessor.dependency_ready,
      ...(attempt === 2 ? {
        attempt: 2,
        retry_of_operation_key: params.retry_of_operation_key,
        retry_of_source_status: retryValidation!.source_status,
        retry_of_source_version: retryValidation!.source_version,
        retry_of_review_status: retryValidation!.review_status,
        retry_of_review_version: retryValidation!.review_version,
        retry_of_dependency_ready: retryValidation!.dependency_ready,
      } : {}),
    }, owner.configPath, owner.python_executable);
    if (typeof result.stage_binding_key !== "string" || !KEY_RE.test(result.stage_binding_key)
        || typeof result.base_sha !== "string" || !SHA_RE.test(result.base_sha)) fail("bridge_result_invalid");
    if (result.state !== "bound" && result.state !== "active") fail("source_stage_activation_reconciliation_required");
    const note = sourceStageBoundNote(
      stage.stage_key, result.stage_binding_key, result.base_sha, source.id, review.id,
      sourceInstructionSha256, reviewInstructionSha256,
      attempt, attempt === 2 ? params.retry_of_operation_key as string : null,
    );
    await persistSourceStageBoundNotes(
      ctx, owner, note, source.id, review.id, sourceInstructionSha256, reviewInstructionSha256, true,
    );
    if (result.state === "bound") await activateSourceStage(ctx, invoke, owner, owner.configPath, result.stage_binding_key, {
      id: source.id, status: source.status, statusVersion: source.statusVersion as number,
    });
    return { content: `stage_key:${stage.stage_key}\nattempt:${attempt}\nstage_binding_key:${result.stage_binding_key}\nbase_sha:${result.base_sha}\nstate:active` };
  });

  ctx.tools.register("deliver_source_stage_content", {
    displayName: "Deliver Finite Brain Source Content",
    description: "Deliver complete bounded contents for the owner-configured paths of the immutable source stage binding.",
    parametersSchema: { type: "object", properties: {
      stage_key: { type: "string" }, stage_binding_key: { type: "string" }, source_issue_id: { type: "string" },
      files: { type: "array", items: { type: "object", properties: {
        path_id: { type: "string" }, content: { type: "string" }, size: { type: "integer" }, sha256: { type: "string" },
      }, required: ["path_id", "content", "size", "sha256"], additionalProperties: false } },
    }, required: ["stage_key", "stage_binding_key", "source_issue_id", "files"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("tool_parameters_invalid");
    const params = raw as Record<string, unknown>;
    if (typeof params.stage_key !== "string" || typeof params.stage_binding_key !== "string"
        || typeof params.source_issue_id !== "string" || !ID_RE.test(params.source_issue_id)
        || !KEY_RE.test(params.stage_binding_key) || !Array.isArray(params.files)
        || Buffer.byteLength(JSON.stringify(params.files), "utf8") > 1_600_000) fail("source_stage_delivery_invalid");
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    const stage = requireSourceStage(owner, params.stage_key);
    if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id
        || runCtx.agentId !== owner.worker_agent_id) fail("worker_actor_binding_mismatch");
    await assertStageCurrentRun(ctx, runCtx, params.source_issue_id);
    const sourceIssue = await readNativeIssue(ctx, params.source_issue_id, owner.company_id);
    if (sourceIssue.parentId !== owner.finite_lead_issue_id) fail("source_stage_source_parent_mismatch");
    const sourceInstructionSha256 = await assertBoundInstruction(
      invoke, owner, stage.stage_key, "source_issue_id", sourceIssue, params.stage_binding_key,
    );
    await assertPinnedSourceWorkspace(ctx, owner, [owner.finite_lead_issue_id!, sourceIssue.id]);
    const result = await invoke("deliver_source_stage_content", {
      company_id: owner.company_id, project_id: owner.project_id, worker_agent_id: owner.worker_agent_id,
      run_id: runCtx.runId, source_issue_id: params.source_issue_id, stage_key: stage.stage_key,
      stage_binding_key: params.stage_binding_key, source_instruction_sha256: sourceInstructionSha256,
      files: params.files,
    }, owner.configPath, owner.python_executable);
    if (result.status === "preflight_failed" || result.status === "preflight_unknown") {
      if (typeof result.content_check_id !== "string" || !KEY_RE.test(result.content_check_id)
          || typeof result.snapshot_sha256 !== "string" || !KEY_RE.test(result.snapshot_sha256)
          || typeof result.policy_digest !== "string" || !KEY_RE.test(result.policy_digest)
          || "operation_key" in result || "candidate_sha" in result) fail("bridge_result_invalid");
      if (result.status === "preflight_unknown" && result.reconcile_required !== true) fail("bridge_result_invalid");
      if (result.status === "preflight_failed"
          && (result.failure_code !== "sandboxed_verifier_failed"
            || typeof result.command_index !== "number" || !Number.isSafeInteger(result.command_index) || result.command_index < 0
            || typeof result.returncode !== "number" || !Number.isSafeInteger(result.returncode)
            || typeof result.timed_out !== "boolean" || typeof result.output_truncated !== "boolean"
            || typeof result.stderr !== "string" || typeof result.stdout !== "string"
            || Buffer.byteLength(result.stderr, "utf8") > 16_000 || Buffer.byteLength(result.stdout, "utf8") > 16_000
            || typeof result.stdout_sha256 !== "string" || !KEY_RE.test(result.stdout_sha256)
            || typeof result.stderr_sha256 !== "string" || !KEY_RE.test(result.stderr_sha256)
            || typeof result.sandbox_profile_sha256 !== "string" || !KEY_RE.test(result.sandbox_profile_sha256))) {
        fail("bridge_result_invalid");
      }
      return { content: JSON.stringify(result, null, 2) };
    }
    if (typeof result.operation_key !== "string" || !KEY_RE.test(result.operation_key)
        || typeof result.candidate_sha !== "string" || !SHA_RE.test(result.candidate_sha)) fail("bridge_result_invalid");
    if (typeof result.preflight_check_id !== "string" || !KEY_RE.test(result.preflight_check_id)) fail("bridge_result_invalid");
    const note = [
      `operation_key:${result.operation_key}`,
      `brain-autonomous-bridge:${result.operation_key}:submitted`,
      `stage_key:${stage.stage_key}`,
      `stage_binding_key:${params.stage_binding_key}`,
      `preflight_check_id:${String(result.preflight_check_id ?? "")}`,
      `candidate_sha:${result.candidate_sha}`,
      `base_sha:${String(result.base_sha ?? "")}`,
      `submitted_sha:${String(result.submitted_sha ?? "controller-generated")}`,
      `policy_digest:${String(result.policy_digest ?? "")}`,
    ].join("\n");
    await persistIssueNote(
      ctx, sourceIssue.id, owner.company_id, `${result.operation_key}:submitted`, note,
    );
    await persistIssueNote(
      ctx, owner.finite_lead_issue_id!, owner.company_id, `${result.operation_key}:submitted`, note,
    );
    return { content: `operation_key:${result.operation_key}\ncandidate_sha:${result.candidate_sha}` };
  });

  ctx.tools.register("deliver_source_content", {
    displayName: "Deliver Brain Source Content",
    description: "Deliver complete bounded contents for the owner-defined source paths for controller-side candidate preparation and independent review.",
    parametersSchema: { type: "object", properties: { base_sha: { type: "string" }, files: { type: "array", items: { type: "object", properties: { path_id: { type: "string" }, content: { type: "string" }, size: { type: "integer" }, sha256: { type: "string" } }, required: ["path_id", "content", "size", "sha256"], additionalProperties: false } } }, required: ["base_sha", "files"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    const params = validateParams("deliver_source_content", raw);
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    if (!owner.source_issue_id || !owner.reviewer_issue_id) fail("legacy_source_route_not_owner_configured");
    if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id || runCtx.agentId !== owner.worker_agent_id) fail("worker_actor_binding_mismatch");
    await assertRunBoundIssue(ctx, runCtx, owner.source_issue_id);
    await validateReviewerWakeTarget(ctx, owner);
    const result = await invoke("deliver_source_content", {
      company_id: owner.company_id,
      project_id: owner.project_id,
      worker_agent_id: owner.worker_agent_id,
      run_id: runCtx.runId,
      source_issue_id: owner.source_issue_id,
      base_sha: params.base_sha,
      files: params.files,
    }, owner.configPath, owner.python_executable);
    if (typeof result.operation_key !== "string" || !KEY_RE.test(result.operation_key)
        || typeof result.candidate_sha !== "string" || !SHA_RE.test(result.candidate_sha)) fail("bridge_result_invalid");
    const note = [
      `operation_key:${result.operation_key}`,
      `brain-autonomous-bridge:${result.operation_key}:submitted`,
      `candidate_sha:${result.candidate_sha}`,
      `base_sha:${String(result.base_sha ?? params.base_sha)}`,
      `submitted_sha:${String(result.submitted_sha ?? "controller-generated")}`,
      `policy_digest:${String(result.policy_digest ?? "")}`,
    ].join("\n");
    await persistIssueNote(ctx, owner.reviewer_issue_id, owner.company_id, `${result.operation_key}:submitted`, note);
    await validateReviewerWakeTarget(ctx, owner);
    await ctx.issues.forCurrentToolRun().update(owner.reviewer_issue_id, "todo", owner.company_id);
    return { content: `operation_key:${result.operation_key}\ncandidate_sha:${result.candidate_sha}` };
  });

  ctx.tools.register("inspect_source_stage_candidate", {
    displayName: "Inspect Finite Brain Source Candidate",
    description: "Inspect the candidate derived from this immutable source stage binding.",
    parametersSchema: { type: "object", properties: {
      stage_key: { type: "string" }, stage_binding_key: { type: "string" }, review_issue_id: { type: "string" },
    }, required: ["stage_key", "stage_binding_key", "review_issue_id"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("tool_parameters_invalid");
    const params = raw as Record<string, unknown>;
    if (typeof params.stage_key !== "string" || !ID_RE.test(params.stage_key)
        || typeof params.review_issue_id !== "string" || !ID_RE.test(params.review_issue_id)
        || typeof params.stage_binding_key !== "string" || !KEY_RE.test(params.stage_binding_key)) fail("source_stage_review_parameters_invalid");
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    const { stage, review } = await validateStageReviewRun(ctx, runCtx, owner, params.stage_key, params.review_issue_id);
    const reviewInstructionSha256 = await assertBoundInstruction(
      invoke, owner, stage.stage_key, "review_issue_id", review, params.stage_binding_key,
    );
    await assertPinnedSourceWorkspace(ctx, owner, [owner.finite_lead_issue_id!, params.review_issue_id]);
    const result = await invoke("inspect_bound_source_stage", {
      company_id: owner.company_id, project_id: owner.project_id, reviewer_agent_id: owner.reviewer_agent_id,
      reviewer_run_id: runCtx.runId, reviewer_issue_id: params.review_issue_id,
      stage_key: stage.stage_key, stage_binding_key: params.stage_binding_key,
      review_instruction_sha256: reviewInstructionSha256,
    }, owner.configPath, owner.python_executable);
    if (typeof result.operation_key !== "string" || !KEY_RE.test(result.operation_key)
        || typeof result.candidate_sha !== "string" || !SHA_RE.test(result.candidate_sha)
        || typeof result.diff !== "string" || Buffer.byteLength(result.diff, "utf8") > 60_000
        || typeof result.inspection_digest !== "string" || !KEY_RE.test(result.inspection_digest)) fail("bridge_result_invalid");
    const metadata = { ...result }; delete metadata.diff;
    const canonicalDiff = result.canonical_diff;
    if (owner.canonical_repo && (typeof result.canonical_candidate_sha !== "string"
        || typeof result.canonical_receipt_digest !== "string" || typeof canonicalDiff !== "string"
        || Buffer.byteLength(canonicalDiff, "utf8") > 60_000)) fail("bridge_result_invalid");
    delete metadata.canonical_diff;
    return { content: `${JSON.stringify(metadata, null, 2)}\n\n--- Projected candidate diff ---\n${result.diff}`
      + (typeof canonicalDiff === "string" ? `\n\n--- Canonical candidate diff ---\n${canonicalDiff}` : "") };
  });

  ctx.tools.register("approve_source_stage_candidate", {
    displayName: "Approve Finite Brain Source Candidate",
    description: "Record independent evidence for the candidate bound to this finite source stage.",
    parametersSchema: { type: "object", properties: {
      stage_key: { type: "string" }, stage_binding_key: { type: "string" }, review_issue_id: { type: "string" },
      approved: { type: "boolean" }, evidence: { type: "object", properties: {
        inspection_digest: { type: "string" }, canonical_candidate_sha: { type: "string" }, canonical_receipt_digest: { type: "string" },
        rationale: { type: "string", maxLength: 4000 },
      }, required: ["inspection_digest", "rationale"], additionalProperties: true },
    }, required: ["stage_key", "stage_binding_key", "review_issue_id", "approved", "evidence"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("tool_parameters_invalid");
    const params = raw as Record<string, unknown>;
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    if (typeof params.stage_key !== "string" || !ID_RE.test(params.stage_key)
        || typeof params.review_issue_id !== "string" || !ID_RE.test(params.review_issue_id)
        || typeof params.stage_binding_key !== "string" || !KEY_RE.test(params.stage_binding_key)
        || typeof params.approved !== "boolean" || !params.evidence || typeof params.evidence !== "object" || Array.isArray(params.evidence)
        || typeof (params.evidence as Record<string, unknown>).inspection_digest !== "string"
        || !KEY_RE.test((params.evidence as Record<string, string>).inspection_digest)
        || typeof (params.evidence as Record<string, unknown>).rationale !== "string"
        || Buffer.byteLength((params.evidence as Record<string, string>).rationale, "utf8") > 4_000
        || (owner.canonical_repo !== undefined && (
          typeof (params.evidence as Record<string, unknown>).canonical_candidate_sha !== "string"
          || !SHA_RE.test((params.evidence as Record<string, string>).canonical_candidate_sha)
          || typeof (params.evidence as Record<string, unknown>).canonical_receipt_digest !== "string"
          || !KEY_RE.test((params.evidence as Record<string, string>).canonical_receipt_digest)
        ))
        || Buffer.byteLength(JSON.stringify(params.evidence), "utf8") > 16_000) fail("source_stage_review_parameters_invalid");
    const { review } = await validateStageReviewRun(ctx, runCtx, owner, params.stage_key, params.review_issue_id);
    const reviewInstructionSha256 = await assertBoundInstruction(
      invoke, owner, params.stage_key, "review_issue_id", review, params.stage_binding_key,
    );
    await assertPinnedSourceWorkspace(ctx, owner, [owner.finite_lead_issue_id!, params.review_issue_id]);
    const result = await invoke("approve_bound_source_stage", {
      company_id: owner.company_id, project_id: owner.project_id, reviewer_agent_id: owner.reviewer_agent_id,
      reviewer_run_id: runCtx.runId, reviewer_issue_id: params.review_issue_id,
      stage_key: params.stage_key, stage_binding_key: params.stage_binding_key,
      approved: params.approved, evidence: params.evidence,
      review_instruction_sha256: reviewInstructionSha256,
    }, owner.configPath, owner.python_executable);
    if (typeof result.status !== "string" || typeof result.operation_key !== "string" || !KEY_RE.test(result.operation_key)
        || typeof result.candidate_sha !== "string" || !SHA_RE.test(result.candidate_sha)
        || typeof result.evidence_digest !== "string" || !KEY_RE.test(result.evidence_digest)) fail("bridge_result_invalid");
    const evidence = params.evidence as Record<string, unknown>;
    if (owner.canonical_repo && result.status !== "review_rejected" && (typeof result.projection_status !== "string"
        || typeof result.canonical_status !== "string"
        || typeof result.canonical_candidate_sha !== "string" || !SHA_RE.test(result.canonical_candidate_sha)
        || typeof result.canonical_receipt_digest !== "string" || !KEY_RE.test(result.canonical_receipt_digest))) {
      fail("bridge_result_invalid");
    }
    const idempotencyKey = `${result.operation_key}:stage-reviewed`;
    const noteFields = [
      `brain-autonomous-bridge:${idempotencyKey}`,
      `brain-source-stage:${params.stage_binding_key}:reviewed`, `status:${result.status}`,
      `candidate_sha:${result.candidate_sha}`, `evidence_digest:${result.evidence_digest}`,
      `review_rationale:${JSON.stringify(evidence.rationale)}`,
    ];
    if (typeof result.projection_status === "string") noteFields.push(`projection_status:${result.projection_status}`);
    if (typeof result.canonical_status === "string") noteFields.push(`canonical_status:${result.canonical_status}`);
    const canonicalCandidateSha = typeof result.canonical_candidate_sha === "string"
      ? result.canonical_candidate_sha : evidence.canonical_candidate_sha;
    const canonicalReceiptDigest = typeof result.canonical_receipt_digest === "string"
      ? result.canonical_receipt_digest : evidence.canonical_receipt_digest;
    if (typeof canonicalCandidateSha === "string") noteFields.push(`canonical_candidate_sha:${canonicalCandidateSha}`);
    if (typeof canonicalReceiptDigest === "string") noteFields.push(`canonical_receipt_digest:${canonicalReceiptDigest}`);
    if (owner.canonical_repo && result.status === "review_rejected") noteFields.push("canonical_publication:not_attempted_review_rejected");
    const note = noteFields.join("\n");
    await persistIssueNote(ctx, review.id, owner.company_id, idempotencyKey, note);
    await persistIssueNote(ctx, owner.finite_lead_issue_id!, owner.company_id, idempotencyKey, note);
    return { content: note };
  });

  ctx.tools.register("inspect_candidate", {
    displayName: "Inspect Brain Candidate",
    description: "Read the complete bounded diff for the exact prepared candidate in this reviewer run before deciding.",
    parametersSchema: { type: "object", properties: { operation_key: { type: "string" }, candidate_sha: { type: "string" } }, required: ["operation_key", "candidate_sha"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    const params = validateParams("inspect_candidate", raw);
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    if (!owner.reviewer_issue_id) fail("legacy_reviewer_route_not_owner_configured");
    if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id || runCtx.agentId !== owner.reviewer_agent_id) fail("reviewer_actor_binding_mismatch");
    await assertRunBoundIssue(ctx, runCtx, owner.reviewer_issue_id);
    const result = await invoke("inspect_candidate", {
      company_id: owner.company_id,
      project_id: owner.project_id,
      reviewer_agent_id: owner.reviewer_agent_id,
      reviewer_run_id: runCtx.runId,
      reviewer_issue_id: owner.reviewer_issue_id,
      operation_key: params.operation_key,
      candidate_sha: params.candidate_sha,
    }, owner.configPath, owner.python_executable);
    if (result.operation_key !== params.operation_key || result.candidate_sha !== params.candidate_sha
        || typeof result.diff !== "string" || Buffer.byteLength(result.diff, "utf8") > 60_000
        || typeof result.inspection_digest !== "string" || !KEY_RE.test(result.inspection_digest)) fail("bridge_result_invalid");
    const metadata = { ...result };
    delete metadata.diff;
    return { content: `${JSON.stringify(metadata, null, 2)}\n\n--- Exact candidate diff ---\n${result.diff}` };
  });

  ctx.tools.register("approve_candidate", {
    displayName: "Approve Brain Candidate",
    description: "After inspecting the exact candidate in this reviewer run, record your evidence and decision; approval runs the fixed verifier and local GitGate CAS.",
    parametersSchema: { type: "object", properties: { operation_key: { type: "string" }, candidate_sha: { type: "string" }, approved: { type: "boolean" }, evidence: { type: "object", properties: { inspection_digest: { type: "string" } }, required: ["inspection_digest"], additionalProperties: true } }, required: ["operation_key", "candidate_sha", "approved", "evidence"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    const params = validateParams("approve_candidate", raw);
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    if (!owner.reviewer_issue_id || !owner.source_issue_id) fail("legacy_reviewer_route_not_owner_configured");
    if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id || runCtx.agentId !== owner.reviewer_agent_id) fail("reviewer_actor_binding_mismatch");
    await assertRunBoundIssue(ctx, runCtx, owner.reviewer_issue_id);
    const result = await invoke("approve_candidate", {
      company_id: owner.company_id,
      project_id: owner.project_id,
      reviewer_agent_id: owner.reviewer_agent_id,
      reviewer_run_id: runCtx.runId,
      reviewer_issue_id: owner.reviewer_issue_id,
      operation_key: params.operation_key,
      candidate_sha: params.candidate_sha,
      approved: params.approved,
      evidence: params.evidence,
    }, owner.configPath, owner.python_executable);
    if (typeof result.status !== "string") fail("bridge_result_invalid");
    if (typeof result.evidence_digest !== "string" || !KEY_RE.test(result.evidence_digest)) fail("bridge_result_invalid");
    const note = [
      `brain-autonomous-bridge:${params.operation_key}:reviewed`,
      `candidate_sha:${String(result.candidate_sha ?? params.candidate_sha)}`,
      `status:${result.status}`,
      `reviewer_agent_id:${owner.reviewer_agent_id}`,
      `reviewer_run_id:${runCtx.runId}`,
      `evidence_digest:${result.evidence_digest}`,
      `evidence:${JSON.stringify(params.evidence)}`,
    ].join("\n");
    await persistIssueNote(ctx, owner.source_issue_id, owner.company_id, `${params.operation_key}:reviewed`, note);
    return { content: `status:${result.status}\ncandidate_sha:${String(result.candidate_sha ?? params.candidate_sha)}` };
  });
}

const plugin = definePlugin({
  async setup(ctx) {
    registerAutonomousTools(ctx, privateBridgeInvoker());
  },
  async onHealth() {
    return { status: "ok", message: "Owner-configured bridge worker is ready" };
  },
});

export { manifest };
export default plugin;
runWorker(plugin, import.meta.url);
