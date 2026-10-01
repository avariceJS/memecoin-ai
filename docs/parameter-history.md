# История параметров и редкие операции

`collect-pilot --token-2022 --dependency-history` создаёт новый immutable pilot spec:
сканируются pool, два vault, config и два mint. Старые журналы не переименовываются
в журналы с полным покрытием зависимостей. Clock остаётся частью каждого schema-2
checkpoint; `getEpochSchedule` сохраняется отдельным исходным capture. Schema 1/2
и прежние архивы совместимы.

`audit-protocol` восстанавливает параметры с левой границы, в порядке block/transaction/
invocation. Поддержаны pinned `update_amm_config` (параметры 0–7), Token-2022
`setTransferFee`, mint/burn supply. Новая transfer fee назначается на epoch+2;
повторное назначение до активации не превращает ещё не действовавшую fee в старую.
Epoch вычисляется по сохранённому schedule и проверяется против Clock **обоих**
снимков. Warmup epochs не поддержаны. Правый снимок используется для проверки,
а не исправления промежуточных значений. Failed tx не применяются; ошибка в
реконструкции не оставляет частично изменённую parameter history.

Полнота history условна относительно индекса одного RPC-провайдера. Для старого
режима pool/vault-only сохраняется явное допущение о неизменности config/mint.
Совпадение параметров на границах само по себе не доказывает это допущение.
Неподдержанные owner instructions, pool-admin изменения, изменения extensions/
authorities и неоднозначные traces блокируют проверку. Это экономическая модель
успешных записанных операций, не полный admission/authority эмулятор Solana.

## Восстановление полными блоками

`recover-pilot --full-blocks` загружает finalized `getBlock`, `jsonParsed`, `full`
вместо отдельного `getTransaction` для каждого signature. Лимит ответа 32 MB,
request budget и пауза прежние. В SQLite сохраняется настоящий block capture;
transaction reference указывает на него. Фиктивных getTransaction receipts нет.
Slot берётся из запроса блока, порядок — из массива его транзакций, receipt не
сдвигается назад. Все обнаруженные signatures должны присутствовать в блоке;
duplicate/неподдержанный ответ не закрывает pending jobs. Смешанный журнал из
getTransaction и getBlock допустим; отсутствие общей независимой source-проверки
этот способ не устраняет. Parse cache ограничен двумя неизменяемыми captures.

Расширение на общий config может сделать тихий pool дорогим для сбора. Это реальная
стоимость полноты зависимостей. Не обрезать данные до pool-only и не объявлять
интервал достоверным ради снижения числа запросов.

## Отдельные LP witnesses

`audit-liquidity-witness CAPTURE --out REPORT` проверяет одну classic-SPL deposit/
withdraw: integer rounding и limits, CPI transfers, LP mint/burn и net vault deltas.
Входные effective reserves и internal LP supply берутся из LpChangeEvent **той же
транзакции**. Поэтому результат называется event-conditioned witness, а не полной
независимой реконструкцией. Он не доказывает prestate, отдельные fee counters,
историческую допустимость pool или идентичность deployed binary. Token-2022 LP
witness без исторической fee schedule отклоняется, даже если fees в event равны нулю:
проверка vault metadata требует classic Token program.

Публичные компактные captures: `tests/fixtures/parameter_history`, manifest с hashes.
Большие полные блоки и SQLite journals остаются вне Git. Реальные результаты и
ограничения текущего прохода приведены ниже.

## Альтернатива пагинации: фиксированный диапазон блоков

Для коротких интервалов `recover-pilot --block-range` перечисляет finalized blocks
через `getBlocks(start+1, end)`, сохраняет исходный список и полные блоки, затем
выбирает транзакции по всем наблюдаемым адресам. Ограничение — 256 слотов между
снимками; верхний bank обязательно должен присутствовать в списке. Пропуски внутри
списка остаются условными относительно RPC, не объявляются независимо доказанными
skipped slots. Адресные страницы и их незаконченные cursors сохраняются.

После успешного range recovery `pending.scans` исключает интервалы с альтернативным
покрытием. Это operational cache, не доказательство: аудит заново читает список,
блоки, membership, отбор по адресам и сверяет transaction cache. В отчёте
`scans_complete` может остаться false, а `block_range_coverage_capture` показывает
другой способ покрытия. Если частичный address index противоречит полным блокам,
интервал отклоняется. Нельзя считать этот метод независимым вторым провайдером.

Цена полного диапазона — объём данных всей сети. Для длительного потока это ещё
не обоснованная production-архитектура. Перечисление фиксированного диапазона полезно
для bounded verification и не зависит от потока новых signatures после правой границы.

## Mainnet результат 2026-10-02

Два новых семиаккаунтных интервала по 12 слотов:

| Проверка | Первый интервал | Второй интервал |
|---|---:|---:|
| Pool | 4GWh5…QYfm | G7iLG…Zxr2 |
| Полностью полученные связанные tx | 455 | 863 |
| Failed / успешные без операции нашего pool | 200 / 255 | 353 / 510 |
| Полные блоки | 12 | 12 |
| Address scans закрыты | 6/6 | 3/6 |
| Покрытие диапазоном блоков | не требовалось | подтверждено исходным getBlocks + 12 блоками |
| Итоговая economic/withheld/config/mint сверка | совпала | совпала |

У обоих выбранных пулов **не было успешных pool operations в этих интервалах**.
Название второго локального файла `active.sqlite` отражает прошлую активность пула,
а не активность новой выборки. Эти проверки расширяют доказанность сбора зависимостей,
но не добавляют новых наблюдений swap/LP/смены тарифа к endpoint-anchored calibration.
Никакой выборки «до первого красивого результата» не выполнялось.

В первом журнале 55 captures, 23 index pages, ошибок источника не зарегистрировано.
Индивидуальная recovery была прервана для перехода к полным блокам; точное число
сетевых попыток не утверждается. Во втором — 117 captures, 100 index pages и одна
сохранённая остановка по request budget. Затем 13 запросов range recovery закрыли
интервал; старые незакрытые cursors не исправлялись вручную. Сбор/recovery разделялись
разработкой кода, поэтому эти числа **не benchmark непрерывного коллектора**.

Целевой исторический поиск: 2 LP address pages, 14 успешных tx captures, среди них
13 classic LP witnesses (5 deposit, 8 withdraw) совпали до atomic unit; ещё одна tx
не содержит LP operation. Четыре репрезентативных LP captures сохранены в Git.
Поиск по admin: 12 успешных tx, ни одной CPMM операции; это не утверждение об
отсутствии admin changes в полной истории. По withdraw-authority: 8 tx, три внешних
withdrawWithheldTokensFromMint, один относится к нашему mint и сохранён как regression.
Полная сумма mint withheld по нему не реконструирована.

192 tests, lint/format/strict typing, synthetic smoke, build и воспроизведение из
установленного wheel прошли. Итоговые dev/wheel range protocol reports побитово равны.
Оба новых frozen архива проверены offline; прежние classic/Token-2022 архивы и
их протокольные результаты также проверены повторно. Hashes больших локальных journals,
отчётов, исходников и точные slots — [evidence](evidence/history-2026-10-02.json).
Mainnet смена config/transfer tariff, epoch-boundary, pool fee collections, LP
между независимыми snapshots и прямые withdraw из tracked vault остаются непокрытыми.
