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
ограничения текущего прохода фиксируются ниже после завершения аудита.

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
