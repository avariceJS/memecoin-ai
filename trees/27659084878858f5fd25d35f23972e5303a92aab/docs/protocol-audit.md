# CPMM: протокольные переходы и проверка котировок

## Реализованная область

`cpmm.py` — целочисленная экономическая модель Raydium CPMM для classic SPL:
exact-input/output, совместное округление trade/creator fee на входе, creator fee
на выходе, доли protocol/fund, deposit/withdraw и сбор трёх видов комиссий.
Комиссии LP остаются в торгуемых резервах; protocol/fund/creator counters исключаются.
LP supply означает внутренний счётчик пула, не текущий supply mint после внешних burns.

`protocol.py` связывает эту математику с raw account/transaction evidence. Он начинает
с левого finalized checkpoint, использует slot/block order существующего vault-аудита,
строит дерево CPI по stackHeight и сопоставляет runtime logs с CPMM/Token invocations.
Результат сверяется с Anchor events, точными SPL transferChecked и каждым pre/post
balance. Правый checkpoint проверяет все девять экономических величин: два vault,
шесть fee counters, LP supply. Он **никогда не исправляет** рассчитанное состояние.

Failed transaction не меняет экономическое состояние. Успешный маршрут без вызова
пула не считается swap. Дополнительный trailing account у swap допустим: pinned
handler не использует remaining_accounts. Неизвестные pool instructions, прямые
необъяснённые vault operations, отсутствующие/усечённые логи, неизвестная глубина CPI
и caught CPI failures останавливают интервал. Это консервативные ограничения adapter,
не утверждение, что такие транзакции невозможны на сети.

Старый decoder/checkpoint schema и архивы не менялись. Структурные pool fields и config
сравниваются между endpoints; изменения не интерполируются. Стабильность config внутри
интервала остаётся предпосылкой: его полный журнал пока не собирается.

## Воспроизводимый запуск

```sh
mcai audit-protocol \
  --store work/forward-pilot/frozen-final/journal.sqlite \
  --out work/protocol-audit.json
```

Команда read-only, без сети. Новый файл создаётся эксклюзивно. Exit 0 означает успешную
**условную сверку экономического состояния**, не квалификацию исполнения. Report содержит
hash исходного vault-аудита, hashes всех зависимых decoder modules, pinned upstream revision,
операции и ошибки каждого интервала. Raw журнал сохраняется прежним immutable export-pilot;
его manifest проверяется `mcai verify-pilot`. Перезапуск аудита на неизменном коде даёт
идентичный report, в том числе из установленного wheel.

Компактный `tests/fixtures/cpmm_protocol` (~457 KB) хранит неизменённые real captures,
receipts и hashes. Это regression fixture с явно описанной выборкой, не полный dataset.
Полный журнал (~8.7 MB), дополнительные diagnostic captures и подробные reports вне Git.
Краткий результат и hashes — [evidence](evidence/protocol-2026-10-01.json).

## Фактический результат 2026-10-01

Замороженный forward pilot: 189 транзакций, 143 failed, 44 swap (42 exact-input,
2 exact-output), 2 успешных маршрута без pool operation. Все 44 рассчитанные суммы
совпали с событиями и transfers до atomic unit. Во всех 8 интервалах совпали vaults,
protocol/fund/creator counters и LP supply. Creator fee в проверенном активном пуле
отключена; неизменность creator counters не является проверкой начисления этой комиссии.
В данных этого pilot нет deposit/withdraw/fee-collection операций.

Дополнительный bounded mainnet probe: из последних 1000 program signatures взяты
24 успешные транзакции через детерминированный шаг по времени; 25 RPC calls с паузой
2 секунды (одна страница + 24 transactions). Выбор диагностический, не universe для ML.
Встречены 13 CPMM swap и 11 транзакций без CPMM swap. Четыре пула с ненулевой creator fee
проверены отдельными account reads: все используют Token-2022; строгий classic-only
допуск не расширен. Логи других программ с тем же Anchor event discriminator не являются
CPMM evidence — программа и invocation обязательны для атрибуции.

Это оставляет creator fee, liquidity changes и collections на уровне source-derived
реализации с инженерными тестами, **без подтверждённых mainnet переходов**. Не следует
считать их эмпирически откалиброванными из-за прохождения тестов.

## Что эта проверка не доказывает

- Полное pool state: oracle/observation account и recent_epoch не реконструируются.
- Эквивалентность pinned исходников deployed binary, независимую правду второго provider,
  полноту адресного индекса, universe births/deaths или memecoin-классификацию.
- Произвольную исполнимость транзакции: подписи, права/балансы пользовательских accounts,
  Clock sysvar, ATA, rent, network/priority fees, latency, конкурентное inclusion и MEV.
  blockTime применяется лишь как диагностический lower-bound gate относительно open_time.
- Историческую доступность данных агенту. Backfill receipt остаётся фактическим поздним
  receipt; реконструкция истинного состояния не превращает его в исторический feature.
- Контрфактуальное исполнение новой стратегии, её capacity или edge.

`transaction_execution_qualified=false`, `market_dataset_qualified=false`,
`full_pool_state_reconstructed=false` и synthetic-only reference replay gate сохранены.
Реальные swaps здесь — проверка условной котировки на восстановленном pre-state.

## Источники

Все ссылки закреплены на revision `59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92`:

- [Calculator](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/curve/calculator.rs),
  [fees](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/curve/fees.rs),
  [constant product](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/curve/constant_product.rs).
- [Swap input](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/swap_base_input.rs),
  [swap output](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/swap_base_output.rs).
- [Deposit](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/deposit.rs),
  [withdraw](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/withdraw.rs),
  [creator collection](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/collect_creator_fee.rs),
  [protocol collection](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/admin/collect_protocol_fee.rs),
  [fund collection](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/admin/collect_fund_fee.rs).

Существенное отличие от generic reference CPMM: объединённая комиссия на входе сначала
округляется вверх, затем creator-доля выделяется вниз; при creator fee на выходе другой
порядок вычислений. Rounding deposit также сохраняет нулевой floor вместо округления
sub-unit amounts до единицы. Общая формула constant-product сама по себе недостаточна.
