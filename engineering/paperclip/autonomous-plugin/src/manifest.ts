import type { PaperclipPluginManifestV1 } from "@paperclipai/plugin-sdk";

const manifest: PaperclipPluginManifestV1 = {
  id: "brain.autonomous-bridge",
  apiVersion: 1,
  version: "0.1.0",
  displayName: "Brain Autonomous Bridge",
  description: "Bounded candidate submission and independent-review handoff for one owner-pinned Brain task pair.",
  author: "Brain Engineering",
  categories: ["automation"],
  capabilities: ["agent.tools.register", "issues.read", "issues.orchestration.read", "issues.update", "issue.comments.read", "issue.comments.create", "execution.workspaces.read"],
  instanceConfigSchema: {
    type: "object",
    properties: {
      ownerConfigPath: {
        type: "string",
        title: "Owner Config Path",
        description: "Absolute path to the owner-only bridge configuration file.",
        readOnly: true,
      },
    },
    required: ["ownerConfigPath"],
    additionalProperties: false,
  },
  entrypoints: { worker: "./dist/worker.js" },
  tools: [
    {
      name: "bind_source_stage",
      displayName: "Bind Finite Brain Source Stage",
      description: "Bind the owner-admitted native source and review tasks to one finite content stage.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, source_issue_id: { type: "string" }, review_issue_id: { type: "string" },
        attempt: { type: "integer", minimum: 1, maximum: 3 },
        retry_of_operation_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
        predecessor: { type: "object", properties: {
          operation_key: { type: "string" }, source_issue_id: { type: "string" }, review_issue_id: { type: "string" },
        }, required: ["operation_key", "source_issue_id", "review_issue_id"], additionalProperties: false },
      }, required: ["stage_key", "source_issue_id", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "deliver_source_stage_content",
      displayName: "Deliver Finite Brain Source Content",
      description: "Deliver complete bounded contents for the owner-configured paths of the immutable source stage binding.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string" }, source_issue_id: { type: "string" },
        files: { type: "array", items: { type: "object", properties: {
          path_id: { type: "string" }, content: { type: "string" }, size: { type: "integer" }, sha256: { type: "string" },
        }, required: ["path_id", "content", "size", "sha256"], additionalProperties: false } },
      }, required: ["stage_key", "stage_binding_key", "source_issue_id", "files"], additionalProperties: false },
    },
    {
      name: "inspect_source_stage_candidate",
      displayName: "Inspect Finite Brain Source Candidate",
      description: "Inspect the projected and canonical candidates derived from this immutable source stage binding.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string" }, review_issue_id: { type: "string" },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "approve_source_stage_candidate",
      displayName: "Approve Finite Brain Source Candidate",
      description: "Approve only the exact inspected projected and canonical candidate identities.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string" }, review_issue_id: { type: "string" },
        approved: { type: "boolean" }, evidence: { type: "object", properties: {
          inspection_digest: { type: "string" }, canonical_candidate_sha: { type: "string" },
          canonical_receipt_digest: { type: "string" },
          rationale: { type: "string", maxLength: 4000 },
        }, required: ["inspection_digest", "rationale"], additionalProperties: true },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id", "approved", "evidence"], additionalProperties: false },
    },
    {
      name: "inspect_candidate",
      displayName: "Inspect Brain Candidate",
      description: "Return the complete bounded diff and exact identity of a prepared candidate; must be called in this reviewer run before approval.",
      parametersSchema: {
        type: "object",
        properties: {
          operation_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
          candidate_sha: { type: "string", pattern: "^[0-9a-f]{40,64}$" },
        },
        required: ["operation_key", "candidate_sha"],
        additionalProperties: false,
      },
    },
    {
      name: "deliver_source_content",
      displayName: "Deliver Brain Source Content",
      description: "Bind the current worker run to its configured Paperclip issue and deliver complete bounded contents for owner-defined paths for controller-side preparation.",
      parametersSchema: {
        type: "object",
        properties: {
          base_sha: { type: "string", minLength: 40, maxLength: 64 },
          files: {
            type: "array",
            items: {
              type: "object",
              properties: {
                path_id: { type: "string" },
                content: { type: "string" },
                size: { type: "integer", minimum: 0, maximum: 1000000 },
                sha256: { type: "string", pattern: "^[0-9a-f]{64}$" },
              },
              required: ["path_id", "content", "size", "sha256"],
              additionalProperties: false,
            },
          },
        },
        required: ["base_sha", "files"],
        additionalProperties: false,
      },
    },
    {
      name: "approve_candidate",
      displayName: "Approve Brain Candidate",
      description: "Bind an independent reviewer run to its configured Paperclip issue and record its decision on the exact prepared candidate.",
      parametersSchema: {
        type: "object",
        properties: {
          operation_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
          candidate_sha: { type: "string", pattern: "^[0-9a-f]{40,64}$" },
          approved: { type: "boolean" },
          evidence: {
            type: "object",
            properties: { inspection_digest: { type: "string", pattern: "^[0-9a-f]{64}$" } },
            required: ["inspection_digest"],
            additionalProperties: true,
          },
        },
        required: ["operation_key", "candidate_sha", "approved", "evidence"],
        additionalProperties: false,
      },
    },
  ],
};

export default manifest;
