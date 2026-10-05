# Historical vacuum: измеренный результат 2026-10-06

[Forensic review](brain-experience-review.md) · [Протокол до RPC](brain-market-vacuum.md).
Exploratory Brain track; F01/D43 holdout не читался. Действующий AUTO не изменён.

## Полученные данные

| Дорожка | RPC / nominal CU | Raw результат | Результат проверки |
|---|---:|---|---|
| PumpSwap program, 4 даты | 4 / 400 | 0 tx | Program index не квалифицирован |
| Address controls | 8 / 680 | 400 tx | Standard core cross-checks совпали |
| Migration authority probe | 12 / 960 | 800 tx | 199 записей о создании pools |
| Monthly migration authority | 54 / 5400 | 5400 tx, 90.79 MB, 58.30 s | 865 birth pools, 17 месяцев |
| Earliest pool/month history | 51 / 5100 | 4919 tx, 88.70 MB | 1236 swap candidates, 8 pools |
| Shared fee-account streams | 18 / 1800 | 1800 tx, 38.99 MB | 243 swap candidates, 97 pools |

Всего **147 RPC / 14 340 nominal CU**. Это локальная оценка по официальным weights,
не billing delta: MCP usage снова немонотонен. Owner attestation/no-PAYG и credential
fingerprint совпали; оплаты, signup и upgrades не выполнялись.

Monthly captures содержат также 104 reconciled swaps на 65 pools. После глобальной
дедупликации трёх campaigns: **1579 events, 167 swap pools, 865 birth pools,
965 разных markets**, 1583 provenance observations. Четыре повторных события
сохранены как дополнительные ссылки на raw evidence.

Canonical store: `work/brain-review-20261006/corpus01/corpus.sqlite`, sealed binding/report.
Это 965 markets в historical registry/event corpus, **не 965 train markets**.

USD Brain по-прежнему имеет **24 train markets, 93 UTC days, 89504 train decisions до
cap / 48000 после cap**. Дополнительных training-ready decisions из raw lane: **0**.
Обученный на расширенном corpus candidate не создан. Существующий automatic candidate
продолжает future paper. Acquisition существенно продвинулся; цель расширить именно
train в этом этапе ещё не достигнута.

## Качество и ограничения

Migration authority даёт historical cohorts без отбора сегодняшних победителей.
Остаётся graduation/authority bias: не представлены токены, не дошедшие до migration,
и не доказана полнота venues/fee routes. Birth metadata проверяет точный layout
instruction/event, CPI parent, фиксированные event authority/global config, создание
pool account, mint/vault identities. Initial liquidity accounting не квалифицирована.
Отсутствие pool сегодня само по себе не означает rug или смерть рынка.

865 births распределены по 17 месяцам между 2025-04 и 2026-09. За ноябрь 2025 в первых
трёх страницах admitted births не было. Это выборки 18 дат, не 18 полных месяцев.

Historical swap profiles из pinned official IDL позволили проверить старые swaps через
integer transfers, arguments, event amounts и итоговые vault balances. Неизвестные
suffix, Token2022 extensions и remaining accounts остаются в quarantine.
Дополнительная проверка точной длины instruction ужесточила предварительные результаты:
pool events 1353 → 1236, fee events 293 → 243. Старые отчёты сохранены; финальные
`*-swaps02` имеют приоритет.

Экономика выборки: migration discovery — 0.1602 birth pool/CU, около 105 kB/birth;
pool hydration — 0.2424 swap/CU; fee index — 0.135 swap/CU и 97 pools за 18 GET.
Это измерения коротких выборок, не устойчивая пропускная способность и не доказательство
полноты provider index. Все 18 monthly ranges закончились на caps с оставшимся gap debt.

Pool history дала лишь 19 quote-bar candidates, fee stream — 97. Они неполные.
WSOL/USDC units разделены; quote price не подставлен вместо USD price. Прежде чем
скачивать год в большом объёме, нужно решить coverage/admission: иначе вырастет число
неполных баров, а не training-ready corpus.

## Реализованная архитектура

