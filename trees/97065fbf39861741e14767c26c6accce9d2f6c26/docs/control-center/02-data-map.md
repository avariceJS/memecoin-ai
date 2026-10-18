# Карта источников и разрешённых artifacts

Срез2026-10-03, core commit6a7e079. Это **не** inventory текущего D50+ main.
`apps/control-center/contracts/source-pins.json` содержит точные9paths+SHA; только эти
файлы читает offline exporter. Runtime читает только `published/observatory-v1.json`.
Все пути таблицы ниже относительно isolated repo, если не сказано иначе.

## A. Доступно сегодня: committed, предварительно просмотрено

| ID / authoritative artifact | Schema / используемые поля | Mutability / refresh | UI scope / ошибки |
|---|---|---|---|
| d43 `docs/evidence/calendar-execution-2026-10-03.json` | Stage D43, `recorded_ns`, `windows[]`, `plan.due_ns`; row0 snapshots/targets/quality/exit codes | Исторический Git report, pin exact bytes, manual re-export review | Development0–3 summary;4–5 только planned time+protected. Changed hash → refuse, не fallback к runtime |
| registry_protocol `configs/corpus-registry-v1.json` | schema1, `holdout.start_ns/end_ns`, folds with30s embargo | Frozen config | Planned membership metadata only; без labels; отсутствует → calendar projection не строится |
| d44 `docs/evidence/ml-foundation-2026-10-03.json` | stage D44 engineering_only, runs[].training/model/seed/epochs, benchmark.results[] | Исторические runs15 и40 cases + отдельный MPS train в original report | Smoke != research; activeModel=null. No checkpoint loading; missing timing=null |
| d45 `docs/evidence/corpus-registry-2026-10-03.json` | stage D45, revision000000, qualification_report.development, development_windows | Git snapshot revision |174usable/1392scenarios/362censored/122complete-grid/5greedy nonoverlap. Не обновлять по часам; не independent N |
| d46 `docs/evidence/research-readiness-2026-10-03.json` | stage D46, decision.protocol_frozen/authorization booleans | Исторический assessment | Frozen spec readiness отдельно от corpus/admission; fixtures positive_readiness не проектный PASS |
| f01 `configs/research-f01-v1.json` | schema1,id F01-v1,training.max_trainable_fits,required_reviews | Frozen spec |114budget не число выполненных fits; текущий grid только внутри study; spec не permission |
| d47 `docs/evidence/f01-runner-integrity-2026-10-03.json` | addendum,real_F01_fits,actual_holdout_releases,engineering_run_history | Final исторический D47 summary |0researchfits,135engineering fits. Не складывать вместе и не показывать глобальные exposures0 |
| d48 `docs/evidence/f01-sampling-2026-10-03.json` | proposal.numeric_checks/blockers/anchor/cutoffs;sampling counts/UTC_hours | Frozen proposal summary |336planned/224development/112reserved,0admitted;111/113blockers. План не активный collector |
| d49 `docs/evidence/f01-collector-qualification-2026-10-03.json` | source.rpc/accepted/latency/Clock/pools,verdict,tests,blockers | Bounded probe report |6/6pass,NOT_READY_FOR_ACTIVATION. Не currentRPChealth; measurements bounded. Не экспортировать account/app/key fingerprints |

Input JSON не имеет единой общей схемы: versioned adapter per artifact, pinned bytes и
explicit whitelist. No generic JSON browser. Source field rename/schema drift → unavailable/
error + formal adapter review. Исторические docs иногда говорят «следующий этап» уже после
его завершения: UI разрешает хронологию, не перезаписывает историю latest-looking text.
D47 addendum приоритетен для final counts; base D47 report — reading context, не второйcounter.

## B. Форматы core, полезные для понимания; runtime UI доступ ЗАПРЕЩЁН

