# История изменений решений

История концепции восстановлена из обсуждения при создании памяти 2026-10-01. Точные даты двух предыдущих reviews отдельно не зафиксированы; ниже используется порядок обсуждения, а не выдуманная хронология экспериментов. Экспериментов не было.

## Исходная пользовательская концепция

Предполагались collector → historical dataset → simulator → pretraining → token selection → trading/RL → out-of-sample/walk-forward → paper. Рассматривались individual encoders, cross-token attention, portfolio state/memory, policy/value heads; действия SKIP/ENTER/HOLD/REDUCE/EXIT плюс size. Пользователь прямо просил критиковать любые предположения, включая необходимость RL.

Цель — самостоятельная обучаемая модель на PyTorch, два M4 Pro по ~24 GB, вероятно Solana, Axiom лишь рассмотрен. На старте без реальных денег. Упоминание «LLM» в конце исходного запроса уточнено как независимая торговая ML-модель, не языковая модель.

## Первый архитектурный review

Предложены shared TCN + heads, отдельный portfolio/risk/execution, отсутствие обязательного RL, target positions и conservative simulator. В качестве стартовых примеров указаны 1–5M параметров, top-K 100–300, решения 5–15 s, horizons 1/5/15 min, один тип пула и более поздняя стадия жизни токена. Cross-token aggregates и ограниченные wallet features предлагались раньше сложных attention/GNN.

Эти значения были предположениями, не результатами измерения. Следующий review существенно ослабил их нормативный статус.

## Второй критический review и текущая база

| Ранее | Теперь | Причина |
|---|---|---|
| TCN основной стартовый backbone | TCN, GRU, небольшой Transformer сравниваются | Нет доказательств превосходства на наших данных |
| Размер 1–5M | Scaling от ~0.5M до 30M; 100M условно | Размер должен следовать данным, diversity и learning curves |
| Forecast + optimizer как достаточная основа | Обязательное рассмотрение sequential MPC baseline | Однопериодность теряет стоимость будущего капитала |
| RL просто отложен | Конкретный условный опыт recurrent PPO после imitation; IQL при подходящем dataset | Разделены interactive simulator и logged offline data |
| Жёсткая модульность | Возможное partial joint decision training после pretraining | Промежуточные targets могут ограничивать полезность |
| Дешёвый scanner на 100–300 | All-token lightweight encoder равноправен; K экспериментален | Scanner может потерять лучшие ранние возможности |
| Агрегаты как раннее ограничение | Pooling/attention проверять рано | Возможна значимая миграция капитала и групповой контекст |
| Фиксированная сетка позиций | Сетка как baseline, adaptive/continuous optimizer позднее | Учесть quantization против jitter/turnover |
| Несколько фиксированных future returns | Action-conditioned net outcomes + рыночные targets; horizons сравнить | PnL требует continuation policy и исполнения |
| Решения 5–15 s | Определить из arrival/execution latency и decay edge | Возможно слишком медленно; данных для выбора нет |
| Regular sequence для TCN | Hybrid bars/events как гипотеза | Агрегация теряет порядок быстрых событий |
| Простые wallet features | Soft clusters → graph features → локальный embedding | Промежуточный вариант до большого temporal GNN |
| Ограниченный рынок целиком | Широкий сбор, узкая достоверная симуляция | Не исключать потенциальный edge вместе с удобством реализации |
| TCN + heads как возможный конечный вид | Гибридная V2/V3 по ablation | Архитектура должна развиваться по измеренным bottlenecks |

Сохранены: приоритет достоверности исполнения, независимые risk limits, строгая временная оценка, отсутствие гарантированной прибыли, optional RL, champion/challenger вместо автоматического promotion.

## 2026-10-01 Создание памяти проекта

Пользователь принял последнюю концепцию как текущую базу и поручил создать локальный Git-репозиторий и Memory Bank в добавленном проекте `memecoin-ai`. Реализацию явно отложил до своего подтверждения.

