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
      name: "get_program_source_scope",
      displayName: "Get Program Source Scope",
      description: "Return the immutable owner-defined department paths and compatible fixed verifiers for the current Astra-created Sol program.",
      parametersSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      name: "validate_program_source_proposal",
      displayName: "Validate Program Source Proposal",
      description: "Check a proposed normalized path subset against the owner program envelope and compatible fixed verifier registry.",
      parametersSchema: { type: "object", properties: {
        profile_id: { type: "string" }, path_ids: { type: "array", minItems: 2, maxItems: 32, items: { type: "string", maxLength: 1024 } },
      }, required: ["profile_id", "path_ids"], additionalProperties: false },
    },
    {
      name: "bind_program_source_stage",
      displayName: "Bind Proposed Program Source Stage",
      description: "Bind Sol-created Luna and independent QA children to a compatible proposed path subset and the current accepted publication ACK.",
      parametersSchema: { type: "object", properties: {
        profile_id: { type: "string" }, corpus_id: { type: "string" },
        path_ids: { type: "array", minItems: 2, maxItems: 32, items: { type: "string", maxLength: 1024 } },
        source_issue_id: { type: "string" }, review_issue_id: { type: "string" },
      }, required: ["profile_id", "corpus_id", "path_ids", "source_issue_id", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "deliver_program_source_content",
      displayName: "Deliver Proposed Program Source Content",
      description: "Deliver the complete bounded snapshot for the dynamic stage bound to the current Luna run.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
        files: { type: "array", minItems: 2, maxItems: 32, items: { type: "object", properties: {
          path_id: { type: "string" }, content: { type: "string" }, size: { type: "integer" },
          sha256: { type: "string", pattern: "^[0-9a-f]{64}$" },
        }, required: ["path_id", "content", "size", "sha256"], additionalProperties: false } },
      }, required: ["stage_key", "stage_binding_key", "files"], additionalProperties: false },
    },
    {
      name: "verify_program_source_unchanged",
      displayName: "Verify Proposed Program Accepted Base",
      description: "Run the registered fixed verifier against the unchanged accepted base without creating or publishing a candidate.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
      }, required: ["stage_key", "stage_binding_key"], additionalProperties: false },
    },
    {
      name: "inspect_program_source_candidate",
      displayName: "Inspect Proposed Program Candidate",
      description: "Inspect the exact dynamic candidate and bounded diff in the independent QA run.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" }, review_issue_id: { type: "string" },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "approve_program_source_candidate",
      displayName: "Record Proposed Program Candidate Review",
      description: "Record independent candidate merits and invoke the fixed GitGate publication path.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
        review_issue_id: { type: "string" }, approved: { type: "boolean" },
        evidence: { type: "object", properties: {
          inspection_digest: { type: "string", pattern: "^[0-9a-f]{64}$" }, rationale: { type: "string", minLength: 1, maxLength: 4000 },
        }, required: ["inspection_digest", "rationale"], additionalProperties: false },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id", "approved", "evidence"], additionalProperties: false },
    },
    {
      name: "inspect_program_source_no_change",
      displayName: "Inspect Proposed Program No Change",
      description: "Inspect the exact accepted-base verification receipt and current accepted tip before product-merit review.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" }, review_issue_id: { type: "string" },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "record_program_source_no_change_merits",
      displayName: "Record Proposed Program No Change Merits",
      description: "Record independent product merits for the verified accepted base without candidate creation or publication.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" },
        review_issue_id: { type: "string" }, merits: { type: "boolean" },
        evidence: { type: "object", properties: {
          inspection_digest: { type: "string", pattern: "^[0-9a-f]{64}$" }, rationale: { type: "string", minLength: 1, maxLength: 4000 },
        }, required: ["inspection_digest", "rationale"], additionalProperties: false },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id", "merits", "evidence"], additionalProperties: false },
    },
    {
      name: "list_accepted_source",
      displayName: "List Accepted Source",
      description: "List a bounded page of paths in the owner-pinned sanitized accepted-source projection.",
      parametersSchema: { type: "object", properties: {
        corpus_id: { type: "string" }, prefix: { type: "string", maxLength: 1024 },
        cursor: { type: ["string", "null"], maxLength: 4096 }, limit: { type: "integer", minimum: 1, maximum: 50 },
      }, required: ["corpus_id", "prefix", "cursor", "limit"], additionalProperties: false },
    },
    {
      name: "search_accepted_source",
      displayName: "Search Accepted Source",
      description: "Search bounded literal text in the owner-pinned sanitized accepted-source projection.",
      parametersSchema: { type: "object", properties: {
        corpus_id: { type: "string" }, query: { type: "string", minLength: 1, maxLength: 512 },
        prefix: { type: "string", maxLength: 1024 }, cursor: { type: ["string", "null"], maxLength: 4096 },
        limit: { type: "integer", minimum: 1, maximum: 50 },
      }, required: ["corpus_id", "query", "prefix", "cursor", "limit"], additionalProperties: false },
    },
    {
      name: "read_accepted_source",
      displayName: "Read Accepted Source",
      description: "Read a bounded line excerpt from one file in the owner-pinned sanitized accepted-source projection.",
      parametersSchema: { type: "object", properties: {
        corpus_id: { type: "string" }, path: { type: "string", maxLength: 1024 },
        start_line: { type: "integer", minimum: 1 }, line_count: { type: "integer", minimum: 1, maximum: 80 },
      }, required: ["corpus_id", "path", "start_line", "line_count"], additionalProperties: false },
    },
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
      name: "verify_source_stage_unchanged",
      displayName: "Verify Accepted Base Without Change",
      description: "Run the registered fixed verifier against the unchanged accepted base with no candidate or publication.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" }, source_issue_id: { type: "string" },
      }, required: ["stage_key", "stage_binding_key", "source_issue_id"], additionalProperties: false },
    },
    {
      name: "inspect_source_stage_no_change",
      displayName: "Inspect Accepted Base Assessment",
      description: "Inspect an accepted-base verifier receipt and current accepted tip before independent merit review.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" }, review_issue_id: { type: "string" },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "record_source_stage_no_change_merits",
      displayName: "Record No Change Product Merits",
      description: "Record independent product merits without approving or publishing a content candidate.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string", pattern: "^[0-9a-f]{64}$" }, review_issue_id: { type: "string" },
        merits: { type: "boolean" }, evidence: { type: "object", properties: {
          inspection_digest: { type: "string", pattern: "^[0-9a-f]{64}$" }, rationale: { type: "string", minLength: 1, maxLength: 4000 },
        }, required: ["inspection_digest", "rationale"], additionalProperties: false },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id", "merits", "evidence"], additionalProperties: false },
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
      description: "Inspect the exact passed source-preflight receipt and candidate. The final fixed verifier is pending independent QA.",
      parametersSchema: { type: "object", properties: {
        stage_key: { type: "string" }, stage_binding_key: { type: "string" }, review_issue_id: { type: "string" },
      }, required: ["stage_key", "stage_binding_key", "review_issue_id"], additionalProperties: false },
    },
    {
      name: "approve_source_stage_candidate",
      displayName: "Approve Finite Brain Source Candidate",
      description: "Record independent evidence. Approval runs the mandatory final fixed verifier before projected or canonical publication; rejection does not publish.",
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
