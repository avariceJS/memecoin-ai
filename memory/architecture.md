# Архитектурное направление

## B07 — работающая exploratory paper вертикаль, 2026-10-04

Frozen B04 source → first-observed causal GT bars → D44/D47 Brain predictions → отдельный
memecoin_paper decision/accounting engine → immutable transition ledger → actual future
price outcomes → daily policy/forecast evaluation. Параллельно отдельный bounded daily
trainer → sealed candidate bundle → новый future challenge; champion не заменяется.

Три изолированных LaunchAgents: paper30s, train eligibility3600s/dailyfits, read-only UI8790.
Frozen source/model/runtime hashes, source expiry/STOP, lock+worker lease/deadline,
600s training/120s paper, no retry afterlane failure. Paper01 incident сохранён, paper02
активирован. Рабочая панель публикует whitelist dashboard.json; отдельная UI worktree
recorded observatory не merge/reset. Это временный bar proxy, не PumpSwap executionadapter.
Приостановка Mac означает наблюдательный gap; источник истекает2026-10-07, неweeklySLA.
[B07](../docs/brain-paper-loop.md).

## B06 — отдельный PumpSwap evidence interpreter, 2026-10-04

memecoin_pumpswap не venueexecutionadapter: ABI inspection, CPI tree, integertransfer/vault
reconciliation, boundedindexedclient/rangejournal/offlinereplay. PublicIDL drift/unknown
Token-2022 profiles quarantined, training permission никогда не выдаётся автоматически.
Journal покаfixturesqualified; weeklysupervisor и livecost/coverage не доказаны. B04
frozenruntime безизменений. NextB07 — deployedprofile+correctedindexedprobe.
[Границы](../docs/brain-pumpswap-events.md).

## B05 — направление source architecture, 2026-10-04

GT discovery/history/cross-check + независимая raw finalized transaction lane с verified
venue decoders → causal qualified corpus → D44/D47 bounded candidate training → paired
future shadow → отдельноеpromotionрешение. Это целевая схема, не уже deployed product.
B04 frozen collector остаётся прежним, новый memecoin_brain_sources пока evidence-only:
ledger/replay/bounded probe/health policy. Реальные9/12PumpSwap owners определили B06decoder;
CPMM/features/targets не меняются ради источника. Полный unattended supervisor и economical
market-wide ingestion ещё bottlenecks. [Обоснование](../docs/brain-source-architecture.md).

## Реализованный reference контур

Точная карта кода и команды находятся в [README](../README.md); ограничения в [contracts](../docs/contracts.md). Реализованы события/хранилище/observations, dual-clock synthetic replay, ledger/risk/target conversion, sampling и supervised contracts, experiment recorder и read-only RPC. Это инфраструктурная проверка архитектуры. Ниже остаётся направление будущей рыночной системы; наличие раздела не означает, что соответствующая модель уже реализована.

## Общая конструкция

```text
Point-in-time market events, pool states, metadata and graph evidence
  ↓
Shared feature pipeline and token representations
  ↓
Broad candidate scoring and optional cross-token interactions
  ↓
Market forecasts and action-conditioned outcome distributions
  ↓
Portfolio state and sequential planner or policy
  ↓
Independent feasibility and risk constraints
  ↓
Execution adapter
  ├─ historical event simulator for research
  └─ live shadow/paper adapter for forward evaluation
```

Обучение и оценка — отдельный контур вокруг этой схемы. Система не переобучается после каждой сделки автоматически. Реальный execution adapter не разрешён и не реализован.

## Контракты между слоями