Выбран Markdown + перекрёстные ссылки + стабильные ID + Git. GraphRAG отложен как неоправданная сложность для небольшого корпуса. Созданы цель, архитектура, решения, эксперименты, требования к данным/исполнению, evaluation/retraining, roadmap, status, история и references. Содержание оформлено как самостоятельный контекст для новых сессий.

Проверены 13 Markdown-документов: непустое содержимое, закрытие code fences, все внутренние файловые ссылки, последовательность D01–D17 и E01–E14. Ошибок не найдено. Локальный Git инициализирован с веткой `main`; remote не создавался. Проверены исключения секретов, данных и тяжёлых результатов и отсутствие whitespace-ошибок в Git diff. Историю snapshot и hash коммитов читать непосредственно из Git. Код торговой системы не создавался.

## 2026-10-01 Начало разрешённого архитектурного прохода

Пользователь разрешил автономную реализацию фундамента и push в существующий origin/main. Прежняя пауза до подтверждения снята. Выбран Python 3.11+ со stdlib в runtime и pytest/ruff/mypy для проверок. Первые компоненты: неизменяемые события с observed/assumed/unknown availability, SQLite журнал, content-verified bundles и point-in-time observations. SQLite — локальный reference store, не решение для полного потока Solana.

## 2026-10-01 Reference replay и бухгалтерия

Реализованы две временные линии, immutable observations, integer CPMM reference, reservations/settlement, отдельный risk manager, target-to-intent conversion, seeded synthetic outcomes и консервативная ликвидационная оценка. Реальный origin и неизвестная availability отклоняются. Выход за impact-domain делает весь replay недействительным, не превращается в удобную simulated failure. Точные ограничения — `docs/contracts.md`. Это проверка инфраструктуры, не E01–E14 и не калибровка Solana.

## 2026-10-01 Research contracts и воспроизводимый запуск

Добавлены отдельные Features/Target, зрелость labels, purged walk-forward, train-only standardizer, unconditional empirical forecaster и pinball score, block bootstrap, promotion blockers. CLI связывает fixture → bundle audit → reference replay → run manifest/metrics/checksums. Отрицательный synthetic round-trip используется как инженерная проверка; прибыльность не выводится. Backbone, neural training и действительная разметка рынка не выбирались.

## 2026-10-01 Ограниченная проверка RPC

Добавлен read-only RPC client с allowlist, timeout/size limits, raw receipts и редактированием чувствительных endpoint errors. Публичный mainnet endpoint ответил на getVersion/getSlot/getFirstAvailableBlock; локальные raw captures вне Git. Это подтверждает только связность: market_dataset_qualified=false, historical arrivals неизвестны. Qualified pool decoder, реконструкция account state и live/history audit остаются следующей практической зависимостью.

## 2026-10-01 Аудит и завершение локального фундамента

Добавлены causal sampling/label reference, lock-файл dev tools, Make targets и CI definition. Аудит исправил disappearance-during-latency, immutable run finalization, source snapshot provenance, strict schema/duplicate JSON checks и конфликт одинаковых pool versions. Проверены 45 тестов, strict mypy, Ruff, CLI smoke с идентичными повторными результатами, sdist/wheel build. RPC проверка реальная, market qualification не выполнена. Документы синхронизированы с кодом; добавлены D18–D21. Это инженерные результаты, не доказательство торгового edge.

## 2026-10-01 Квалификация реального CPMM adapter

Реализованы pinned layout decoder, единый finalized account checkpoint, bounded signature/
transaction audit, offline verification и сверка vault transfers с pre/post balances.
Добавлены компактные реальные regression captures. Первые RPC запросы с max version 0
пропускали v1; после проверки официальных docs добавлено чтение v1. Legacy-only domain
оказался узким: добавлен структурный Token-2022 inventory, но исполнение extensions не
объявлено поддержанным. SOL/USDC используется только как контрольный pool, не как memecoin
research segment. Состав и ограничения проверок зафиксированы в docs/source-audit.md.