| Семейство / logical path | Авторитет / schema | Почему не читать из UI | Нужный будущий export |
|---|---|---|---|
| D43 `work/calendar-execution-01/plan.json` | immutable due_ns/spec/pinned runtime | Out-of-worktree local operational root; known secret-free file не разрешает соседние пути | Core-owned calendar planned metadata |
| D43 `outcomes/NNN.json`, `windows/NNN/reservation.json`, `seal.json` | append-only outcome/index/start/finish/exit codes/inventory | Seal inventory ведёт к labels; future/protected windows; не сканировать | Metadata-only operational rows fromcore, no recursive raw access |
| D43 campaign/captures/targets/quality/execution | immutable raw + derived labels/fees | Raw/holdout/availability disclosure, huge data; calendar_inventory читает targets всех окон | Разрешённый development-only summary с explicit membership и redaction |
| D44 `work/ml-d44/**` | plan/cases, sealed run report, manifests, weights, benchmarks | Имеются real/synthetic datasets и tensors; UI не принимает pickle/model checkpoints | Run metadata + permitted metrics only, parameter count supplied by core |
| D45 `work/corpus-registry-01/contract`, `objects`, `revisions/000000` | contract schema1, revisions parent manifest, qualification/folds/development | sync/audit может писать/читать много данных; sample IDs/targets не операторская telemetry | Explicit revision summary, counts, fold boundaries; protected no labels |
| D46 `work/research-d46/protocol` | spec/design/trial plan/assessment | Local snapshots отсутствуют здесь; не копировать весь root | Already committed summary достаточно дляv1 |
| D47 `work/runner-d47/**/event-*.json` | sequence/previous/SHA/kind/time_ns/payload | Exposure events/paths и future research tests; Ledger ctor writes/locks | Core-owned ledger metadata projection + exposure completeness scope |
| D48 `work/sampling-d48/proposal/{binding,spec,manifest}.json` | regenerated design/source hashes, source_root, permissions false | Path binding не право доступа к source root; no auto activation | Planned window metadata и review status |
| D48 `registry/revisions/*/revision.json`, `dossier/{report,provenance,folds}.json` | source records,role checks,coverage denominator,missing reviews | Raw conversion/source verification скрыто открывают bytes; fold/sample memberships не выгружать вслепую | Safe per-role counters с recorded revision/head |
| D49 `work/collector-d49/activation-candidate`, `http-probe-01` | exact binding/interpreter/credential fingerprint,raw responses | Account/local paths/credential fingerprint не нужны браузеру; raw network input | Already committed selected source qualification |
| Source files `.env*`,wallets,credentials, runtime private logs | Секреты или произвольный текст | Полный запрет, включая filename discovery | Никогда не нужны UI read plane |

В isolated worktree `work/` и `.venv` отсутствуют — ожидаемо: ignored artifacts не
копируются Git worktree. Подтверждено только existence отдельных metadata paths в
основном checkout; их contents/holdout не открывались. Не исправлять это symlink/mount.
Первый UI запускается на Git evidence без operational disk. Относительные work paths
в JSON — provenance, **никогда** не инструкция открыть файл. Абсолютные пути из evidence
не экспортируются в браузер.

## Поля и неоднозначности

- Snapshot counts180/180 != usable174 != mature135 !=1440scenario rows. D45 usable-start
  dataset1392variants отличается от D43 all-start1440: не «исправлять несходство».
- `actualStartNs=null`: pinned D43 summary не публикует reservation.started_ns.
  firstReceiptAt — другой timestamp. `finishedNs` известен из outcome.
- `executionOrCollectionExitCode` — комбинированное историческое поле, не автоматически
  «execution failed». Если corpus уже failed, audit мог не выполняться. Present exit0
  относится к process, не экономической квалификации.
- `recordedSealSha256` — ссылка на seal, не UI assertion «raw seal verified сейчас».
- `recordedHoldoutReleases=0` != глобальное число exposures. Внешние открытия невозможно
  восстановить из этих summaries. Статус `globallyUnseenHoldout=NOT_CERTIFIED`.
- D48 planned336windows можно рисовать из разрешённого design, явно DESIGN/NOT ACTIVATED.
  Не назвать окна future pending active schedule. No labels/counts уreserved112.
- Три pool IDs — не token scanner/universe и не top liquidity selection. Price/reserves/
  marketcap/volume/OHLCV в current projection отсутствуют. Current `MarketProvider` unavailable.
- D44 timings measured on specific batch/context/device; сравнивать одинаковые shapes,
  не превращать median forward into full end-to-end trading latency. MPS support≠speed win.
- D49 tests502 — recorded tests этого этапа, не сегодняшнее состояние CI или UI tests.

## Refresh/access policy

V1 runtime reads только approved projection (≤512KiB), same-path fixed manifest, no globs.
Offline exporter читает только9pins, не following nested references и не запускает verifier.
Новый source является review event: classification, protected scope, fields, expected hash,
observed-at semantics, tests; затем новая projection version с lineage. Не автоматически
переписывать pins, если основной core chat выпустил D50 report.

Future operational: требуется [CC-P001](proposals/CC-P001-operational-export.md).
Until accepted, show UNAVAILABLE даже если agent технически способен читать локальныйraw.
Changed/missing/corrupt source → fail-closed export, preserve previous approved bundle,
явно показать его source date. Invalid published bytes →HTTP503 без raw bytes/errors.
Нет «последних 100 строк логов» без отдельного sanitization contract.
