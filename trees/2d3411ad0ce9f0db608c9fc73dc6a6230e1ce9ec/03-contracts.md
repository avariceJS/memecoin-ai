# Read-only UI contracts v1

Executable example: `apps/control-center/published/observatory-v1.json`.
Typed contract: `apps/control-center/contracts/types.ts`. Source mapping:
`adapter/export_snapshot.py` + `contracts/source-pins.json`; [data map](02-data-map.md).
Это UI-owned projection schema, **не** новый core contract/разрешение.

## Transport

| Route | Method | Response |
|---|---|---|
| `/api/v1/snapshot` | GET | SnapshotResponse: apiVersion,snapshotId,servedAt,data |
| `/api/v1/capabilities` | GET | Same envelope;data={capabilities} |

Ни path/source/query/filter, ни command/request body не принимаются. Другие paths404,
write methods405, bad origin/Host/custom header403, malformed/missing artifact503.
Ошибка имеет только stable code, без provider text/absolute paths. Не отдавать stacktrace.
Response≤512KiB input bound, no-store,nosniff,CSP default-src none. Нет статического
file server, source code endpoints, downloads или shell/URL proxy.

Backend binds **127.0.0.1**, exactHost127.0.0.1:port. Frontend origin127.0.0.1:5173
илиsame-origin; X-Control-Center:1 обязателен. CORS только exact origin, no credentials,
no wildcard; cross-site Fetch Metadata отвергается. Loopback-only не auth против локального
пользователя/процесса: сейчас доступны только reviewed Git evidence. До любых private data/
write capabilities потребуется новый auth/threat-model review, не «добавить POST».
API tests используют ephemeral loopback port; никакого внешнего network test.

## Provider boundary

React component → domain view model → typed provider → DTO boundary validator → read API.
Компоненты никогда не import source JSON из docs/evidence и не вычисляют core folds/targets.
Имена provider capabilities — opaque IDs, unknown capability допустимо показать unavailable.
Unknown majorversion/API/dataKind → error, не optimistic fallback/demo. Minor additive fields
игнорируются после validation. Breaking changes →newmajor+explicit adapter migration.

| Provider | v1 payload | Runtime capability |
|---|---|---|
| CalendarProvider | D43 planned/safe recorded window rows, protected range | recorded read |
| CorpusProvider | D45 revision/development count summary | recorded read |
| ResearchProvider | specification/readiness/budget/recorded release + D48 plan summary | recorded read; no admission commands |
| ModelEvidenceProvider | engineering run metadata + measured benchmark rows | recorded read; not model registry/promotion |
| SourceEvidenceProvider | D49 historical RPC/Clock/latency/cohort/verdict | recorded read; no RPC |
| SystemProvider | UI API state only; core status unavailable | no process/hardware polling |
| Market/Prediction/Portfolio/TradingProvider | unavailable reason | no backend implementation |

TrainingProvider/trial/exposure detail provider не создавать только ради классов;
пока summary вResearch/Models достаточно. Когда core опубликует безопасный ledgerfeed,
отдельный adapter сохранит тот же UI vocabulary/provenance contract.

## Envelope/time semantics

- `snapshotId`: SHA256 exact bytes published bundle; связывает все panes одной projection.
- `servedAt`: текущее время UI API response, не обновляет evidence/source availability.
- `coreBaselineCommit`: baseline handoff, не текущее состояние main или runningcollector.
- `sources[id]`: source digest/date/recordedAtNs/null; integrity PINNED_BYTES.
- Всеepoch nanoseconds — **decimal strings**. Использовать BigInt при точном сравнении;
  при передаче вchart Date переводить только display-resolution (ms), исходныйns сохранить.
- Date-only source не получает выдуманный timestamp00:00. UTC/Moscow labels явные.
- Provider failure не меняет core status. UI может сохранить last-known approved snapshot,
  но показывает ERROR/STALE+last retrieval time; не silently serve cached as fresh.

## Domain interpretation

`Pane<T>` имеет availability/provenance/qualification/sourceIds/data/reasonCode.
DEMO из отдельного explicit selection не смешивается с REAL_EVIDENCE; recorded measurements
наsynthetic inputs маркируются отдельно от mock UI. Unavailable/protected/error:data=null,
не empty success array. `activeModel:null` не «выбран cash» и не GRU default.

Window union:
- DEVELOPMENT: recordedStatus, timestamps/counts nullable, stage exit codes раздельно.
- PROTECTED: только{id,index,plannedAt,access}. Никаких snapshot counts, targets/status
  classes/fees/latency/QA reports; не hidden CSS — этих полей нет вresponse/browsercache.

Projection sourcewindow0 реально завершено. User-provided window1 failed/executionFAIL
можно добавить **только** отдельнойsynthetic test fixture, не записывать какrealfact.
D48planned calendar — design, не тот же namespace чтоD43 actual window IDs.
`quarantined` потребуется вfuturecoreexport; не смешивать сfailed/holdout/missed.

F01: recorded authorization fields показывают прошлое evidence. `admission=LOCKED` —
политика текущей read-only оболочки, она остаётся locked даже если будущий core source
содержит true: право commands не появляется отзаменыDTO. Numeric111blockers — не проценты
готовности и не countdown доразрешения. `missingReviews` вsnapshot взяты изfrozen design/
recorded absence; неизвестное текущее состояние так и подписать.
Показывать0/114 **recorded researchfits**,135engineeringfits отдельно. Неизвестное глобальное
exposure count не заменять0; release count иexposurecount разные сущности.

## Runtime schema validation, errors иperformance

Phase1 реализует Zod validator под `types.ts`, tests against full published fixture;
запрещены coercion unknown→0, implicitNs→Number иmerging synthetic/realarrays.
Client fetch AbortController deadline3s, retries0 дляimmutable API, no refetch-on-focus
приofflinebundle; manualrefresh допустим. Error boundary не показываетrawresponse.
Дляfuture poll feed задатьlease/lastupdated+sequence иbackoffдо60s; изменениеstate не
считается atomicвсегоengine безcontract. Readiness gate не вычисляетсяfrontend.

Filter/sort/pagination — производные view state, не core artifact. Query cache только
in-memory; localStorage только theme/timezone/table layout (schemaVersion), без frames/
labels/receipts/tokens. ServiceWorker cache не добавлять без review. Charts не сглаживают
missed windows и не соединяют gaps; bounded point budget. Если нужна decimation, помечать
её как derived display с исходным provenance; features/targets никогда не пересчитывать.

## Future interface proposals, не текущие endpoints

- OperationalSnapshot: nodeId/serviceId, observedAt/recordedAt/staleness budget, sequence,
  typed state/unknown/error category, exact code/plan ID; metadata only.
- PredictionView: procedure/model/version, scope/source revision, decision/available time,
  target units/horizon/uncertainty. Только после принятого core prediction contract.
- ControlApi: отдельные auth/authorization/idempotency/audit; core-owned command validation,
  risk и emergency-stop semantics. Никаких action stubs в read API.
- HoldoutView: отдельно разрешённые metadata, partition check before reads, никаких raw
  или label-based selection в UI.

Недостающий contract → formal proposal в docs/control-center/proposals, не core правка
из этой ветки. [CC-P001](proposals/CC-P001-operational-export.md) уже подготовлен.
