# Решения UI workstream

## CC-001 — сохранить текущий worktree, схема A

Принято2026-10-03. Detached6a7e079 превращён в branchcontrol-center/observatory-handoff.
Grok продолжает здесь; новый worktree и merge main не нужны. Причина: простая изоляция
working files без мешающего main lifecycle. Общие Git refs/config остаются риском — не
трогать их кроме собственной ветки/push. Scope guard проверяет diff от6a7e079.

## CC-002 — reviewed projection вместо direct raw artifact mounts

Принято. V1runtime читает только published bundle/manifest внутри app. Offline exporter
имеет9конкретных inputs сexactSHA, explicit field mapping, no nested path following.
D43holdout rows — metadata-only. Raw `work/`, `.env`, logs/checkpoints не нужны браузеру.
Причина: read-only core import/CLI не обязательно без side effects/exposure; Ledger ctor
пишет, calendar_inventory читает labels. Projection не второй source of truth: source
hash/date/provenance и exact reproduction обязательны; вручную факты не править.
Ограничение: historical snapshot не realtime. Новый core-safe export — CC-P001 PROPOSED.

## CC-003 — web first, thin local backend, no realtime infrastructure

Принято. React19+TSstrict+Vite, ReactRouter7, TanStackQuery5; CSS modules/tokens;
Table8/Recharts3 по потребности; Virtual3 только после измерений. Python stdlib read API
отдельно от core. No SSR/Redux/database/distributed services. Exact package pins/lock Phase0.
Future Tauri2 после web acceptance, не сейчас; его capabilities/auth/lifecycle отдельныйreview.
Выбор библиотек — UI engineering choice, не fixation ML architecture.

## CC-004 — разделить knowledge, access, purpose и time

Принято. REAL_EVIDENCE/DEMO отдельно от SYNTHETIC INPUT иENGINEERING purpose.
AVAILABLE/UNAVAILABLE/PROTECTED отдельно от admissionLOCKED иrecorded/live freshness.
servedAt не обновляет evidence availability; pending не inferredmissed. Current model
unknown, global exposures unknown, counts не permission. No demo fallback on API failure.

## CC-005 — future controls без fake semantics

Принято. Read API не содержит command/path/shell/provider endpoints; UI не может
активировать даже источник/обучение. Emergency stop только unavailable placeholder.
Core решит control/risk/execution/auth semantics в своём roadmap. Future interface
proposal ≠ принятое core решение. Это сохраняет возможность роста без смешенияполномочий.

## CC-P001 — operational metadata export

PROPOSED, core acceptance отсутствует. См. docs/control-center/proposals. Не реализовывать
его в core из UI branch; можно продолжать все recorded evidence phases независимо.


## CC-006 — primary visual fidelity без ложной semantics

Принято2026-10-03. User PNG primary; каждый visual worker сам открывает изображение,
review сравнивает реальные screenshots. DESIGN-SYSTEM задаёт плотную navy composition,
status truth/accessible fallbacks. Trading art не data source. Token values — начальные
UI решения, не вымышленные «точные измерения» картинки. Нет semantic changes core.

## CC-007 — native local team + repo execution state

Принято. 6CC custom agents: artifact analyst, UI+visualization builder, adapter builder,
test-debugger, visual-reviewer, boundary-reviewer. Coordinator owns docs/state/Git и
сложные решения. Explicit `composer-2.5[fast=false]` по official docs, no cheap-model guess,
no silent inherit; actual task card verification P0.1 pending. Grok4.7/xhigh coordinator.
Scope extension — только6named `.cursor/agents` files; scoped guard допускает exact names.
Не менять global Cursor settings, permissions, hooks, root rules/core memory.

## CC-008 — bounded autonomy/recovery, не вечный daemon

Принято. WORK_STATE + generated STATUS + checks/review receipts; phases0–7 web-v1 loop,
ordinary errors чинятся без approval, missing interface → proposal и независимая работа.
Native delegation/context isolation; account/approval/tool limits явно не гарантированы.
Не SDK/cloud/cron/newchat automation. Model/runtime conformance отдельно от offline fixtures.
Scope guard проверяет working tree **и index** от base, без rename detection: staged core
change нельзя спрятать отменой только working copy. Shared JSON — single writer protocol,
не distributed scheduler и не immutable evidence ledger core.

## CC-009 — exact UI pins без сдвига major

Принято 2026-10-03. Registry latest в этот день: React 19.3.0, Vite 8.3.2,
TypeScript 7.0.2, React Router latest 8.4.0, TanStack Table latest 9.2.4.
Оставляем Router 7.18.4 и Table 8.21.3, потому что CC-003 уже выбрал эти major.
Query 5.104.1, Zod 4.6.5, Vitest 5.0.3 (peer Vite 8). Полный список —
`docs/control-center/toolchain-pins.md`. Lockfile появится только вместе с `web/`.
Причина: совместимая матрица до scaffold, без root install и без web3/RPC пакетов.
Boundary review матрицы PASS до установки. Эксперимент установки не проведён.

## CC-010 — непроверенная карточка модели не останавливает web v1

Принято 2026-10-03 по прямому указанию пользователя. Native task card фактически
использованной модели через UI подтвердить нельзя. `model_binding.status` остаётся
`UNVERIFIED`, `observed_executor` пустой. Configured executor по-прежнему
`composer-2.5[fast=false]`; YAML агентов не переписывать на fast или inherit.
`VERIFIED` допустим только вместе с observed id и evidence. P0.1 закрывается по
DTO/boundary, не по карточке. Web v1 phases 1–7 продолжаются.