| Слой | Вход | Выход и обязательные свойства |
|---|---|---|
| Data | События источников, состояния пулов, metadata | Raw evidence, время получения и доступности, provenance, пропуски, подтверждение |
| Features | Только доступное к моменту решения | Causal признаки, masks, возраст наблюдения, одинаковая семантика offline/live |
| Token encoder | История токена и текущие признаки | Embedding с общими весами для новых и существующих токенов |
| Candidate selection | Доступная вселенная и лёгкие признаки/embeddings | Кандидаты, scores, причины недопуска, время обновления; открытые позиции не теряются |
| Market interactions | Embeddings и доступные связи | Контекст рынка и токенов без зависимости от произвольного порядка активов |
| Outcome heads | Представление, размер, горизонт, состояние исполнения | Калиброванные прогнозы, риски, неопределённость; чётко заданная семантика outcome |
| Portfolio control | Прогнозы, позиции, cash, pending orders, ограничения | План или целевые позиции с возможностью ничего не менять |
| Risk and feasibility | Предложения и фактическое состояние | Только допустимые действия; ограничения не обходятся reward |
| Execution | Допустимое намерение и состояние рынка | Fill/failure/drop, время, комиссии, изменения балансов и диагностические причины |

## Представление токена

Не выбирать backbone по названию. Первое сравнение: TCN, GRU и небольшой Temporal Transformer на одинаковых входах и targets. LSTM — конкурент GRU; SSM/Mamba — после проверки эффективной реализации на оборудовании. См. E01 и E02 в [реестре экспериментов](research.md).

Стартовая гипотеза входа: многомасштабные временные агрегаты плюс короткая событийная ветка, masks и интервалы времени. Варианты bars-only и events-only обязательны для ablation. Общий encoder, а не отдельные веса для каждого токена. Не вводить обучаемый идентификатор токена как способ запоминать исторических победителей.

Прежние 1–5 млн параметров заменены scaling experiment 0.5M, 2M, 8M, 30M поэтапно. 100M рассматривается только при продолжающейся пользе. Эти размеры — точки исследования, не обязательная архитектурная сетка.

## Вселенная и scanner

Сравнить простой scanner, табличный learned scanner и лёгкий shared encoder всей доступной вселенной с последующей тяжёлой обработкой top-K. Стоимость данных может быть выше стоимости embeddings. Число K определяется recall, latency и итоговой портфельной пользой, а не фиксируется в 100–300.

Открытые позиции остаются под наблюдением независимо от отбора. Cash всегда доступен. Для оценки упущенных возможностей нужны контрольные временные блоки с подробной обработкой всей доступной вселенной.

## Межтокенная информация

Порядок исследования: собственные признаки → SOL и market aggregates → learned pooling → sparse/set attention → граф проверенных связей. Взаимодействия могут быть полезнее простого увеличения temporal backbone.

Оценки отдельных токенов должны быть permutation-equivariant, общий market embedding — invariant. Миграция капитала требует временных признаков или истории embeddings; один синхронный срез и attention не доказывают причинность. Возможные связи графа имеют время доступности и уверенность.

## Прогнозные задачи

Рыночные heads: распределения доходности, ухудшение ликвидности, downside и риск недоступности выхода. Ranking — отдельный head или производная от ожидаемой полезности, проверяемая экспериментом.

Outcome head: распределение net-результата при текущем портфеле, изменении позиции, горизонте, условиях исполнения и **явно заданной continuation policy**. Terminal PnL без определения последующих действий неоднозначен.

Raw return полезен как более переносимый target; net-target ближе к экономике, но зависит от качества симулятора. MFE/MAE — вспомогательные цели, не доступная по умолчанию прибыль. TP-before-SL имеет смысл для заданной barrier-policy; crossing не равен фактическому fill.

Отдельные горизонты и head с входным горизонтом сравниваются. Маргинальные распределения на 1/5/15 минут не задают совместный процесс; MPC требует согласованных сценариев или модели переходов. Размерный impact и риск нельзя выводить только из прогноза для бесконечно малой позиции.

## Последовательное управление

Supervised representation и прогнозы → однопериодный baseline → MPC → ограниченный RL-эксперимент при наличии оснований. MPC планирует последовательность по прогнозным сценариям, исполняет первый шаг и пересчитывает план. Оно должно учитывать будущую стоимость cash и terminal value; совершенное знание будущего недопустимо.

