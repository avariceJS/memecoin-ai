# B11 — архив, учебная выборка и фазовый цикл Brain

## Решение и результат, 2026-10-04

**Принят archive-first цикл с фазовым приоритетом тяжёлых задач. B09 source и B10
paper/train/UI сохранены без изменения deployment/config hashes.** Новый phase scheduler
не установлен: существующему prospective rollout даём пройти первые сутки и refresh,
параллельно готовим более полезный учебный архив. Два Mac и новый backbone сейчас не нужны.

Скорость развития ограничивают прежде всего **разнообразие и отбор данных**, затем
семантическая квалификация raw history. Текущий trainer уже использует 12 OHLCV-derived
признаков и q10/q50/q90 return distribution на 5/15min, а не только цену или point return.
B07 измерил около60s на4fits; текущая CPU-конкуренция не объясняет слабый corpus. Это не
benchmark будущего обучения на миллионах примеров. Source redundancy остаётся отдельным
operational риском: phased scheduling само по себе не исправит freshness единственного GT.

### Что фактически сделано

Из consistent backup существующего store, без новых GT GET, создан immutable
`work/brain-b11/textbook02`: **61 233 first-observed свечи, 24 пула, 50 event-day buckets**,
2026-08-16 01:50 → 2026-10-04 17:05 UTC (время открытия свечи). Все receipts относятся
пока к одному календарному дню. 50 исторических дней не равны 50 дням prospective работы.
Raw audit:1318captures, exact. Каталог хранит namespace и наблюдавшиеся непрерывные интервалы;
пробелы не объявляет отсутствием торгов или смертью токена. Повторно скачивать этот архив
для обучения не требуется.

| Этап отбора | Полный локальный архив | Действующий rolling7d view |
|---|---:|---:|
| Свечи | 61 233 | 15 287 |
| Контекстные decisions | 59 837 | 15 076 |
| Оба зрелых горизонта | 59 404 | 14 995 |
| Train по прежнему global60/20/20 split | 28 191 / **4 pools** | 6 005 / **5 pools** |
| Validation | 11 968 /5pools | 2 918 /10pools |
| Test | 19 218 /24pools | 6 027 /24pools |
| Purged/incomplete | 460 | 126 |

Эти split имеют разные даты, поэтому **не являются matched сравнением моделей**. Их задача —
показать selection funnel. 28k bar cap действующего trainer сейчас НЕ достигнут; историю
отсекает rolling7d. У полного архива effective pool count=5.26, доля крупнейшего=23.34%.
Простой переход к full-history 60/20/20 увеличивает train duration, но уменьшает количество
обучающих рынков. Текущая discovery cohort также имеет survivorship/liquidity bias. Нельзя
обещать улучшение Brain от одного увеличения числа строк.

Первый full build действительно упёрся в `B01_dataset_capacity` (>40kdecisions).
`textbook01` сохранён со STOP; revision02 использует **тот же** backup. Новый adapter
сохраняет 35 shards≤4096 origins, overlap14past/3future: воспроизводятся features каждого
из8context frames и labels. Совместимая материализация для существующего trainer ограничена
100kdecisions; выше — явный отказ, не скрытый truncation. Это подготовка к larger corpus,
**не готовый streaming trainer на миллионы строк**. Независимый unsharded legacy build
каждого пула точно совпал со всеми59 837decisions и использованными feature frames.

Сохранены119 088 зрелых auxiliary horizon labels и586 missing-future masks. На горизонтах
1/3bars: log return; сумма квадратов close-to-close log returns; минимум log(close/origin)
по будущим closes с верхней границей0; log1p будущего наблюдавшегося USD volume.
На1bar variance определяется return и не даёт независимой информации; на3bars добавляется
path structure. Это не intrabar drawdown/stop path, ликвидность, fill probability или net PnL.
Actual input/target availability и capture pins сохранены; historical backfill остаётся
retrospective. Новые targets ещё не подключены к model heads или paper runtime.

Revision audit:611 изменённых pool×candle keys, из них40 с изменённым close. Сохраняются все
версии, training view остаётся first-observed; latest revised history нельзя выдавать за
то, что было доступно онлайн. Новых fits в B11: **0**, promotion: **0**.

