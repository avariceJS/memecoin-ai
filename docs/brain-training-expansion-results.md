# Расширение training support — 2026-10-06

Статус: acquisition и qualification в работе. Финальные числа training/future ниже будут
добавлены после sealed dataset и завершённых fits. [Протокол](brain-training-expansion.md).
Предыдущие [forensic](brain-experience-review.md) и [vacuum](brain-vacuum-results.md) приняты;
этот этап проверяет превращение historical registry в actual training support.

## Что изменено в пути данных

`Alchemy historical birth catalogue → outcome-independent month/SHA selection →
GT USD/5m hydration → exact birth/mint/quote/receipt admission → immutable corpus →
snapshot + прежний B13 recipe → frozen candidate → prospective paper`.

Исторические cohorts выбраны из May–Sep2026, без текущего ranking/liquidity/success filter.
Это выборки из ранее сохранённых monthly migration windows, не полный census месяцев.
Graduation bias сохраняется: bonding-curve токены, не дошедшие до migration, не представлены.
«No contiguous targets» не означает умерший токен или rug. Простое попадание pool в train
отделяется от числа его окон и ширины фактических SGD draws.

Namespace свечей совпадает с работающим Brain: USD/base/5min/first-observed. Для каждого
capture проверяются raw seal/hash, request currency/aggregation, base/quote mints против
on-chain birth, UTC bucket, OHLCV, birth range и реальные receipts. Gaps не заполняются.
Targets требуют все future bars; будущая доступность не переносится назад в прошлое.
UNKNOWN2026swap layouts и WSOL-цены не подставляются вместо этого contract.

Новый corpus — отдельный immutable artifact. AUTO snapshot умеет объединять его со своим
consistent live backup. Reference закрепляется в runtime binding. Первое наблюдение
сохраняется, conflicting revisions учитываются отдельно. Sealed historical-test corpus
имеет отдельный role и не допускается в training merge.

Счётчик growth для действующего24h+100gate остаётся source-only. Total training support
хранится отдельно: иначе после добавления archive следующий refit получил бы отрицательный
live growth. Information trigger пока не включён; модель, features и policy не усложнялись.

## Отрицательные operational результаты

- `admission-probe01`: HTTP401 для2025; ответ public API прямо ограничивает history180days.
  Upgrade не выполнялся; старый диапазон не запрашивался повторно через другие keys/hosts.
- `admission-probe02`:5validGET/5pools/1341eligible, затем429. STOP сохранён,900scooldown.
- `admission-bulk01`:10validGET/7positivepools/246eligible, затем15sURLError.
- `admission-bulk02`:14cases, включая2transportgaps; остановка при routine live cooldown.
- `admission-bulk03`: продолжает75unrequestedcases из исходного100;25priorGET и3transport
  failures перенесены. Source cooldown означает WAIT; auth/rate/sourceSTOP остаются fatal.
  Timeout30s вместо15s, totaltransportfailurecap10/3consecutive, безretryfailedpools.
  Исходный100GET/case budget и wall expiry не сбрасывались.

Новые operational fixes относятся к historical worker. Работающий live collector не
переписывался. Его краткий transport cooldown и последующее самостоятельное recovery
сохранены в журнале. Нельзя приписывать все ошибки провайдера новому lane или считать
опубликованный30/min гарантированно доступной пропускной способностью.

Ранние protocol hashes воспроизведены точными SHA-matching prefixes в
`work/brain-expansion-20261006/protocol-prefixes01`; старые campaigns не редактировались.
Начиная сbulk03 копия protocol хранится внутри pinned code bundle.

## Роли providers после проверки

| Provider | Практическая роль | Что пока ограничивает |
|---|---|---|
| Alchemy | Historical birth universe; raw archival transactions2025–2026 | Coverage/decoders/quote normalization доUSD bars |
| GeckoTerminal | Готовые USD historical/live bars в существующий feature contract | Public180days,429/timeouts,sparse intervals; единственный qualifiedpricefeed |
| Helius | Возможный второй archival/event adapter | Configuredkey,ноaccountplan/no-paidbulk не подтверждены; installer/signup не запускался |
| Public Solana | Независимая проверка текущего account/venue owner | Не готовый historical price feed; wholesale archive не измерен |

Public verification01 ошибочно запросил адрес с пропущенным символом; егоnull не используется.
Verification02 взял точныйaddress изsealedbirthbinding: account существует, owner
`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`. Обаcaptures сохранены;2RPC,0USD budget.
Это проверка owner, не доказательство completeness/price correctness за2026June.

Alchemy теперь участвует в отборе actual training universe. Ценовой runtime всё ещё зависит
отGecko; это ограничение не скрыто за формулировкой «multi-source». Helius raw transactions
сами по себе также не устраняют decoder/coverage/USD bottleneck. Нельзя на основании
одного advertised throughput обещать1000qualifiedtrainmarkets.

Первичные источники:
[GT OHLCV semantics](https://docs.coingecko.com/reference/pool-ohlcv-contract-address),
[Alchemy indexed history](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-transactions-for-address),
[Alchemy CU weights](https://www.alchemy.com/docs/reference/compute-unit-costs),
[Helius archival API](https://www.helius.dev/historical-data),
[public Solana endpoints](https://solana.com/docs/references/clusters).

## Сравнение без подгонки модели

Control: тот же consistent source prefix и cutoff, старый24market corpus.
Broad: control + admitted historical markets. Validation/test samples и input frames должны
совпадать; новые historical origins находятся доtrain boundary. Train cap48k и прежние
pool/day weights,12features,context8,GRU16/linear,seeds7/19,1000updates сохранены.
8fits зарезервированы отдельно отB13AUTO; повторныхfits поtest не будет.
Validation1% simplicity selection сохраняет empirical полноправнымcandidate.

Послеfreeze независимый paper сравнит frozenB13/control/broad на одних будущих receipts.
Forecast loss/calibration проверяется пофактически опубликованнымpredictionfiles; loss
пересчитывается изq/Y, input/target identity сверяется. Нельзя выдавать offline inference
заprospective prediction. Policy прежняяfixed15min, книги независимы, real executionнет.

20дополнительных unseen pools закреплены доfits и будут получены послеfreeze; их past-event
test не входит ни в train, ни в selection. Он дополняетfuture exam, но не заменяет его.
Future borrowedsource узок: hotforecast support преимущественно изстарых рынков. Даже
улучшение на нескольких таких outcomes не доказывает общего улучшения широкогоBrain.

## Проверки на текущем срезе

`make check`:909passed/11optional skips. ML:140passed. Предыдущийsmoke/build прошёл.
Проверены namespace/mint mismatch, gaps, receipt cutoff, corruption, duplicate admission,
source-onlygrowth послеmerge, stagedruntimeбезпереключения, prospective pairing/metric
reproduction, transport isolation/auth/rateSTOP. Финальные acceptance/pins будут добавлены
после dataset/fits/future activation/cutover.