Финальный аудит: 67 тестов, Ruff/strict mypy, repeatable synthetic smoke, offline real
checkpoint/inventory/flow reproduction и sdist/wheel сборка. В новые qualification specs
добавлены hashes локальной реализации decoder; старым captures они не приписываются задним
числом. Исторический coverage gate остаётся открытым, исполнение не объявлено калиброванным.

## 2026-10-01 Durable forward collection и восстановление

Пользователь утвердил ограниченный непрерывный сбор и сверку между snapshots. Добавлены
fixed-cohort polling, SQLite/WAL raw journal, атомарные pagination cursors, pending transaction/
block jobs, source-error history и возобновление после перезапуска. Аудит использует реальный
порядок getBlock и проверяет каждый pre-balance плюс конечные vault residuals. Неподдержанные
переходы и отсутствие checkpoint остаются blockers. Synthetic-only execution gate сохранён.

Практическая корректировка: page size 5 слишком затратен для активного пула после паузы;
добавлен регулируемый размер до 100 и reuse идентичных finalized pages между интервалами.
Найдена и исправлена ошибка передачи recovery параметров CLI; отдельный тест проверяет
фактические запросы. Прежние receipts/ошибки не переписаны. HTTP status теперь можно записать
без endpoint/credential; старым ошибкам неизвестный статус не приписывается.

## 2026-10-01 Результат реального forward pilot

10 checkpoints / 8 интервалов / 189 транзакций (46 successful, 143 failed), 15 blocks.
После дозагрузки нет pending scans/transactions/blocks; все промежуточные pre-balances
согласованы, terminal residuals равны нулю. Сохранены gap-open и recovered evidence, 56
issue records, включая HTTP 429; старые ошибки не удалены. Архив воспроизведён без сети
двумя установками; повторное recovery не делает запросов. Пройдены 82 теста и проверки
сборки. Рыночная полнота, full pool state, execution calibration и edge не объявлены.

## 2026-10-01 Протокольная реконструкция CPMM и quote checks

Добавлены отдельная integer venue math и CLI audit-protocol. 44 mainnet swap совпали
с events/transfers до atomic unit; fee counters и internal LP supply совпали между
checkpoints во всех 8 интервалах. 143 failures применяются атомарно, два успешных маршрута
не считаются swaps. Строгое предположение «ровно 13 swap accounts» скорректировано: pinned
handler допускает неиспользуемые trailing accounts; обнаруженный no-op router допустим
по ownership/CPI правилам. Raw accounts и старые reports не переписаны.

Source-derived liquidity/collection/creator math покрыта тестами; реального подтверждения
этих переходов пока нет. Дополнительная выборка 24 tx и четыре pool reads показали
Token-2022 у всех creator-fee кандидатов. Это основание для следующего ограниченного
extension этапа, а не для снятия strict gate сейчас. 126 тестов, smoke, build и одинаковые
аудиты из dev/wheel прошли. Memory Bank сохраняет различие evidence и предпосылок.

## 2026-10-02 Token-2022 и обнаруженный withheld harvest

Пользователь утвердил следующий этап. Реализованы extension whitelist, transfer-fee
ceil/cap/inverse math, explicit schema 2 с Clock, gross/net quote checks и withheld ledger.
Главная корректировка по mainnet: withheld регулярно harvest-ится в mint при неизменных
spendable reserves. Добавлена эта операция, другие withdrawals остаются неподдержанными.

Новый pilot: 132 creator-fee swap, 123 с transfer fees, 8 harvest, 157 failed, 85 no-op;
все 6 endpoint состояний совпали. 19 rate limits и один transport error сохранены,
recovery завершён. Fixed pacing оказался недостаточным для getBlock на public RPC;
добавлены bounded cooldown и adaptive spacing без скрытых повторов и снижения gates.
160 тестов и offline/wheel reproduction прошли. Калибровка редких LP/collection/epoch
переходов и независимая полнота источника не объявлены завершёнными.