### 2025: source access подтверждён, учебный годовой corpus ещё отсутствует

Frozen qualification Alchemy выполнена: **5RPC/440nominalCU**,400 full transactions за2025,
8 536 317response bytes суммарно,7.924s один bounded subprocess. Два пула выбраны по oldest
creation metadata до запросов, а не по успешному ответу. Raydium CLMM `2AXX…rvY2` дал200tx
за первые~6min 2025-01-01; Orca `6R4r…TXCo` —200tx за2025-07-02. Обе пары страниц имеют
ещё cursor: история не завершена. Envelope фактически `data`; стандартный getTransaction
первой записи полностью совпал по slot/blockTime/transaction/meta/version. Offline replay
вновь проверил hashes, cursors, адрес, даты, порядок и core equality без сети.

Это **архивные transaction records, не400 qualified swaps**, и тем более не годовые свечи.
Протоколы этой старой cohort не поддержаны текущим экономическим adapter. B06 PumpSwap
semantic quarantine остаётся; account history не восстановлена. Probe сохраняет время
HTTP receipt/записи, но не претендует на квалифицированный crash-durable canonical ingestor.
`training_eligible=false`, `history_complete=false`. Не расходовать миллионы CU на payloads,
которые пока не превращаются в проверяемые примеры; сначала измерять useful decoded coverage.

Первый probe root завершился **до RPC**: uppercase source alias → локальная validation;
parent STOP handler также имел duplicate category. Сохранены оба safe error type и sealed
STOP, сделана одна исправленная revision02 с прежней cohort и общим бюджетом. Provider retry
не было. Поле `paid_usd=0` в локальном результате означает разрешённый денежный envelope,
**не независимо измеренный счёт**: основание — owner noPAYG/no-card attestation и тот же
credential hash. MCP4480CU/$0/dataThrough17:06UTC немонотонен; это не charge delta/остаток.
Helius не опрашивался: наличия локального URL недостаточно для account/no-paid verification.

Официальные границы, проверенные2026-10-04:

