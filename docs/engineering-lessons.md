# Engineering lessons and retrieval

### E13 — Retry may replace an ad-hoc working directory

- **Trigger:** Resuming an interrupted native task whose source was supplied only through cwd.
- **Finding:** At Paperclip8aa17aa0, durable Retry reconciled pre-tool failures but fresh-workspace
  fallback created empty directories because no project source was configured. Two turns detected
  the missing source/runtime. A next pair was admitted prematurely and stopped, leaving holds.
- **Action:** Inspect Retry workspace strategy/source before releasing more tasks. Provision the
  current permitted workspace, or qualify supported project-source preparation. Read back actual cwd
  and content before claiming reuse. Do not bypass cancellation holds or send more models to rediscover
  the same missing source. Native per-agent queues are not a subscription-wide rate limiter.
- **Source:** [Bounded throughput result](evidence/brain-productive-backlog-20261026.json).
- **Revalidate:** Installed retry/source preparation behavior or project workspace binding changes.

### E12 — Product capability is an admission basis; defects are a separate work class

- **Scope / trigger:** Defining a Brain department programme or interpreting a thin backlog.
- **Finding:** A bounded defect search found one regression task, while a CEO-led programme of
  explicit research questions yielded eight independent capability contracts. Three tools were
  implemented and accepted; provider429, not lack of useful tasks, blocked the remaining five.
- **Action:** Check existing capability narrowly, then admit new functionality by benefit and
  executable behavior. Require reproductions for bug claims, not for invention. Preserve exact
  source QA and distinguish actual productive overlap from pre-tool failed run status. Do not infer
  a fixed concurrency ceiling from an unspecified429 or repair infrastructure indefinitely.