## 2026-10-02 — История параметров и целевой поиск редких переходов

- D28: versioned opt-in dependency scans, captured EpochSchedule/Clock, ordered
  config/transfer-fee/supply transitions; unknown mutations fail closed.
- D29: после реальной высокой стоимости общего config добавлены full-block и
  bounded-range recovery. Незавершённая 100-request pagination сохранена; диапазон
  восстановлен 13 дополнительными запросами без фиктивного закрытия cursors.
- D30: 13 real LP witnesses; event-provided prestate явно отделён от независимых
  checkpoints. Найден external mint-withheld withdrawal, mint-wide суммы неизвестны.
- Два новых интервала: 455 + 863 tx, совпадение economic/withheld/parameters; сами
  выбранные pools новых успешных операций не имели. Edge и новые quotes не заявлены.
- Защищены immutable parsed cache и конфликтующие same-bank mint checkpoints.
  Старые архивы остаются совместимыми. Подробности и provenance — docs/parameter-history.md.

## 2026-10-02 — Read-side latency, второй оператор и fund collections

- D31: exact-request comparison и sealed audit bundles. Попытки PublicNode (403) и
  OnFinality (429) сохранены как незавершённые планы; независимой сверки фактически нет.
- D32: isolated mainnet Clock run, 24 requests без ошибок; response ~250 ms существенно
  отличается от finalized Clock age ~9.7 s. Это не execution calibration.
- Целевой поиск protocol/fund/creator authorities вместо новых случайных swaps:
  3 real collect_fund_fee witnesses, включая Token-2022 gross/net deductions.
  Согласование transfers не подменяет неизвестный counter/tariff prestate (D30).
- 209 тестов, smoke/build и dev/wheel reproduction; synthetic-only/market gates сохранены.
  Следующий внешний dependency — доступный второй оператор для block-range verification.

## 2026-10-02 — Повторный preflight остановлен отсутствующей переменной

Пользователь сообщил о новом secondary RPC, но его endpoint не доступен окружению
Codex. CLI вернул exit 2 до сети; это не новый HTTP-отказ провайдера. Пользователь
уведомлён сразу, запрошен путь настройки без credentials. Независимая сверка остаётся
незавершённой; код и исследовательские gates не менялись.

## 2026-10-02 — QuickNode отвечает на getSlot, getBlocks даёт HTTP 413

После предоставления расположения `.env.local` preflight прошёл. На первом запросе
диапазона 12 slots QuickNode вернул HTTP 413 (request 103 bytes). Причина неизвестна,
HTTP body отсутствует; серия остановлена, пользователь сразу получил безопасный лог.
План из 13 запросов сохранён, agreement не получено. Crosscheck теперь прекращает
оставшийся план при любой ошибке запроса (D31 уточнено), не только 401/403/429.
212 tests прошли; credentials исключены из Git.

## 2026-10-02 — Chunked getBlocks и новый transport failure

По разрешению пользователя диапазон 12 слотов разбит 5+5+2 без upgrade. Добавлены
crosscheck-range и audit-provider-range, raw provenance и offline reproduction.
Первый mainnet chunk вернул SourceError без HTTP response; остальные запросы остановлены,
пользователь сразу получил лог. HTTP 413 из прежнего запуска не переписан. Safe transport
categories добавлены для будущей диагностики, прежняя причина осталась unknown.
227 тестов, smoke/build и wheel verification прошли; data/execution gates не повышены.

## 2026-10-02 Диагностика QuickNode через MCP и прямой RPC

MCP Console API/Tooling Access вернули 401 тарифного доступа. Прямой getBlocks
в sandbox дал dns failure; после сетевого разрешения первая часть прошла.
Отдельный 30s план получил timeout. Новый 60s план полностью перечислил 12 slots
по 5 + 5 + 2, три full blocks совпали; четвёртый получил timeout, серия остановлена.
Причина старого SourceError неизвестна; 60s не объявляется устранением нестабильности.
Добавлен CLI `--timeout`, default 10s сохранён. 227 tests, lint/typing, smoke и три
offline bundle verification прошли. Старые captures не изменены. Evidence:
docs/evidence/quicknode-diagnostic-2026-10-02.json. Полного agreement и qualification нет.

