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