`IndexedRPC → immutable capture → reservation journal → versioned decoder →
canonical event shards → single-writer SQLite → immutable corpus snapshot`.

Сохраняются cursors и незавершённые диапазоны после каждой страницы, raw provenance,
receipt и durable availability. Reservation переживает crash и остаётся charged.
Неоднозначный запрос не повторяется автоматически. Первый provider/schema failure
создаёт STOP. Глобальный ключ события — signature + instruction path; противоречивые
версии отвергаются. Есть bounds на pages/bytes/deadline и OS lease watchdog.

Здесь реализована **indexed pagination, не WebSocket subscription**. Campaigns завершены;
новый 24/7 vacuum LaunchAgent не установлен. B08–B13 компоненты переиспользованы.
Новый corpus строится в отдельном каталоге; старые outputs не открываются для записи.

## Загрузка dataset

Streaming writer читает отсортированный SQLite cursor без загрузки всего архива в RAM.
Overlap 14 past / 3 future сохраняет существующие признаки и targets на границах shards.
На реальном NEW snapshot: **91472 context samples, 44 shards, 3.504 s**;
peak RSS — 695435264 bytes на этой машине. Это измерение writer, не скорости training.

Lazy LRU reader проверяет pins. Для случайных minibatches добавлен read-only float32
memory-map cache, чтобы не разбирать JSON заново на каждом draw. Cache не выбирает
train membership и не меняет scaler. Проверены gaps, shard boundaries, repeated draws,
NaN targets и повреждённые bytes. Старый B13 trainer пока не переключён на этот путь.
Измерение реального cache — `tensor-cache01-measurement.json`.

## Retraining

Зарегистрирована shadow policy `information-support-v1`:

- Normal: ≥1000 действительно новых train-eligible IDs, effective pools ≥8,
  new pool-days ≥8, disjoint windows ≥128, минимум 1 час после reservation.
- Fast: ≥20000 / 50 / 50 / 1000 соответственно, минимум 10 минут.
- Quota, source/domain STOP, freshness и exact snapshot pins остаются обязательными.
  Missing/invalid metrics закрывают gate.

«Новые» сравниваются со **всеми** прежними eligible train/validation/test IDs. Перенос
старого validation в train не считается новым опытом. Первый суточный прирост
2082 / 9.98 / 11 / 216 прошёл normal branch; момент самого раннего срабатывания не измерен.
Это proxy нового support, не оценка mutual information.

**Работающий AUTO сохраняет 24h + 100 matured и лимит 6 reservations.** Новый gate
пока shadow: raw registry не является его учебным входом, cutover не проведён.
Control Center не показывает 865 как train markets; diversity audit сохранён отдельно.

## Следующий этап

1. Закрыть historical ranges для нескольких cohorts 2025: проверить все fee routes
   на matched pool controls, непрерывность coverage и отсутствие пропущенных trades;
   затем квалифицировать quote/USD normalization.
2. Получить авторитетное описание фактических layouts 2026 и token extensions.
   Не отрезать неизвестные 8 bytes.
3. Admit большой dataset, обучить один candidate с прежним recipe и провести future
   paper. После qualification переключить loading path и diversity trigger.
4. Отдельно сравнить deterministic/MPC-like WAIT/ENTER/HOLD/EXIT с fixed15 на одном
   будущем рынке: updated forecasts, net return, downside, costs/liquidity. RL сейчас не нужен.

Для конкретной монеты из Axiom нужен verified pool/mint → venue/quote/vault mapping,
затем тот же address range/cursor ingestion. Axiom API не предполагается; специальный
watch-market UI и непрерывная on-chain subscription пока не реализованы.

Mac B позднее может читать immutable corpus/shards и возвращать sealed model bundles.
Общих mutable SQLite writers нет. LLM node может читать summaries/events, но не менять
admission, promotion или risk. Установка LLM и multi-Mac сейчас не выполнялась.

## Эксплуатация