## 2026-10-02 Helius preflight и строгое сравнение результатов

Проверены и отдельно сохранены коммитом ранее незакоммиченные timeout/QuickNode
изменения; 227 исходных тестов и три legacy bundles прошли повторную проверку.
По поручению переход на Helius: документация MCP доступна, единственный chain
preflight получил timeout getSupply; дальнейшая серия остановлена. Helius endpoint
в доступном окружении/проекте не найден, chain data получено 0, совпало 0/12 blocks.
Safe log, MCP receipts, manifest, source snapshot и deferred direct plan сохранены.

Offline reference: 12 blocks / 14 809 tx, metadata и ancestry согласованы; frozen
journal integrity ok. Новые comparison schema 2 проверяют полный result и полноту
обязательных metadata, schema 1 оставлена для архивов. Три прежних QuickNode blocks
совпали и по schema 2 без новых сетевых запросов. В allowlist добавлен getGenesisHash;
планы фиксируют timeout/byte cap/spacing, останавливаются на non-match.
239 tests, lint/format/typing, smoke и sdist/wheel build прошли; build потребовал
сетевого разрешения после sandbox DNS error. Helius не квалифицирован, ML/trading
не начаты. Следующий шаг — локальный авторизованный Helius RPC URL и bounded
16-request plan; подробнее docs/helius-audit.md.

## 2026-10-02 Прямой Helius: enumeration совпал, logs обрезаны

После настройки локального endpoint проведён bounded direct preflight/range plan.
Sandbox DNS failure сохранён отдельно; после штатного разрешения mainnet genesis,
version и finalized slot подтверждены, enumeration совпал 12/12. Первый full block
(1 310 tx, 10 011 629 bytes, 5.373 s) остановил серию: у двух tx logs с marker
Log truncated. 1 308 tx совпали целиком; identity/order и остальные поля совпали.
Целевой getTransaction также обрезан, плюс rewards=[] вместо block null; raw сохранён.
Всего шесть успешных HTTP requests, 11 других blocks не запрошены; agreement false.

Добавлены schema 3 (marker guard, bounded difference paths), offline compare-rpc-captures,
реальный неизменённый getTransaction fixture и regression tests. Старые schemas и
архивы воспроизводятся без переписывания. 244 tests, lint/format/typing, smoke и sdist/wheel build прошли.
Обновлены Memory Bank и docs/helius-audit.md, evidence/helius-direct-2026-10-02.json.
Следующий шаг — проверяемый доступ к полным historical traces; новые сетевые серии
остановлены до устранения metadata blocker. Qualification/ML/trading не повышались.


## 2026-10-02 Historical Helius traces и отдельная проверка активного блока

Четыре targeted historical calls для двух signatures (json и indexed full) подтвердили
ту же обрезку. У untruncated LaserStream нет historical replay по официальной документации.
Проверенный доступ признан непригодным для полной trace-сверки; остальные способы
использования не отвергнуты. D32 фиксирует отдельный bounded cohort audit без снижения
strict/replay gates. Общая offline scope диагностика и 8 tests добавлены.

Первый активный block 452408963 совпал целиком: 1 154 tx и ancestry. Накопленный итог
1/2 full blocks, 2 462/2 464 tx; 10 blocks ещё не получены, full-range false. Concurrent
state pair остановлена на public RPC timeout; Helius ответил, но сравнения нет.
6 успешных Helius calls / 8 318 302 bytes на этапе, один public timeout, no retries.
Дополнительная загрузка docs .md получила TLS handshake timeout; HTML прочитан отдельно.