Стартовый RL-кандидат при хорошем интерактивном симуляторе — небольшой recurrent PPO, инициализированный imitation baseline, с зафиксированными сначала encoder и scanner. Это гипотеза, не обязательный этап. IQL — возможен для фиксированных реальных траекторий с достаточным покрытием действий. Рыночная история без действий агента не является таким набором.

## Позиции и риск

Предпочтительная параметризация — целевая позиция, сначала небольшая сетка, затем adaptive/continuous sizing через optimizer либо policy. HOLD должен сохранять фактическое количество токенов и учитывать pending-транзакции; изменение цены само по себе не должно требовать ребалансировки.

Условный критерий выбора размера: ожидаемый net-результат минус хвостовой риск и неопределённость, при жёстких ограничениях капитала, концентрации, ликвидности и доступности исполнения. Квантили сами по себе не обеспечивают надёжную оценку CVaR редких катастроф; нужны стресс-сценарии. Численные лимиты пока не заданы.

Reward RL, если он появится, строится на согласованной бухгалтерии и консервативной ликвидационной стоимости. Издержки учитываются один раз. Не вознаграждать только закрытые победы, не стирать убытки в конце эпизода, корректно учитывать время между решениями. Жёсткие лимиты не заменяются штрафами.

## Организация моделей

Старт — shared encoder с несколькими heads и отдельными portfolio/risk/execution-компонентами. Альтернативы: прямой end-to-end Brain и независимые модели. Модульность не гарантирует глобальную оптимальность; end-to-end тоже её не гарантирует.

После доказанного edge: supervised pretraining → частичное joint decision-focused обучение с сохранением диагностических heads. Независимые модели полезны при разных источниках/частотах или negative transfer. Внешний risk layer сохраняется всегда.

## Возможная V2 и V3

Локальная ветка TCN или другой encoder + длинная память Transformer/SSM/GRU → sparse/set interactions → локальные temporal graph features → совместные сценарии рынка и исполнения → MPC с learned terminal value или policy/value → независимый риск.

Ни один блок не обязателен без ablation. MoE допускается только при устойчивой специализации режимов и достаточных данных каждому эксперту. Сильный простой baseline остаётся соперником всей системы. Развитие модели не должно опережать достоверность execution model.

## Реализованная граница venue math

`cpmm.py` содержит отдельные immutable economic transitions pinned Raydium CPMM,
`protocol.py` связывает их с raw evidence и строгим interval audit. Эти слои не зависят
от neural architecture и не включены в synthetic ReferenceReplay. Условная точная
котировка и полная execution qualification — разные gates; детали в docs/protocol-audit.md.

`tokens.py` добавляет whitelist token semantics и отдельный versioned checkpoint с Clock.
Protocol replay учитывает transfer fees и withheld harvest, не включая пользовательские
account admission/transaction inclusion в доказанную область. Старый six-account codec
остаётся неизменным. Config/mint updates и переход эпохи требуют дальнейшей реконструкции.

## Временные параметры и evidence adapters

`history.py` применяет config/mint изменения по порядку исполнения; `protocol.py`
использует актуальные для каждого шага fees. `blockrange.py` даёт отдельный bounded
способ восстановления покрытия без address pagination. `witness.py` проверяет
LP arithmetic при event-conditioned prestate, не выдаёт его за checkpoint replay.
Эти расширения относятся к audit plane и не включают real-data ReferenceReplay.


## Реализованный переход от chain audits к dataset (D34)

`datasets.py` использует optional state export из единого `audit_protocol`, сохраняя
его проверки и реконструкцию. Frozen raw journal → quarantined/accepted intervals →
ordered economic state rows → conditional quote grid. В bundle остаются source version,
receipts/hashes и offline verifier. Нет преобразования реального CPMM в synthetic
reference Pool или снятия replay gate. Historical availability неизвестна; этот слой
служит data QA и условной симуляции, causal observations/features — следующий слой.