Control Center: http://127.0.0.1:8792. На финальном срезе AUTO/waiting,
source healthy, domain_stop=false. Source expiry: **2026-10-07 12:23:51 UTC**;
operator bound: **2026-10-11 20:31:13 UTC**. Сроки и бюджеты не продлевались.

Старые STOP/incidents и virtual books сохраняются. Новые инструменты принимают
отдельный output root: `brain_vacuum.py`, `brain_swap_audit.py`, `brain_corpus.py`,
`brain_shard_snapshot.py`, `brain_tensor_cache.py`. Среда `.venv`, для Torch —
`work/ml-venv`. Completed campaigns не перезапускать; новый range сначала регистрировать.

Официальные основания: [Alchemy indexed endpoint](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-transactions-for-address),
[CU weights](https://www.alchemy.com/docs/reference/compute-unit-costs),
[Pump migration](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md),
[Pump official IDL](https://github.com/pump-fun/pump-public-docs/tree/main/idl).

### Сравнение направлений после аудита

| Направление | Ожидаемый эффект сейчас | Решение |
|---|---|---|
| Больше fits / новый backbone | Низкий: ноль новых train pools за сутки | Отложено |
| Быстрее прежний timer | Больше fits на похожих данных | Только shadow diversity trigger |
| Ещё несколько GT pools | Умеренный, сохраняет polling и survivorship bias | Не основной этап |
| Historical cohorts + indexed events | Высокий: доступ к прошлым рынкам и массовый routing | Реализована qualification |
| Новая торговая policy | Важна, но затруднит объяснение одновременного data change | Следующая отдельная ablation |

Memory-map измерение на Apple M4 Pro: cache для 91472 decisions создан за 2.990 s;
1000 CPU batches по 64 решения считаны за 0.0341 s после создания/прогрева cache.
Это **только загрузка тензоров**, не forward/backward, не cold-disk benchmark и не оценка
ускорения всего training. Peak RSS процесса 628408320 bytes. Seed draws: 7.

### Роли providers и пределы масштаба

| Provider | Подтверждённая роль сейчас | Ограничение |
|---|---|---|
| GeckoTerminal | Действующий USD OHLCV / live Brain | Fixed cohort, polling, revisions/quarantine |
| Alchemy | Historical indexed transactions, migration discovery, fee flow | Address index coverage и protocol layouts требуют проверки |
| Helius | Connector видит настроенный API key | Account plan/usage неизвестны; платный доступ не предполагался |
| Public Solana RPC | Возможный read-only verification/fallback | Historical throughput и completeness в этом этапе не квалифицированы |

Free quota не растягивалась искусственно: полезные campaigns запускались последовательно
с 1s pacing. Дальнейший bulk остановлен после измерения узкого места admission, а не ради
сохранения нескольких тысяч CU. Стоимость доставленных swap candidates составила примерно
4.13 CU /71.7 kB (pool lane) и 7.41 CU /160.4 kB (fee lane) на событие. Это слишком разные
профили, чтобы переносить одну скорость на целый год или обещать 1000 train markets.
Discovery 100/500 markets уже пройден; 1000 пока не достигнут даже в этом registry snapshot
(965). Для 100/500/1000 **train** markets остаются общими блокерами qualified bars, coverage,
quote units и cohort admission; затем потребуются storage/compute измерения на большом корпусе.

## Проверки и воспроизведение

`make setup/check/smoke/build` прошли; основной набор: 890 passed /11 optional skips.
В ML-среде: 135 passed, включая shard/tensor-cache parity. Seals OLD/NEW datasets/models,
нового corpus/shards/cache и operator deployment guard проверены повторно.
[Evidence с hashes](evidence/brain-vacuum-2026-10-06.json). Код: `520731f`, `6431b50`, `e2e9262`.
Полные artifacts — `work/brain-review-20261006/`, не Git. Не заменять существующие outputs.
Ранние acquisition workers были связаны hashes изменяемого checkout; полная immutable
копия исходников worker добавлена с `fee-stream01`. Все raw позволяют повторить offline
декодирование финальным кодом. Это ограничение provenance ранних запусков сохранено явно.