252 tests, lint/format/typing, smoke/build passed; manifests и offline diagnostics
проверены. Memory Bank, docs/helius-audit.md и evidence/helius-traces-2026-10-02.json
обновлены. CPMM/replay/collection, прежние captures и secrets не менялись. Далее —
полная независимая история frozen cohort; production/ML/trading qualification нет.


## 2026-10-02 Закрыт второй RPC; начат и проверен economic dataset

По прямому поручению пользователя D33 закрывает исходную задачу второго RPC с
наблюдаемыми ограничениями, отменяя обязательную дозагрузку 10 blocks из D32.
Сохранены evidence, full-range=false, truncation и public timeout; новых RPC нет.

D34 реализует offline dataset exporter/verifier поверх existing protocol reconstruction:
coherent journal snapshot, state tape, raw provenance, ordered fees/epoch, whole-segment
quarantine, явная unknown historical availability. Добавлены conditional size probes
с Token-2022 fees и no-free-roundtrip invariant; старый replay gate не менялся.

Реально построены и воспроизведены четыре bundles: 16 intervals, 5 pools, 1 889 tx,
1 921 states, 11 430 conditional quotes + 96 unavailable. Оригиналы неизменны;
это повторное использование audit data, не новые независимые market observations.
264 tests, lint/format/typing, smoke/build, CLI и wheel verification прошли.
Memory Bank, README и docs/economic-dataset.md обновлены; provenance/results:
docs/evidence/economic-corpus-2026-10-02.json. Дальше causal observation/features,
gap masks и bounded forward data. ML/RL/paper/live не начаты; edge не установлен.


## 2026-10-02 Causal observations/features и реальный bounded forward corpus

По утверждённому D35 реализованы общая live/offline CausalBook, 12 признаков, freshness/
gap masks, измеренные receipt/decode/publication timestamps и temporal guards. Collector
с immutable plan/raw/code/error records ограничен бюджетом и останавливается на первом
сбое. Targets имеют maturity/purging/censoring; training не запускался.

Реальный Helius run: 31 success / 32 requests из max 36, 112 971 bytes. На round 10
transport timeout 10.006 s, HTTP/RPC code отсутствуют; retries нет. 33 feature rows,
29 usable, 3 warmup/1 timeout masks. Корпус сохранён как incomplete, не замаскирован pass.

QA выявил отличие публикации feature frame от нового snapshot endpoint. Schema 2
требует fresh post-horizon input и новый slot; old schema 1/raw/frames не переписаны.
Отдельный derived artifact: 8 calibration, 4 engineering evaluation, 7 purged, 14 censored.
Проверены prefix invariance, late input, maturity, same-bank conflict, gap recovery,
late/repeated snapshots, actual source hashes и offline/wheel reproduction. 280 tests,
lint/format/typing, smoke/build прошли. Memory Bank, CLI, docs/causal-forward.md и
соответствующий evidence обновлены. Далее — более длинные prospective segments,
затем execution-conditioned targets; no ML/RL/paper/live/edge claims.

## 2026-10-02 — D36: bounded campaigns и условные execution targets

Добавлены resumable immutable segments с общим бюджетом, source pinning, crash sealing,
OS lock, warmup/censoring на границах и ограниченным возобновлением transient failures.
Offline size/delay/minOut/holding probes используют existing CPMM/Token-2022 math;
failed exit сохраняет позицию, unknown full net PnL остаётся null.

Собрано 115 valid snapshots в двух прерванных кампаниях; два HTTP 200 context/Clock
mismatch остановили сеть. Один контрольный запрос между кампаниями прошёл; systematic
cause неизвестна. Raw regression fixtures сохранены. 120 features / 112 usable,
960 target variants (432 conditional roundtrips, 4 entry/8 exit failures, 516 censored),
zero engineering evaluation decisions. 297 tests, smoke/build, offline/wheel reproduction.
Memory и docs/evidence обновлены. ML не запускается; длительный corpus остаётся
неполным до решения current-checkpoint coherence blocker. Historical RPC audit не открыт.