## Реализованный causal forward слой (D35)

`causal.py`: validated same-bank snapshots → availability-ordered CausalBook →
nullable features/masks → отдельно matured/censored/purged diagnostic targets.
`forward.py`: bounded fixed-cohort acquisition, immutable raw/online frames, offline
reproduction и versioned target refinement. Исторический economic tape не подаётся
в этот слой как наблюдение с выдуманной доступностью. Snapshot features не выдают
trade flow или executable return; synthetic replay gate остаётся.

## Возобновляемый сбор и conditional execution (D36)

`prospective.py` оборачивает existing forward collector в кампанию с immutable plan,
reference captures, source snapshot, exclusive lock, segment reservation и linked seal.
Resume добавляет новый сегмент; crash bytes не удаляет. Causal book не переносится через
границы. `execution_targets.py` читает только воспроизведённые campaign artifacts и
строит separate derived targets с size/delay/slippage/holding условиями, censoring и
campaign-wide purging. Математика из tokens/cpmm общая; synthetic Replay не меняется.
Unknown full execution costs/admission/persistent impact не попадают в labels как нули.

## Разделённые coherence/support/quality gates (D37)

`checkpoint_diagnostics.py` — отдельный diagnostic-only результат с raw hashes,
Clock/context equality и uninterpreted unknown config words; не путь допуска observations.
Existing tokens/raydium decoder остаётся строгим. `corpus_quality.py` проверяет verified
campaign/targets независимыми temporal/accounting инвариантами, считает coverage и
раздельные latency distributions, не запускает обучение. Campaign schema 2 закрепляет
public endpoint origin; закрытая кампания не возобновляется. Старые source snapshots
и verifiers воспроизводятся через pinned wheels без изменения прошлых artifacts.


## Versioned AmmConfig snapshot domain (D38)

`raydium.decode_config(layout_version=2)` понимает подтверждённое поле creator fee
share; неизвестный остаток не допускается. `tokens`/`protocol.checkpoint` явно выбирают
профиль, `causal` сохраняет collection terms отдельно от swap state. `forward` и
`prospective` закрепляют config_layout_version в frozen plan; offline target reconstruction
использует тот же профиль. Старые plans по умолчанию version1 и требуют pinned source.
Profile2 checkpoint нельзя передать legacy transition/replay. CLI diagnostics поддерживает
явный --config-layout-version 2, оставаясь diagnostic-only, не feature ingestion.

## Persistent read transport и target support QA (D39)

ReadOnlyRPC может явно выбрать stdlib PersistentHTTPTransport: TLS verified, endpoint-bound,
serial keepalive, no retries/redirects, bounded response/deadline. Campaign/forward plans
фиксируют transport_profile; resume отвергает смену до сети, attempt хранит phase/reuse
measurement. Economic adapter, raw Capture, CausalBook и target semantics общие.
`coverage.py` дополняет quality audit полной scenario-grid проверкой, зрелостью всех
вариантов в одной partition, chronological nonoverlap и наблюдаемой вариацией prices.
Никакой новый forecast feature или обучение этим audit не создаются.


## D40: transport pilot поверх общей модели наблюдений

`pubsub.py` даёт bounded verified-TLS WSS и reducer hints. `hybrid.py` сохраняет raw до
очереди, подтверждает subscriptions, принимает только strict HTTP snapshots через прежний
CausalBook и targets. Между raw WSS и pool state нет merger. Offline replay использует
тот же reducer, source snapshots и manifest; independent report сверяет receipt metadata.
В D40-v2 добавлены absolute CadenceGrid и per-account ContentTriggers, replay решений
читать/пропустить; matched10s и symmetric stress проверены. Payload criterion провален:
WSS остаётся optional/off, snapshot authority — HTTP. Для bounded D41 выбран Alchemy
HTTP5 persistent TLS через прежний prospective collector; public D39 — reference/control.
Первый D41 connect timeout закрыл кампанию; production availability не доказана.
CPMM/ML/runtime модели не переписывались. Итоги: docs/alchemy-pilot-v2.md и
docs/qualified-http-corpus.md.


