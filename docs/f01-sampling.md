# D48: будущий sampling/source binding и sealed corpus adapter F01

## План до реализации и опытов — 2026-10-03

Источник правил: frozen [F01-v1](../configs/research-f01-v1.json),
[D46](research-readiness.md), [D47](f01-runner.md). D43 и унаследованный holdout
не открывать/не менять. Настоящее обучение и новый сбор не запускаются.

1. Отдельный immutable proposal: future UTC anchor после inherited holdout,84days,
   четыре5min окна в сутки по6h strata (UTC00:30/06:30/12:30/18:30),336 окон.
   Existing finalized Alchemy HTTP/profile2, fixed3pools,2segments×30rounds×5s,
   ≤180RPC/окно,≤60480RPC всего; без WSS/fallback/нового execution sampling.
   Это ceiling предлагаемого плана, не разрешённый расход/тариф/оплата.
2. Source semantics, frozen converter/runtime hashes, exact schedule и отдельный
   activation boundary. First error closes window; no retry/backfill/catch-up,
   lateness≤900s,window wall≤600s,stop после8 failed окон. Missing сохраняется.
   Нельзя сдвигать календарь или заменять pools по observed outcomes.
3. Новый пакет сохраняет hashes D43–D47. Sealed development admission: actual source
   semantics/reproduction, times/identities/grid/counters, complete window seal,
   duplicate/conflict detection, append-only revisions и offline verification.
   Holdout по window index/cutoff отклонять до любого чтения его source path.
4. Dossier связывает immutable revision/runner plan, actual support, full scheduled
   denominator, failures/missed/pending, usable fraction, frozen folds и missing
   reviews. Numerics не выдают admission; forecast/economic gates прежние.
5. Fixtures на multi-window/restart/tamper/temporal/holdout/no-false-readiness,
   engineering read-only integration только D45 development. Не переименовывать
   старые timestamps в реальные prospective observations. Budget/scaling feasibility
   оценить без training; generic threshold не гарантирует power/rare-class support.
6. Offline/wheel reproduction, проверки, Memory Bank, логические commits/push.

Fail-closed при integrity/clock/source/unsupported input: safe category, сохранённый
attempt/log, без retry loop. Старые artifacts не переписывать. Новый network/runtime
activation требует отдельно зафиксированного решения по concrete budget/credentials/
feasibility; D48 не устанавливает collector и не выдаёт research admission.

## Реализованный контракт

Новый stdlib пакет `memecoin_sampling`; исходники/протоколы D43–D47 не меняются.
[Machine policy](../configs/f01-sampling-v1.json) равна фиксированной функции policy;
load сверяет полный binding с regenerated design/source hashes. Binding задаёт отдельный
ещё не существующий source root. Источники допускаются только из его `windows/NNN`,
никаких произвольных paths/симлинков. Индексы224–335 и неверно привязанные paths
отклоняются до source reads. D43 source root не сканируется.

336 запланированных окон:224 development,112 reserved holdout. В сутки наблюдается
около20min, а не непрерывный рынок; estimand остаётся conditional fixed-cohort/windowed.
60 480 — верхний RPC ceiling предложения. Monetary/CU cost, доступность credentials,
provider quota, actual scheduler/service/source freshness и activation ещё не проверены;
никаких платежей, upgrades, сетевых calls или launchd изменений D48 не делает.

Sampling и F01 model specification — разные versioned artifacts. Сдвиг future anchor
требует нового binding с сохранением прежнего; существующие окна/раскрытые данные не
переносятся в новый календарь. Если activation опоздает, нельзя catch-up/backfill или
молчаливо исправить даты. Если источник/коhort/protocol support изменились — fail-closed,
не замена pools по returns. По8 failed windows future collector обязан остановиться.

## Admission sealed development и воспроизведение

Envelope совместим с existing campaign/window schema: reservation exact index/due/start,
proposal-file SHA и180reserved RPC; completed outcome с exit codes0, finished within
600s и lateness≤900s. `data_kind` обязателен:prospective илиsynthetic_fixture.
Source path привязан до чтения; затем metadata guards, seal inventory, campaign budget,
full campaign reproduction/complete180 snapshots, exact source/target semantics и
D44 raw→features/targets/converter. Raw сохраняются байт-в-байт рядом с converted object.
Не меняются CPMM/features/targets, receipt/availability/maturity; history сбрасывается
existing правилами. Duplicate(pool,decision), changed grid, late availability и
cross-window history не становятся дополнительными samples.