## 2026-10-02 — D37: coherence диагностика и новый protocol-support blocker

Подтверждён strict Clock/context contract по Agave; public RPC прошёл шесть preflight
проверок, Gatekeeper timeout. Новый public prospective запуск coherent, но отклонён из-за
unknown AmmConfig reserved word@116=50000. Classic cohort с другим config дала тот же
отказ; pool search/network collection остановлены. ProgramData metadata read timeout,
смысл bytes и binary/source equivalence неизвестны. Старые два Helius mismatch не
исправлены и не замаскированы; extended economic corpus остаётся незавершённым.

Добавлены campaign origin pinning/closure guard, отдельная checkpoint diagnosis,
independent corpus-quality report и реальные regression captures. 313 tests, smoke/build,
offline/new wheel QA; старые D36 bundles повторно verified pinned wheel. Новых accepted
forward observations 0; preflight не превращён в прошлые features. Следующий gate —
поддержанная deployed семантика config. Memory/docs/evidence обновлены, ML не начиналось.


## 2026-10-02 — подтверждён AmmConfig creator fee share, bounded resume D38

- Official SDK/docs объяснили word@116: default доля creator fee для protocol при collection;
  swap quotes не меняются. Helius подтвердил оба config и ProgramData upgrade metadata;
  binary equivalence и override PDA неизвестны. Прежний D37 unknown-layout вывод уточнён.
- Explicit snapshot/quote profile2, remaining-padding/range/Clock guards, metadata с
  unknown effective rate и запрет legacy replay. Versioned CLI diagnostics. 327 tests.
- Public prospective campaign с неизменяемым планом: 2 accepted /4 requests, два timeout
  после одного cooldown/resume. Кампания закрыта, 6 masked rows /48 censored scenarios,
  нет mature labels. История запросов/ошибок/raw сохранена, бесконечных retries нет.
- Dev/installed-wheel verification, smoke/build прошли; D36/D37 frozen artifacts также
  воспроизведены прежними wheels. Следующий blocker — доступность coherent source.
  Training, RL, реальные транзакции и платные подключения не запускались.


## 2026-10-02 — persistent TLS, real restart и target support D39

- Диагностика отделила как минимум connect/TLS timeout от RPC/account semantics.
  UA оказался недостаточен. Canonical public host + verified persistent connection
  выбран для bounded сбора; Helius integrity guards и старая история сохранены.
- Transport profile закреплён в plans; no redirects/hidden retries, response bounds
  и deadline, phase/reuse measurements. Реальный restart между сегментами проверен.
- 482 accepted snapshots, 464 usable features, 345 mature decisions (141 evaluation).
  Один headers connection error сохранён; второй run 180/180 completed.
- Новый offline audit полной scenario grid и overlap: 307 complete decisions,
  15 chronological nonoverlap windows. 346 tests, smoke/build, dev/wheel reproduction.
  ML/edge не объявлены; далее новые календарные окна и execution uncertainty QA.

## 2026-10-03 — handoff в Astra и согласование пилота Alchemy (D40)

- Пользователь сообщил о подключённом в другой сессии Alchemy MCP, фактически рабочем
  HTTP RPC и доступном WSS; в этой сессии Alchemy runtime/raw evidence не проверялись.
- Согласован bounded пилот HTTP+WSS параллельно существующему pipeline. Миграция не
  принята. Зафиксированы baseline на том же провайдере, strict coherence/availability,
  handoff/recovery/failure tests и критерии практического выигрыша. Пилот не проведён.
- Разработка переносится в Astra. Код, raw corpora и runtime не менялись; последнее
  фактическое тестирование кода остаётся 346 tests из D39. В итогах давать ссылки на
  документ этапа и evidence. Сохраняются запреты на реальные сделки/оплату и ML gates.

## 2026-10-03 — D40, план и HTTP preflight