D42 добавляет только offline/операционные tools: cold_connection.py наблюдает прежние
DNS/TCP/TLS calls в отдельных bounded процессах; corpus_windows.py использует existing
prospective pipeline; execution_uncertainty.py и calendar_report.py проверяют immutable
targets и агрегируют QA. Runtime modules/CPMM/features/targets не менялись. Два окна
с отдельными cutoffs не становятся global ML split. WSS остаётся off.


D43 использует finite launchd dispatcher поверх существующего collector: immutable
schedule+runtime hashes, OS lock, reservation-before-network, no replay interrupted
windows, per-window seals/outcomes. Report snapshot отдельно от живого schedule.
Observed execution — sidecar raw/QA, не изменение CausalBook/features/targets. Только
getSignatureStatuses добавлен в read-only RPC allowlist; старые bundles требуют свои
source-pinned packages. Секретов в plist/source snapshots нет.


## D44: optional ML foundation рядом с collector

`src/memecoin_ml` изолирован от stdlib `memecoin_ai`; torch нужен только optional
окружению. Frozen D43 package/runtime/interpreter не менялись. Data interface → unique-
frame train scaler → bounded history tensor25channels +pre-action scenario4channels →
swappable encoder → ordered0.1/0.5/0.9 return quantiles +3 conditional status logits.
Linear/empirical comparators; MLP/GRU/TCN/Transformer — кандидаты. GRU16/context8/2174
parameters — первый runnable temporal brain fragment, не окончательная архитектура.
Policy/MPC/portfolio/risk promotion и own execution model этим модулем не подменяются.
AdamW, masks/finite guards/clipping, validation checkpoint selection, safe weights-only
load, source/dataset/config binding, optimizer/RNG epoch resume и label-free Predictor.
CPU default, actual MPS forward/backward/parity проверены; distributed/streaming не
реализованы,256MiB eager tensor guard/partition. Большой research mode закрыт отдельно.
[Runbook/границы/результат](../docs/ml-foundation.md).


## D45: registry рядом с immutable collector и ML foundation

Новый stdlib пакет memecoin_registry, без изменений memecoin_ai/memecoin_ml sources.
Contract → hash-addressed development objects → atomic revision chain → causal combined
bundle +fold membership +qualification. No collector/network/launchd mutations; повторный
CLI sync подключает только завершённые новые окна. Один registry pin-ит одну calendar
plan/cohort/target semantics; другие источники требуют нового reviewed contract.
Holdout metadata-only quarantine отделён от development exporter; research gate закрыт.
Audit воспроизводит report/folds без calendar root, по pinned objects. Installed wheel
work/registry-d45/installed-wheel даёт exact audit/export. Raw holdout не дублируется;
последующее source availability/semantic release — отдельный этап. [D45](../docs/corpus-registry.md).


## D46: research contracts без изменения collectors/ML artifacts

Новый stdlib memecoin_research: frozen spec/source fingerprint, будущий calendar
materializer,114slot trial planner, строгий validation-only selector, role support
assessment и result-summary guard. CLI не содержит training/release/network действий.
D44/D45 source identity сохранена; assessment воспроизводится из installed wheel.
Current D45 adapter намеренно не подтверждает F01 sampling binding. Generic positive
checks/review fixture остаётся non-executable; formal launch/release receipts ещё нет.
Следующий D47 обязан реализовать trial/exposure ledger, fold-aware admitted runner,
masked day scoring/block inference и проверку budget/resume на fixtures. [D46](../docs/research-readiness.md).
