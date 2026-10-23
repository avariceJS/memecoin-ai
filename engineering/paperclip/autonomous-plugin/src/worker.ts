import { spawn } from "node:child_process";
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

type ToolAction = "submit_candidate" | "inspect_candidate" | "approve_candidate";
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
  source_issue_id: string;
  reviewer_issue_id: string;
  python_executable: string;
  configPath: string;
};

function fail(code: string): never {
  throw new Error(code);
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
  const boundRun = orchestration.runs.find((run) => run.id === runCtx.runId);
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
    const keys = ["company_id", "project_id", "worker_agent_id", "reviewer_agent_id", "source_issue_id", "reviewer_issue_id"] as const;
    if (parsed.schema !== CONFIG_SCHEMA || keys.some((key) => typeof parsed[key] !== "string" || !ID_RE.test(parsed[key] as string))
        || typeof parsed.python_executable !== "string" || !isAbsolute(parsed.python_executable)) {
      fail("owner_bridge_config_invalid");
    }
    return {
      ...Object.fromEntries(keys.map((key) => [key, parsed[key]])),
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
  const keys = action === "submit_candidate" ? ["base_sha", "submitted_sha"]
    : action === "inspect_candidate" ? ["operation_key", "candidate_sha"]
      : ["operation_key", "candidate_sha", "approved", "evidence"];
  if (Object.keys(params).some((key) => !keys.includes(key)) || keys.some((key) => !(key in params))) fail("tool_parameters_invalid");
  if (action === "submit_candidate") {
    if (typeof params.base_sha !== "string" || !SHA_RE.test(params.base_sha)
        || typeof params.submitted_sha !== "string" || !SHA_RE.test(params.submitted_sha)) fail("candidate_identity_invalid");
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
  const existing = comments.filter((comment) => comment.body.includes(marker));
  if (existing.some((comment) => comment.body !== body)) fail("paperclip_evidence_replay_mismatch");
  if (existing.length === 0) {
    await scopedIssues.createComment(issueId, body, companyId);
  }
}

async function validateReviewerWakeTarget(
  ctx: Pick<PluginContext, "issues">,
  owner: OwnerConfig,
): Promise<void> {
  const scopedIssues = ctx.issues.forCurrentToolRun();
  const [issue, orchestration] = await Promise.all([
    scopedIssues.get(owner.reviewer_issue_id, owner.company_id),
    scopedIssues.getOrchestration({ issueId: owner.reviewer_issue_id, companyId: owner.company_id }),
  ]);
  if (!issue || issue.id !== owner.reviewer_issue_id || issue.companyId !== owner.company_id
      || issue.projectId !== owner.project_id || issue.assigneeAgentId !== owner.reviewer_agent_id
      || issue.parentId !== owner.source_issue_id || !["backlog", "todo"].includes(issue.status)
      || issue.executionRunId !== null || orchestration.blockedByIssueIds.length !== 1
      || orchestration.blockedByIssueIds[0] !== owner.source_issue_id) {
    fail("reviewer_issue_wake_target_mismatch");
  }
}

export function registerAutonomousTools(ctx: PluginContext, invoke: BridgeInvoker): void {
  ctx.tools.register("submit_candidate", {
    displayName: "Submit Brain Candidate",
    description: "Submit this exact worker checkout commit for owner-pinned verification and independent review.",
    parametersSchema: { type: "object", properties: { base_sha: { type: "string" }, submitted_sha: { type: "string" } }, required: ["base_sha", "submitted_sha"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    const params = validateParams("submit_candidate", raw);
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
    if (runCtx.companyId !== owner.company_id || runCtx.projectId !== owner.project_id || runCtx.agentId !== owner.worker_agent_id) fail("worker_actor_binding_mismatch");
    await assertRunBoundIssue(ctx, runCtx, owner.source_issue_id);
    await validateReviewerWakeTarget(ctx, owner);
    const result = await invoke("submit_candidate", {
      company_id: owner.company_id,
      project_id: owner.project_id,
      worker_agent_id: owner.worker_agent_id,
      run_id: runCtx.runId,
      source_issue_id: owner.source_issue_id,
      base_sha: params.base_sha,
      submitted_sha: params.submitted_sha,
    }, owner.configPath, owner.python_executable);
    if (typeof result.operation_key !== "string" || !KEY_RE.test(result.operation_key)
        || typeof result.candidate_sha !== "string" || !SHA_RE.test(result.candidate_sha)) fail("bridge_result_invalid");
    const note = [
      `operation_key:${result.operation_key}`,
      `brain-autonomous-bridge:${result.operation_key}:submitted`,
      `candidate_sha:${result.candidate_sha}`,
      `base_sha:${String(result.base_sha ?? params.base_sha)}`,
      `submitted_sha:${String(result.submitted_sha ?? params.submitted_sha)}`,
      `policy_digest:${String(result.policy_digest ?? "")}`,
    ].join("\n");
    await persistIssueNote(ctx, owner.reviewer_issue_id, owner.company_id, `${result.operation_key}:submitted`, note);
    await validateReviewerWakeTarget(ctx, owner);
    await ctx.issues.forCurrentToolRun().update(owner.reviewer_issue_id, "todo", owner.company_id);
    return { content: `operation_key:${result.operation_key}\ncandidate_sha:${result.candidate_sha}` };
  });

  ctx.tools.register("inspect_candidate", {
    displayName: "Inspect Brain Candidate",
    description: "Read the complete bounded diff for the exact prepared candidate in this reviewer run before deciding.",
    parametersSchema: { type: "object", properties: { operation_key: { type: "string" }, candidate_sha: { type: "string" } }, required: ["operation_key", "candidate_sha"], additionalProperties: false },
  }, async (raw, runCtx): Promise<ToolResult> => {
    const params = validateParams("inspect_candidate", raw);
    const owner = await readOwnerConfig(ctx, runCtx.companyId);
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
