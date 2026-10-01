# Token-2022: ограниченная область поддержки

## Контракт

`tokens.py` допускает base Token/Token-2022 accounts и только перечисленные extensions:

| Объект | Extensions | Проверка |
|---|---|---|
| Mint | TransferFeeConfig (1) | Размер 108, старый/новый тариф, cap, bps, активная эпоха |
| Mint | MetadataPointer (18), TokenMetadata (19) | Размер pointer и полное Borsh framing metadata; без влияния на transfer math |
| Vault | TransferFeeAmount (2), ImmutableOwner (7) | Размер, withheld amount, состояние и authority options |

Неизвестные и остальные extensions отклоняются, даже если выглядят выключенными.
Transfer hooks, confidential transfers, permanent delegates, default account state,
nontransferable, memo/CPI restrictions не интерпретируются как обычный перевод.
Проверка mint/vault не сертифицирует пользовательские source/destination accounts.

Комиссия Token-2022 округляется вверх и ограничивается cap. Обратный расчёт находит
минимальный gross, дающий нужный net, включая 100% с cap и проверку u64 overflow.
Сначала применяется входящая transfer fee, затем CPMM trade/creator math, затем исходящая
transfer fee. Slippage сравнивается с gross списанием и net получением пользователя.

`amount` Token-2022 account уже содержит доступные токены без withheld. Вычитать withheld
из него повторно нельзя. Входящая комиссия отдельно прибавляется к withheld vault;
исходящая удерживается у получателя. HarvestWithheldTokensToMint обнуляет накопленный withheld отслеживаемого vault без
изменения spendable amount. Его вклад учитывается в runtime-порядке, включая повторные
harvest внутри одной транзакции. Полный mint-wide withheld total и прямые withdrawals
не реконструируются. Неизвестные операции не исчезают как «нулевое изменение reserve».

## Версия снимков и границы времени

Старые six-account checkpoints/schema 1 остаются classic-only. Новый pilot явно выбирает
`--token-2022`; его immutable spec требует schema 2 и семь accounts в **одном** запросе:
pool, config, два vault, два mint, Clock. Проверяются owner и совпадение Clock.slot с bank
context. Тариф выбирается по Clock.epoch, а не по текущей эпохе в момент позднего backfill.

В интервале проверяются неизменность mint base/config, экономических параметров и эпохи.
При переходе эпохи требуется дополнительная сегментация; текущий аудит отказывает вместо
догадки о моменте переключения. Не наблюдавшееся изменение config с последующим возвратом
не исключено — полный config/mint update index остаётся отдельной зависимостью.
Metadata strings не участвуют в economic fingerprint; mint base (включая supply) участвует,
поэтому даже supply-only изменение пока консервативно блокирует интервал. Withheld на самом
mint не входит в тарифный fingerprint, но withheld на vault сверяется независимо.

```sh
export MEMECOIN_RPC_URL=https://api.mainnet-beta.solana.com
mcai collect-pilot --store work/token2022/pilot.sqlite --source-alias solana-public \
  --token-2022 --pool POOL --rounds 3 --interval 15 --rpc-interval 2 --budget 30
mcai recover-pilot --store work/token2022/pilot.sqlite --source-alias solana-public \
  --budget 100 --rpc-interval 2
mcai audit-protocol --store work/token2022/pilot.sqlite --out work/token2022/report.json
```

Флаг задаётся при создании нового pilot, старые journals не преобразуются. Проверка с
пропусками остаётся неуспешной. Raw receipts не переписываются, snapshot codec не превращает
исторические данные в доступные агенту до фактического получения.

## Первичные источники

- [Token-2022 transfer-fee interface](https://github.com/solana-program/token-2022/blob/5bc7ea3b9568b9fdac2d08c921d73d40260249c7/interface/src/extension/transfer_fee/mod.rs).
- [Token processor](https://github.com/solana-program/token-2022/blob/5bc7ea3b9568b9fdac2d08c921d73d40260249c7/program/src/processor.rs).
- [Raydium transfer helpers](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/utils/token.rs).
- [Официальное руководство extensions](https://www.solana-program.com/docs/token-2022/extensions).

Pinned source revision не доказывает соответствие deployed binary. Протокольная сверка
не снимает execution/dataset qualification gates и synthetic-only ограничение replay.

## Mainnet-проверка, завершена 2026-10-02 (Europe/Moscow)

Снимки получены 2026-10-01, 23:45:20–23:46:13 MSK: три диагностических пула,
9 checkpoints, 6 интервалов, receipt span 52.948 секунды. Это не полная мемкоин-вселенная;
классификация и репрезентативность токенов не доказаны.

- 382 транзакции: 157 failed, 132 exact-input swap с ненулевой creator fee,
  8 harvest-to-mint и 85 успешных транзакций без поддержанной pool/vault операции.
- 58 swap с входящей transfer fee, 65 с исходящей; ещё 9 без transfer fee.
  Активный тариф проверенного fee-bearing mint — 300 bps. Creator fee проверена
  на входе и выходе. Все суммы совпали с событиями и CPI transfers до atomic unit.
- Все шесть конечных экономических состояний и withheld balances совпали. Один пул
  дал только пустой и failed-only интервалы: его наличие не подтверждает swap math.
- 18 address scans завершены, 88 block orderings получены, pending jobs нет.
  503 captures сохранены. 19 HTTP 429 и один общий transport SourceError не удалены;
  после замедления и recovery все необходимые данные получены. Публичный RPC не
  квалифицирован для непрерывного широкого сбора; полной телеметрии прерванных
  запросов нет, поэтому точное число всех RPC attempts не заявлено.

Первоначальная модель этапа исключала harvest; реальные транзакции показали её
неполноту. Без harvest конечный withheld ошибочен при правильных spendable balances.
Добавлен переход, который обнуляет только известный накопитель отслеживаемого vault.
Полная сумма на mint после harvest других accounts не подставляется удобным допущением.

Freeze journal (~22.6 MB) и полные отчёты находятся вне Git. Freeze/export повторно
проверены; dev и installed wheel выдали побитово одинаковый protocol report. Прежний
classic archive и 44 classic swap также проходят. 160 тестов включают реальные creator/
transfer-fee/harvest sequences и негативные проверки epoch/mint/withheld изменений.
Краткие hashes и точные границы: [evidence](evidence/token2022-2026-10-02.json).

**Пока нет mainnet-подтверждения:** exact-output с Token-2022, liquidity changes,
pool fee collection, epoch-boundary реконструкция и прямой withdraw withheld. Их
нельзя объявлять проверенными из-за общих unit tests. Полные config/mint history,
независимая source completeness и execution admission/inclusion остаются отдельными gates.

Расширение следующего этапа: opt-in parameter history и epoch schedule реализованы
в [parameter history](parameter-history.md). Описанные выше запреты на изменение
эпох/параметров остаются для прежних journals без dependency coverage. Реальный
epoch-boundary и смена тарифа всё ещё не наблюдались в опубликованной выборке.