- [Alchemy getTransactionsForAddress](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-transactions-for-address): time filtering/pagination, full pages≤100; [CU table](https://www.alchemy.com/docs/reference/compute-unit-costs):100CU/page и40CU/getTransaction. Нельзя переносить размер/стоимость страниц Helius на Alchemy.
- [Alchemy Account Archive](https://www.alchemy.com/docs/solana/account-archive): historical account-state API требует paid tier. Доступ к старым транзакциям этого права не доказывает.
- [CoinGecko API guide](https://www.coingecko.com/learn/coingecko-api-troubleshooting-guide-and-solutions) описывает Demo on-chain history past6months; [OHLCV reference](https://docs.coingecko.com/reference/pool-ohlcv-contract-address) различает Basic и Analyst+,1000rows/page. Keyless GeckoTerminal и Pro/Demo contracts не считать одинаковыми. B11 не доказал доступ GT к2025 и не обещает его повторной пагинацией.
- [Helius method](https://www.helius.dev/docs/rpc/gettransactionsforaddress) документирует другой full-page/credit contract и archival retention. Это кандидат на qualification, не доступный/проверенный fallback проекта.

### Как теперь организовать collect → train → paper

Разделение acquisition/storage/trainer/paper/evaluation остаётся. **Фазы — приоритеты и
resource leases для тяжёлой работы, а не единый процесс и не обязательное выключение paper.**

| Фаза | Тяжёлая задача | Что продолжает работать | Условие выхода |
|---|---|---|---|
| COLLECT | Недостающие historical ranges, возрастные/рыночные cohort, raw semantic work | Приоритетный лёгкий hot capture, frozen champion paper, UI | Bound/stop либо измеренный новый useful coverage; immutable raw и range journal |
| SNAPSHOT / TRAIN | Consistent snapshot, qualification, sampling, train-only transforms и bounded fits | Hot source + champion; bulk history/discovery уступают ресурсы | Whole-run seal, dataset/model/config/code hashes, completed result |
| PAPER | Frozen candidate inference, paired future forecasts и virtual decisions | Hot source, UI, зрелые outcomes; только дешёвый background | Заранее заданное future окно/maturity; никакой подмены weights внутри версии |

Эта политика принята; **автоматического phase controller пока нет**. B09 frozen config
не расширяется молча:30historypages/pool,history floor90days,12s spacing,hot6/120s и прежний
72h expiry остаются. Их корректировка — новый operational revision после review, не правка
живого config. Однообъектный canonical store сейчас достаточно локален; второй Mac пригодится
для CPU/RAM isolation при измеренной конкуренции. Переносить тогда sealed shards/models,
а не разделять mutable SQLite между машинами.

Исторический архив растёт двумя дорожками. GT продолжает инкрементально сохранять доступные
bars; следующая широкая collect campaign выбирает также зрелые markets по pre-return
metadata/возрастным слоям, а не только сегодняшние trending. Raw Alchemy lane даёт путь
к2025 и собственным candles после квалификации protocol decoder, USD conversion и missing
transaction rules. Для умерших токенов нужны **исторические birth cohorts** из прошлых
program/block windows с учётом закрытых/недоступных pools. До появления такой выборки
показывать survivor-only coverage; пустой endpoint не доказывает dead token.

Запросы планировать по namespace+pool+range+decoder profile, сохранять cursor/terminal reason
и использованные captures. Transaction canonical key — signature/slot, versioned decode;
пересечения pool queries дедуплицировать глобально. Controlled overlap для revision audit
допустим, систематический redownload уже сохранённого интервала — нет. Лимиты нужны для
защиты от runaway/неполезной истории, не для искусственного растягивания free quota.
Кампанию оценивать по новым пригодным pool-days, coverage cohorts, gaps/revisions и decoded
observations наCU/GET/byte. 400rawtx/440CU не является оценкой цены года market history.

### Следующий data experiment: сначала curriculum, затем feature value

До следующего fit зафиксировать отдельный exploratory manifest на новом sealed snapshot:

1. Общие абсолютные даты validation/test берутся один раз из последнего rolling7d view;
   все data arms используют одни и те же observations и maturity purge. Новые/недоступные
   labels не заполняются. Test только development, не reserved F01. После просмотра scores
   не менять split/selection ради улучшения. Будущий paper exam начинается после model ready.
2. Arms: прежняя recent7d учебная часть; весь доступный past до **того же** train cutoff;
   past с равным весом pool×UTC-day. Последний arm ограничивает доминирование старых pools,
   но не выдумывает историю недавно созданных. Возраст/cohort/regime coverage показывать
   отдельно. Nested25/50/100% curves по deterministic hash(seed7,pool,day,decision), выбор
   до labels; sampling weights train-only. Same evaluation, budget и recipe для arms.
3. Сначала прежние12features/return quantiles, empirical + linear/GRU(width16,seeds7/19,
   max10epochs, прежний optimizer); никаких новых backbone/hyperparameter sweep. Для каждого
   data arm записать maxsteps/walltime до запуска: предел нельзя повышать после score.
   Mean и tails ошибок, calibration, coverage/missingness, pool/day support и paired
   day-block uncertainty. Один день future data не поддерживает устойчивый daily-bootstrap.
4. Только затем отдельная ablation longer past volume/volatility и auxiliary heads из B11;
   одни будущие observations, capacity/compute controls. Training loss улучшился — не значит
   улучшились entry/rejection/exits. Сравнить turnover, cash fraction, forecast errors и
   виртуальный PnL при одинаковой cost sensitivity; учитывать общие overlapping episodes.
   Не выбирать по лучшему одиночному test/shadow snapshot. Champion promotion отдельно.

Это specification следующего опыта, **не зарегистрированный выполненный trial**; новые
absolute dates/step limits/source batches ещё нужно связать с конкретным snapshot перед fit.
Streaming mini-batch loader нужен перед материальным ростом выше100kdecisions. Разработка
curriculum/loader не требует ждать календарного окна; новый GT bulk runtime не должен
сломать текущий prospective exam. Raw PumpSwap qualification развивается параллельно и
не блокирует GT price-proxy paper или корректный supervised experiment.

Доступны сейчас: return/price structure, past volatility и volume dynamics с causal masks.
Исторические liquidity, buy/sell flow, trade count, transfer-fee-aware pool state и net
opportunity пока не квалифицированы. Today's discovery liquidity/age нельзя broadcast назад;
snapshot metadata допустимы только as observed. Не кодировать технические индикаторы как
заранее истинные buy/sell правила. Paper PnL не используется как execution truth для labels.

### Живой продукт и следующий видимый кандидат

Cutoff17:27:37UTC: B09 source и B10 paper healthy,906sourceattempts,61 249bars в последнем
dashboard; runtime/config pins verified unchanged. С начала B09 сохранены6 transport
failures: `URLError / TimeoutError / http_status=null`, отработал прежний cooldown; это
категория transport, не quota/429. Новых fatal STOP на cutoff нет. 400soak samples:
span4.684h,observed4.378h,≥1fresh89.75%,≥4fresh70.50%; maxgap1189.70s включает старый outage.
Это ещё не сутки и не недельный reliability result. История failures/gaps не сброшена.

Control Center остаётся [локально](http://127.0.0.1:8790); champion empirical, старые challengers,
positions/cash сохраняются. Следующий scheduled fit **не раньше2026-10-05 13:41:54МСК**,
плюс≥100новых mature samples и hourly scheduler. После успешного completed/sealed run
кандидат получает будущие paper/shadow observations. Это новый fit **прежнего recipe**,
не автоматическое внедрение full archive/auxiliary heads B11. Automatic promotion нет.
Expiry по-прежнему2026-10-07 15:23МСК. Полноценный фазовый цикл выбирается после этого
первого daily review, без преждевременного объявления weeks/month autonomy.

### Evidence и воспроизведение

- [Corpus/selection/revisions/labels](evidence/brain-archive-curriculum-2026-10-04.json).
- [Offline reproduction и archival proof](evidence/brain-archive-reproduction-2026-10-04.json).
- [Runtime health, safe failure history, проверки и monetary basis](evidence/brain-archive-qualification-2026-10-04.json).
- Sealed roots: `work/brain-b11/textbook02`(542MiB), `archive-probe02`(8.2MiB); предшествующие
  failed roots01 сохранены. Textbook manifest `06caf6fbfafd424f85977e06092de0cad48ddae2b8ffa5a0c12beac91671bf02`,
  probe `1ebec9861d1b6e9e85c7fc53e64f5ff2e501203e3e4d404a6c65d2ddd63e9e94`.
- Code: frozen plan d48e99e; implementation16f4359; corrected probe52005d7. Probes/snapshot
  доступны для offline replay; не запускать acquisition scripts повторно поверх roots.
  `tools/brain_b11_reproduce.py` читает sealed inputs без RPC, сравнивает legacy per-pool
  build и auxiliary stream. Public report публикуется create-exclusive; повторный запуск
  с `--output work/brain-b11/replay-audit02.json` пишет отдельный результат (выбрать новый путь,
  если он уже существует). Исходный evidence не перезаписывается. Raw artifacts вне Git, SHA reports в Git.
- 644basePASS/6optional skips,670combinedMLPASS; makecheck/smoke/build успешны. Tests проверяют
  shard boundary/gap equivalence, missing-future labels, availability/duplicate/capacity.
  Ни edge, ни новый модельный winner, ни calibrated execution этим не установлены.

**Следующий B12:** data curriculum/масштабируемая загрузка и useful historical campaign,
одновременно — review первого daily refresh. Сначала исправить data selection; более сложная
модель не решит отсутствие рынков в train. Главный оставшийся предел большого2025–2026corpus —
historical cohort coverage и экономическая интерпретация raw events, а не общий объём free quota.

---

## План до новых RPC и dataset snapshot, 2026-10-04

Сохранить B09 source/audit и B10 paper/train/UI до первого scheduled refresh; не менять их
runtime, quotas, expiry, models и temporal gates. На старте B11 paper/source healthy, около
61k bars, новых B10 incidents нет. Следующий daily fit не раньше 2026-10-05 13:41:54 МСК.
F01/D43/holdout не читать. Реальные сделки, signing и paid подключения запрещены.

Выбор: archive-first с фазовым приоритетом тяжёлых задач, а не полным выключением остальных
сервисов. Сейчас training занимает десятки секунд/сутки, поэтому само распределение CPU
не основной bottleneck. Проверить потери archive→rolling7d/cap28k→context8→mature→split,
концентрацию по рынкам/датам, revisions, sampling bias и actual prospective coverage.

Реализовать offline immutable учебный snapshot из consistent SQLite backup существующего
GT store без GET/redownload. Полный archive bundle для существующего D44/B01 trainer,
отдельный воспроизводимый coverage/selection report и auxiliary bar-path targets:
close-to-close realized volatility, downside относительно origin по будущим closes,
future observed dollar volume. Это price-proxy labels, не исполнения, intrabar path,
историческая ликвидность или net opportunity. Модели/backbone/runtime пока не менять;
новые targets не подмешивать в старый model contract. Составить следующий data/feature
experiment до fits, с global chronological boundaries/purge, train-only transforms,
paired future evaluation и отдельными frozen model versions.

Новая bounded archival qualification Alchemy: два наиболее старых пула из admitted24,
созданных по metadata до2026 (tie address). Это targeted availability probe, не случайная
выборка и не admission dead-token universe. На каждый до2pages getTransactionsForAddress,
full/jsonParsed, finalized, maxSupportedTransactionVersion0, limit100, ascending,
blockTime [2025-01-01,2026-01-01), затем один getTransaction первой полученной transaction
для core cross-check. Максимум5RPC/440nominalCU/120s/8MiB per response/≥1s spacing,
первый error→STOP, без retry или замены cohort. JSON shape и реальные даты проверять;
raw сохранить отдельно, training_eligible=false до semantic qualification.

Это отдельная B11 allocation, не reuse D51/B06. Основание USD0: текущая owner attestation
noPAYG/no card и неизменный credential fingerprint. Свежий Alchemy MCP usage4480CU/$0,
dataThrough17:06UTC немонотонен относительно прошлых отчётов: не измеренная стоимость,
не verification plan/остатка. MCP/runtime/credentials различать. Helius setting существует,
но access/no-paid/account mapping не проверены; RPC Helius не выполнять. GT production
limiter не обходить параллельным backfill. Никаких новых GT acquisition в B11.

Крупный следующий collect запуск — после квалификации источника/семантики и отдельного
фиксированного envelope с полезными rows/covered pool-days на CU/GET, overlap cache, cursor
и resumable range journal. Готовность 2025–2026 не заявлять только по наличию archival RPC.
Исторический список ныне умерших pools требует birth cohorts из прошлых windows; текущий
trending universe не делает такого списка. Не заменять прошлую available_ns временем блока.

Проверки: fixtures availability/maturity/gaps/duplicate/immutable snapshot; полный raw audit,
reproduction snapshot и поддержанных labels, runtime hash equality до/после; make check/smoke.
Сохранить evidence, Memory Bank и Git. Два Mac, distributed orchestration и новые backbone
пока не нужны. Следующий этап определяется audit и фактическим archival result.

### Уточнение после реального capacity failure, до training/RPC

Первый read-only snapshot `textbook01` остановился на `B01_dataset_capacity` (>40000samples).
Raw SQLite backup сохранён и sealed со STOP; повторной загрузки сети нет. Archive revision2
использует тот же immutable backup, shards≤4096 decision origins с overlap14past/3future,
чтобы features всех8context frames и targets совпадали с прежним build. Все shards сохраняются;
материализация для существующего trainer ограничена новым явным envelope≤100000decisions.
Это не снятие gate B01 и не изменение frozen B10. Для миллионов examples далее нужен
streaming trainer; не выдавать текущий in-memory trainer за уже масштабированный.

### Исправление preflight до первого RPC

`archive-probe01` не сделал RPC: локальный alias с заглавной B отвергнут конструктором.
Обработчик parent failure имел duplicate `category` keyword; также исправлен. Первый root
sealed со STOP, debit0. Разрешена одна исправленная revision02: та же заранее выбранная
cohort и совокупные5RPC/440CU, новый120s только для ещё не начатой сетевой проверки.
Это исправление локального preflight, не повтор после provider отказа или смена выборки.