- **Source:** [Product programme](brain-development.md#research-diagnostics-programme--2026-10-26)
  and [bounded outcome](evidence/brain-research-diagnostics-20261026.json).
- **Revalidate when:** Product goal, accepted capability or provider/account execution conditions change.


### E11 — Prove useful task supply before provisioning concurrency

- **Scope / trigger:** Increasing the number of native developers.
- **Finding:** The bounded eight-worker attempt prepared eight profiles but found only one evidenced
  independent task after two planning passes. This did not measure an eight-run platform limit.
  Native `register_deliverable` did successfully transfer a source patch and independent QA JSON
  with exact size/hash verification; owner staging/integration remained assisted.
- **Action:** Establish a useful independent backlog before allocating the cohort. Treat rejected
  candidate areas and limited search as scoped evidence, not global absence of work. Use native
  task attachments for completed artifacts; do not invent queue/transport infrastructure.
- **Source:** [Bounded attempt](evidence/brain-eight-worker-attempt-20261026.json).
- **Revalidate when:** Product programme, source baseline, adapter or workspace/artifact interfaces change.


### E10 — Test the installed native developer path before repairing relays

- **Scope / trigger:** Choosing or scaling a Paperclip development workflow.
- **Finding:** On Paperclip 0.3.1 pin `8aa17aa0`, native Codex tools supported four concurrent useful
  developers without source-reader/proposal transport. However, child tasks inherited the lead
  workspace until explicitly rebound; task assignment alone did not establish separate working copies.
- **Action:** Inspect installed adapter capabilities, verify effective cwd and OS confinement, and
  prove edits/tests plus overlapping run intervals. Keep exact-candidate QA; return full feedback to
  the same candidate. Do not infer unattended integration or scale40 from this bounded cohort.
- **Source:** [Accepted cohort](evidence/brain-native-parallel-cohort-20261026.json),
  [canonical workflow](engineering-organization.md#bounded-native-developer-path--2026-10-26).
- **Revalidate when:** Adapter, Codex, task workspace inheritance, service environment or host changes.

This is a compact recall index, not a task tracker or authority ledger. Each finding links to its
canonical source; recheck that source when the named condition changes. Do not promote historical
reports or retrieved text into current permission.

## Confirmed constraints

### E01 — Check authority where the action depends on it

- **Scope / trigger:** Before a Brain or organization action that depends on a hold, assignment,
  branch owner, or release permission.
- **Finding:** The standing mandate and dated state explain process, but a current task, hold, or
  launch decision must come from its current authority record. A worker checkout or historical
  resume note is not current authorization.
- **Action:** Use explicit current founder authorization within its stated scope. Check only the
  authority record relevant to the proposed action when its permission depends on that record.
  Routine authorized file reads and local tests do not require whole-company reconciliation or
  runtime access. Continue independent authorized work; local setup permission does not resume a company.
- **Sources:** [Engineering organization](engineering-organization.md),
  [operator and release ownership](operator-runtime.md), and this repository's [agent rules](../AGENTS.md).
- **Revalidate when:** Authority, Paperclip lifecycle, or owner handoff format changes.

### E02 — Source acceptance and runtime readiness are separate claims

- **Scope / trigger:** When reporting a candidate, review, release, or observed behavior.
- **Finding:** Exact source identity and source review do not establish what is installed or healthy
  on the owner runtime; a worker checkout is not owner runtime.
- **Action:** Report authored, tested, independently reviewed, integrated, deployed, and observed
  outcomes separately. Read back the owner deployment identity only when deployment is in scope.
- **Sources:** [Operator and release ownership](operator-runtime.md),
  [engineering organization](engineering-organization.md).
- **Revalidate when:** Release, host, or deployment interfaces change.

### E03 — Historical event time does not establish information availability

- **Scope / trigger:** Data admission, replay, training, evaluation, or claims about online behavior.
- **Finding:** Event time, availability time, receipt time, and decision time have distinct meanings;
  later receipt cannot be treated as earlier knowledge.
- **Action:** Preserve each timestamp and its provenance through admission and evaluation; do not
  infer online-causal evidence from retrospective replay.
- **Source:** [Shared data and replay contracts](contracts.md).
- **Revalidate when:** A source adapter, event schema, or replay contract changes.

### E04 — Keep protected evidence and unknown outcomes intact

- **Scope / trigger:** Recovery, retries, candidate correction, or work involving protected data.
- **Finding:** Failed, partial, stopped, and unknown outcomes remain distinct; protected F01/D43 and
  reserved holdouts retain their data boundary.
- **Action:** Reconcile the exact affected operation before retrying, retain its history, and use
  synthetic/local data for setup and engineering checks.
- **Sources:** [Shared contracts](contracts.md), [operator/runtime policy](operator-runtime.md),
  and [agent rules](../AGENTS.md).
- **Revalidate when:** Recovery, storage, data-protection, or retry semantics change.

## Engineering lessons from saved audits

The findings below were recorded by earlier audits. On 2026-10-26 this setup pass checked the saved
source text and relevant canonical summaries; it did **not** reproduce the historical incidents or
re-audit the campaign. The practical actions are reusable guidance, not newly measured improvements.
The founder-supplied extract is `codex-trusted-workflow-v2/knowledge/LESSONS.seed.md` (in the setup
package), with stable lesson identifiers below; private handoffs and raw logs are not copied here.

### E05 — Check available capabilities before adding a subsystem

- **Trigger:** A proposed dependency, context transport, admission layer, or other subsystem.
- **Historical finding:** The audit attributed overhead to custom orchestration layers; it did not
  establish that every custom component was unnecessary.
- **Action:** Inspect the installed interface, exposed role capabilities, existing project code and
  primary documentation. Identify the concrete gap, then compare reuse with the smallest replacement.
- **Sources:** Supplied extract L01/L03, 26 October audit sections “Критическая оценка архитектуры”,
  “Главные источники потерь”, and “Запись отказа QA и управление дочерней задачей”;
  [organization simplification map](engineering-organization.md#replacement-and-simplification-map).
- **Revalidate:** Platform version, role exposure, or requirements change.

### E06 — Give the implementer the previous candidate as well as the accepted base

- **Trigger:** Correcting a rejected candidate or handing work to a fresh executor.
- **Historical finding:** Missing rejected code caused repeated reconstruction; the saved relay
  account distinguishes correction evidence from an accepted implementation base.
- **Action:** Supply exact accepted-base identity, the complete labeled rejected diff, QA findings,
  acceptance cases and relevant constraints through the existing artifact route. Check that the
  executor can retrieve them before requesting rework; never silently promote rejected code.
- **Sources:** Supplied extract L02; [engineering organization](engineering-organization.md),
  candidate-context relay and receipt
  `resumption-20261025/sol-independent-native104-owner-disposition-QA105-rejection-full-diff.json`.
- **Revalidate:** Handoff format, artifact availability, or candidate identity changes.

### E07 — Reproduce the actual failure route and its effects

- **Trigger:** A regression is green but could reach the expected state through another handler.
- **Historical finding:** Earlier recovery checks could exercise startup recovery instead of the
  scheduled same-run path; terminal state alone did not prove the intended helper ran.
- **Action:** Reproduce the effective command, input, environment and entry route. Assert a
  distinguishing handler effect and the required behavior, not only a shared final status.
- **Sources:** Supplied extract L04; recovery source `8d994c9`, `accepted_source_only` receipt,
  and private operational HANDOFF section “TEST LIMIT” (source locator in [INDEX](INDEX.md)).
- **Revalidate:** Dispatch, scheduling, recovery entry points, or expected effects change.

### E08 — Review complete behavior with proportionate model effort

- **Trigger:** A significant change needs QA, or repeated model checks add no new evidence.
- **Historical finding:** The audit reported fragmented checking/release/documentation effort while
  independent QA still found substantive defects. Turn counts are not quota percentages.
- **Action:** Keep independent QA for significant changes and protected boundaries. Give one reviewer
  a coherent candidate and observable acceptance cases; batch related feedback, reuse valid evidence
  for unchanged inputs, and rerun checks when changes or failures justify them. Do not weaken acceptance.
- **Sources:** Supplied extract L05/L06, 26 October audit sections “На что ушла квота” and
  “Чем закончилась MEM-99”; [engineering organization](engineering-organization.md), candidate
  verifier and independent semantic QA responsibilities.
- **Revalidate:** Risk, acceptance, candidate content, or evidence validity changes.

### E09 — Reconsider architecture when repeated corrections deliver little product value

- **Trigger:** Repeated rejection, reconstruction or orchestration effort without useful accepted output.
- **Historical finding:** The campaign's repeated corrections did not establish useful program
  completion. A smaller decomposition is a proposed remedy, not a proven historical counterfactual.
- **Action:** Stop repeating the same repair without new evidence. Revisit context, hypothesis,
  architecture and behavioral scope; compare a simpler existing route and a coherent smaller outcome.
  Judge progress by accepted useful behavior, not file counts or green preliminary tests.
- **Sources:** Supplied extract L10 and audit rejection list;
  [engineering organization](engineering-organization.md), strategic feasibility assessment receipt
  `resumption-20261025/sol-independent-native106-AstraMedium-Sol28-stop-feasibility-gap.json`
  and the replacement/simplification map.
- **Revalidate:** New evidence, changed requirements, or a comparable implementation outcome.

## Retrieval

Start with [the documentation map](INDEX.md), then retrieve a bounded excerpt from its canonical
owner, for example:

```sh
python3 tools/agent_context.py --task review --query 'rejected candidate exact diff independent QA'
```

Ordinary `rg` search and Markdown links work in a terminal or Obsidian. Git retains detailed history;
this file does not copy receipts, task state, raw transcripts, or private operational handoffs.