Заморожен bounded Alchemy HTTP/WSS план до runtime requests. MCP и локальный доступ
разделены; genesis +6 strict snapshots прошли. Локальная ошибка отчётности сохранена
отдельно, бюджет 7 не расширен. Реализуются bounded raw WSS и conservative snapshot
triggers поверх существующих decoder/CausalBook/targets; миграция не принята.

## 2026-10-03 — D40 v1 завершён без миграции

Два 120s paired runs, реальные reconnect и restart: Alchemy144+72 snapshots, public144,
preflight 6;366 strict passes и1158 raw WSS messages. Dev/wheel/offline и старый D39
воспроизведены,364 tests/setup/smoke/build прошли. HTTP calls−50%, но usable95,83→80%,
payload−0,66%; заданный criterion не выполнен. Config noise и coarse min-interval
scheduler мешают приписать выигрыш WSS. D39 primary сохранён; следующая кандидатура —
отдельно замороженный v2 с matched5/10s controls и steady/stress окнами. Raw/gaps/availability,
protocol whitelist, synthetic-only и запреты ML/RL/paper/live не менялись.
Итог: docs/alchemy-pilot.md; evidence: docs/evidence/alchemy-pilot-2026-10-03.json.

## 2026-10-03 — D40-v2 заморожен

По новому поручению пользователя разрешён выбор роли WSS и следующий логичный этап.
Исправлены absolute scheduler/content coalescing, frozen matched10s + adaptive/5s
controls, раздельный steady/symmetric stress, общий feature grid. 372 tests прошли;
до нового сбора критерии/budgets сохранены в configs/alchemy-pilot-v2.json.

## 2026-10-03 — D40-v2 решение и старт D41

Initial startup EOF сохранён, один preregistered serial-ack follow-up завершён. Matched
cadence/common features и content coalescing проверены; criterion не выполнен из-за
payload overhead. WSS оставлен optional/off; Alchemy HTTP5 выбран для нового bounded
corpus D41 (180 max, stop first error) поверх прежних decoder/collector/targets.


## 2026-10-03 — D41 закрыт на первом cold connect timeout

Первый запрос15,0718s, HTTP=null, snapshots0; retries/второго сегмента нет. История и
closure сохранены. Existing targets/quality/support воспроизведены dev/wheel:3 masked
rows,24 censored scenarios,0 mature/nonoverlap. CLI stop guard теперь блокирует также
crash до записи closure; пять synthetic tests. 377 tests/check/smoke/build прошли.
WSS optional/off, Alchemy HTTP strict correctness не равна квалификации длительной
availability. Далее cold-connection observability и новые календарные окна по новому
плану; текущую закрытую кампанию не возобновлять. ML/RL/paper/live не запускались.


## 2026-10-03 — D42: завершение transport tuning и два календарных окна

- Frozen diagnosis4 connections/8 strict checkpoints, все успешны. DNS/TCP/TLS
  observer не менял trust/deadlines/RPC; причина D41 timeout остаётся unknown.
- Alchemy HTTP persistent TLS выбран по заранее заданному правилу. Два prospective
  окна с creation separation 599,994084s: 360 accepted, 348 usable, 268 mature (135 evaluation),
  12 nonoverlap windows. WSS off, без fallback, retries или изменений CPMM/targets.
- Offline conditional cost/delay uncertainty с explicit assumptions; censored/unfilled
  отдельно, no calibrated admission/costs/net PnL. 386 tests/check/smoke/build и exact
  dev/installed-wheel reproduction. D40/D41 проверены, прежние bytes неизменны.
- Далее календарное coverage разных часов/дней и read-only execution evidence, без ML.
  Документ docs/cold-connection-corpus.md, evidence рядом в docs/evidence/.

- Strict calendar audit выявил недобор5,916ms между created_ns планов; actual segment
  starts разнесены600,037526s. Ошибка anchor исправлена только для будущих запусков,
  критерий текущего run оставлен false; данные не переписывались и не пересобирались.
