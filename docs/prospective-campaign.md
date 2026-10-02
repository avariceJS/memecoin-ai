# Возобновляемый prospective corpus

Кампания фиксирует cohort, raw reference captures, source snapshot, общий request budget,
срок действия и временной calibration cutoff до первого запроса. `create-prospective-campaign`
создаёт план без сети; `resume-prospective-campaign PATH` собирает **один** новый bounded
сегмент; `verify-prospective-campaign PATH` воспроизводит завершённые сегменты offline.
Endpoint поступает через MEMECOIN_HELIUS_RPC_URL, в artifact сохраняется только alias.

Сегмент использует existing collect_forward и сохраняет exact requests/responses,
receipt/decode/publication timestamps, append-only attempts и frames. Перезапуск процесса
не переписывает сегменты: недозавершённый каталог опечатывается как interrupted вместе
со всеми сохранившимися bytes и исключается из labels. Весь его planned budget считается
израсходованным. Новый сегмент получает новый causal book, warmup и независимый tail
censoring. Границы не заполняются backfill. OS file lock исключает параллельный resume.

По умолчанию: максимум 4 × 30 rounds, три фиксированных пула означают 360 requests;
5 s cadence, socket timeout 15 s, response cap 1 MB, spacing 1 s, expiry 1 h.
Socket timeout и cooperative segment wall budget не являются жёстким deadline процесса.
HTTP/RPC/validation failure прекращает кампанию; timeout/connection допускает новую
ручную или оркестрированную invocation не ранее 30 s, максимум два failed сегмента
подряд. Внутри сегмента нет retries. Это инженерные ограничения, не trading parameters.

Seal каждого сегмента ссылается на предыдущий seal. Старые captures и manifests остаются
неизменными. Exhausted resume не делает сетевых запросов. После изменения transform
version нужна новая кампания или pinned source для проверки старой.

Сегментные observed-price targets остаются исходными diagnostics. Для последующей общей
разметки используется **единый frozen cutoff кампании**, а не повторяющееся чередование
calibration/evaluation каждого сегмента. Это engineering split, не финальный holdout.
Обучение, рыночная достаточность и execution qualification не следуют из complete=true.

## Условные targets исполнения

`export-execution-targets CAMPAIGN --out DIR` и
`verify-execution-targets CAMPAIGN DIR` работают offline, связывают artifact с plan и
всеми segment seals. Append к родителю требует нового export. Source snapshot включает
все transforms; старые файлы не переписываются.

Сетка: два направления × 100/1000 ppm decision reserve × assumed delay 0/5 s.
Gross wallet input выбирается по initial observation, до future states. Quote включает
integer CPMM trade/creator fees и Token-2022 transfer fees на каждом плече; применяется
existing transfer_swap, отдельная упрощённая математика не вводится. Для входа minOut
равен decision quote минус 100 bps; первый новый snapshot после decision+delay должен
уложиться в 15 s tolerance. Нулевая задержка тоже требует нового snapshot; фактическая
задержка ограничена polling resolution и сохраняется через leg timestamps.

После входа удерживаем весь net acquired amount 20 s от entry publication. Первый
fresh snapshot после этого времени становится exit decision. Его quote задаёт minOut;
exit fill проверяется на следующем fresh snapshot после exit decision+delay. Каждое
плечо требует новых slot/receipt, неизменного causal segment и отсутствия unusable rows.
Неудачный вход оставляет нулевую позицию, неудачный выход сохраняет всю acquired position;
ни один не становится нулевым profitable/безубыточным roundtrip. Censored outcome не
имеет PnL; на хвостах нет интерполяции. Label maturity — публикация final observation,
не позднейший CPU time разметки; maturity на/после campaign cutoff удаляется из calibration.

Модель `independent_future_snapshot_reset_v1` оценивает каждую сделку отдельно.
Immediate own impact учтён внутри integer quote; перенос нашей сделки в будущий рынок
не моделируется. Admission, inclusion, network fees, rent/ATA setup, native-token FX,
MEV и persistent own impact неизвестны. Поэтому `net_pnl=null` всегда; вычисляется
только условный результат после pool/transfer fees **до сетевых расходов**. Ни реальные
fills, ни исполнимый net edge этими метками не доказаны. Нет накопления PnL портфеля.
Восемь вариантов одной decision и пересекающиеся окна не независимые samples.

Эта сетка — инженерный контракт, выбранный до генерации labels, а не fitted execution
calibration или final holdout design. Достаточность данных оценивается отдельно.

## Реальный результат 2026-10-02

Collection revision `5975456`, target revision `d7436ca`; seed отсутствует. Fixed cohort
взята из прежних трёх pools до новых outcomes. Точные cutoff/receipt timestamps, plans,
errors, byte counts и SHA256: [evidence](evidence/prospective-execution-2026-10-02.json).

| Artifact | Requests | Valid snapshots | Feature rows / usable | Segment span |
|---|---:|---:|---:|---:|
| work/prospective-campaign-01 | 49 | 48 | 51 / 47 | 116.016 s |
| work/prospective-campaign-02 | 68 | 67 | 69 / 65 | 118.773 s |

Между ними один isolated consistency probe, valid, исключённый из corpus features.
Всего 118 HTTP 200; два ответа отклонены: context/Clock 452681508/452681509 и
452682339/452682340. Метод getMultipleAccounts, finalized/base64, точные семь адресов
и minContextSlot сохранены в safe error rows. RPC error code отсутствует; категория
checkpoint_validation, причина decoder — Clock does not match checkpoint bank.
Upstream причина **не установлена**. Согласно [Solana RPC docs](https://solana.com/docs/rpc/http/getmultipleaccounts),
ответ содержит общий context и упорядоченный массив account values; документация
не объясняет наблюдаемое расхождение. Контракт same-bank не ослаблен. Оба raw responses
в regression fixtures, bytes исходных corpus неизменны. После повторения серии закрыты.

Получено 234.789 s суммарного segment coverage внутри 333.407 s wall envelope с разрывом.
Старые D35 данные сюда не приклеены. Real resume внутри одной кампании не состоялся:
non-transient validation policy запретила его. Resume/crash/cooldown/locking/budget
проверены synthetic tests. Длительный непрерывный corpus **не собран**.

HTTP response time min/median/max: 264/390/11594 ms и 257/370/4563 ms. Это измерения
read requests; не trade execution latency и не backfill throughput. Возраст chain Clock
в usable features median 11.658 / 11.107 s; это отдельная величина, не HTTP delay.

Два derived artifact `work/conditional-execution-targets-01/02`: 960 сценариев,
432 conditional roundtrip, 4 entry-unfilled, 8 exit-unfilled, 516 censored. Mature
outcomes относятся к 67 distinct decisions. Все 444 mature scenarios — calibration;
engineering evaluation **0**, frozen campaign cutoffs не достигнуты и не перенесены.
Net PnL labels — 0. Обучение не начиналось: minutes/fixed cohort/overlapping labels,
источник нестабилен относительно checkpoint contract, execution не квалифицирован.

297 tests, Ruff/strict mypy, smoke/build, offline и installed-wheel reproduction обеих
пар artifacts прошли (`work/prospective-wheel-01`). Следующий шаг — targeted решение
current-checkpoint coherence blocker через Helius diagnosis или другой разрешённый
согласованный source, затем расширение prospective coverage и калибровка execution.
Whole-chain historical trace-аудит D33 остаётся закрытым.
