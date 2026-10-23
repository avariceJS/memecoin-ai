import type { PaperclipPluginManifestV1 } from "@paperclipai/plugin-sdk";

const manifest: PaperclipPluginManifestV1 = {
  id: "brain.autonomous-bridge",
  apiVersion: 1,
  version: "0.1.0",
  displayName: "Brain Autonomous Bridge",
  description: "Bounded candidate submission and independent-review handoff for one owner-pinned Brain task pair.",
  author: "Brain Engineering",
  categories: ["automation"],
  capabilities: ["agent.tools.register", "issues.read", "issues.orchestration.read", "issues.update", "issue.comments.read", "issue.comments.create"],
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
      name: "submit_candidate",
      displayName: "Submit Brain Candidate",
      description: "Bind the current worker run to its configured Paperclip issue and prepare its exact Git candidate for independent review.",
      parametersSchema: {
        type: "object",
        properties: {
          base_sha: { type: "string", minLength: 40, maxLength: 64 },
          submitted_sha: { type: "string", minLength: 40, maxLength: 64 },
        },
        required: ["base_sha", "submitted_sha"],
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