Writer lock, immutable terminal window records, staged objects/revisions, parent seal
hashes и retained attempts. Один revision добавляет один terminal факт; late registry
admission не создаёт прошлую online availability. Повтор идентичного объекта idempotent,
terminal conflict отвергается; publication crash оставляет bytes и может продолжить
только offline import, не повторить сбор. Raw provider/SHA integrity не является proof
against владельца, переписывающего все hashes; для аудита pin revision head отдельно.

`reproduce_sources` независимо регенерирует converter из retained raw. Единственное
нормализуемое поле — абсолютный filesystem path места conversion; это не market time.
Все features/targets/IDs/masks/available_ns и quality должны совпасть. Dossier reproduction
использует выбранный immutable revision даже после появления новых окон и без source mount.
Registry ревизии хранят references/records, не дублируют объединённый corpus каждый раз;
full dataset материализуется только по явной команде dossier.

## Readiness dossier

D47-compatible runner plan и grouped chronological memberships строятся из exact F01
cutoffs; готовый plan **не admission**. Все роли получают support/class/censoring/strata/
pool checks D46, закрытие календарного периода, completed/planned fraction и usable
fraction. Denominator всегда полный заранее заданный schedule роли, не только успешные
или уже импортированные окна. Holdout support до release вообще не оценивается.

Для неполных/failed/missed/pending источников actual feature starts могут быть неизвестны.
Dossier не объявляет их нулём: учитывает ceiling180 на такое окно и показывает lower bound
usable fraction. ≥90% этого lower bound — консервативно достаточное условие исходного
порога, не оценка actual fraction и не ослабление F01. При недостатке evidence gate остаётся
закрыт; completed/planned≥80% само по себе не обещает пройти остальные критерии. Failed
metadata — declared operational fact, не evidence успешного raw replay; его точность и
activation/runtime counters подлежат отдельному review. Факты после failure-stop
сохраняются, но делают соответствующую adherence check false.

Synthetic sources маркируются в raw disclosure/outcome, registry и dataset provenance;
`prospective_only=false` препятствует readiness даже при хорошем synthetic support.
Linked scope/semantics/dependence/runner/exposure/formal reviews и actual activation
не заменяются булевым numeric_pass. Research/holdout/economic permission всегда false.

## Команды без запуска collector

```sh
# Только новые directories; SOURCE_ROOT должен ещё не существовать.
# PYTHONPATH=src .venv/bin/python tools/f01_sampling.py freeze \
#   work/research-d46/protocol NEW_PROPOSAL --anchor-ns FUTURE_UTC_MIDNIGHT_NS \
#   --source-root NEW_ABSOLUTE_SOURCE_ROOT
# PYTHONPATH=src .venv/bin/python tools/f01_sampling.py init NEW_PROPOSAL NEW_REGISTRY
# PYTHONPATH=src .venv/bin/python tools/f01_sampling.py ingest REGISTRY INDEX \
#   --source BOUND_SOURCE_ROOT/windows/NNN
# PYTHONPATH=src .venv/bin/python tools/f01_sampling.py dossier REGISTRY NEW_DOSSIER
# PYTHONPATH=src .venv/bin/python tools/f01_sampling.py reproduce REGISTRY DOSSIER
make check
```

`tools/f01_sampling_smoke.py NEW_ROOT` использует только старые D40 reference bytes,
явно synthetic Clock/slot/receipt и injected transport;180fake calls, real network0.
Это end-to-end protocol/converter conformance, не новый prospective corpus и не проверка
persistent HTTP. Unit multi-window fixtures mock только raw validation/conversion boundary;
отдельный полный raw smoke не использует этих mocks.
`tools/f01_data_capacity.py SEALED_D45_DEVELOPMENT NEW_OUTPUT` создаёт явно synthetic
shape fixture224copies с отдельными Python objects;120s child deadline,≤180rows template.
Только load/validate/partition feasibility, без fit/torch/holdout и без statistical evidence.
