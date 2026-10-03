# D49: квалификация bounded collector для frozen D48

## План до реализации и сетевых проверок — 2026-10-03

Проверяется существующий `work/sampling-d48/proposal`; его календарь, F01, D43,
source/feature/target semantics и reserved holdout не меняются. Source root proposal
не создавать. Никакого launchd install/load, длительного collector или training.

1. Read-only Alchemy MCP: app metadata и usage; не считать их доказательством runtime
   credentials, Free tier или zero-dollar hard limit. Официальные CU/throughput/pricing
   сверить отдельно. Не платить, не подписывать, не менять account/plan.
2. Один HTTP qualification attempt: ≤6 finalized getMultipleAccounts (2×3 fixed pools),
   existing persistent transport/profile2, timeout15s/response≤1MB, inter-round5s,
   ≤100s parent deadline, nominal≤120CU. Это отдельная разрешённая bounded диагностика,
   не расход proposal60480 и не F01 corpus. Первый transport/protocol/freshness failure
   останавливает attempt без retries/fallback. Raw/actual receipt/availability сохранить.
   Полное180RPC live окно не запускать без подтверждённого monetary режима.
3. Новый изолированный collector qualification package: durable reservation/restart,
   due-only/no catch-up/8 failures/600s wall/180RPC и exact staged activation binding.
   Проверять adversarial fixtures, без редактирования frozen runtime пакетов.
4. Runtime feasibility: полный180-call synthetic raw integration и offline replay,
   реальные filesystem/RAM/runtime metadata, response/storage и CU ceiling по месяцам.
   Snapshot Clock/freshness не доказательство полноты исторических данных или uptime84days.
5. Staged activation artifact связывает proposal/spec/runtime/interpreter/references,
   output root, credential fingerprint, budget evidence; все permissions false.
   Отсутствующий тариф/hard cap/account identity/service readiness — явные blockers.
6. Проверки, evidence, Memory Bank, commits/push. Отдельное итоговое решение после D49
   необходимо для установки/запуска. Readiness F01 и holdout release остаются закрыты.

Сбои сохраняются с безопасной категорией; сетевой budget не возобновляется после сбоя.
Критерий ready: подтверждены все source/quota/zero-money/storage/runtime/scheduler/binding
условия, не только успешный RPC. Установленный сервис и устойчивость календарных окон
не выводятся из synthetic тестов. Неподтверждённое условие означает NOT READY.

## Результат — NOT READY FOR ACTIVATION

План `4af4ae9`, реализация `b1ad81a`.
[Curated evidence](evidence/f01-collector-qualification-2026-10-03.json).
D48 proposal остаётся прежним: T=2026-10-07 00:00UTC,336окон,60480RPC ceiling;
source root не создан. D43/F01 и protected holdout не читались/не менялись.
Долгосрочный collector не установлен и не активирован, research/holdout permissions false.

| Проверка | Факт | Статус |
|---|---|---|
| Runtime source | 6/6 strict finalized checkpoints fixed3pools | PASS для короткого probe |
| Freshness/coherence | Same-request dependencies+Clock, возраст9,95–10,77s при45s bound | PASS в наблюдённом интервале |
| Quota arithmetic | 1 209 600CU всего; max432 000CU/месяц | Малый относительно public Free; account allowance не доказан |
| Monetary | Usage MCP $0,00; тариф и hard limit не возвращены | BLOCKED |
| Storage | Свободно135,67GB; shape projection2,65GB; raw ceiling100,8GB | Условно feasible; места/guard не зарезервировано |
| Scheduler | Due-only, durable reservations, crash/no retry,8failures, STOP/lock/clock guards tested | PASS инженерного engine |
| Service/runtime | Hard child kill tested; calendar supervisor210s/600s, uptime/sleep/login не квалифицированы вместе | BLOCKED |
| Exact binding | Sealed candidate, wheel verification, no permission | Staged; отдельного activation record нет |

### Реальный источник и независимое воспроизведение

Alchemy MCP admin доступен; приложение `nsvflslm1kqtntvz` имеет Solana mainnet.
Runtime credentials отдельно прочитаны из существующего `.env.local`, не выводились,
не копировались в artifacts/Git. MCP select_app/account updates не выполнялись.
HTTP persistent v1 с прежним decoder profile2/finalized выполнил **ровно6RPC** за7,42s.
Cold response0,600s; ещё5responses0,115–0,136s с retained connection. Response sizes
3524/3699/3701bytes; всего21 848bytes. Все6slots advanced, protocol/freshness failures0.
Это одна короткая проверка; полнота любого historical recovery, межзапросная атомарность,
устойчивость180RPC окна или84day SLA из неё не следуют. Raw/receipt/availability сохранены
в `work/collector-d49/http-probe-01`, immutable manifest приложен в evidence.

[Alchemy getMultipleAccounts](https://www.alchemy.com/docs/chains/solana/solana-api-endpoints/get-multiple-accounts)
поддерживает multiple account request; наши прежние guards независимо проверяют Clock
и зависимости внутри одного response. Последовательные checkpoints разных пулов не
объявляются единым atomic cross-pool snapshot. Время availability при offline replay
берётся исходное, а не время воспроизведения; нет synthetic backfill online-доступности.
Probe выполнен на implementation worktree от plan commit; финальный reproducer закреплён
implementation commit. Эти engineering данные не импортируются в future F01 calendar.

### CU и денежная граница

[Официальная стоимость](https://www.alchemy.com/docs/reference/compute-unit-costs) —
20CU/getMultipleAccounts.180RPC=3600CU/окно;336окон=1 209 600CU. По UTC calendar:

| Месяц | Окон | RPC | CU |
|---|---:|---:|---:|
| 2026-10 | 100 | 18000 | 360000 |
| 2026-11 | 120 | 21600 | 432000 |
| 2026-12 | 116 | 20880 | 417600 |

Номинально3RPC/5s≈12CU/s;3requests=60CU, existing core additionally spaces calls≥1s.
[Throughput](https://www.alchemy.com/docs/reference/throughput) общий для account и
оценивается10s token bucket: other apps/D43 также потребляют quota. Public Free allowance
30M CU/month и300CU/s из [pricing](https://www.alchemy.com/docs/reference/pricing-plans)
не являются доказательством фактического тарифа этого account.

MCP usage до/после:27 540→27 660CU, delta120CU ровно соответствует6×20. Billing period
2026-10-01..2026-11-01, freshness dataThrough16:30→16:41UTC, reported USD0,00 в обоих
ответах. Это согласованное косвенное evidence; округление USD, sole app и delta не
доказывают exact credential→account mapping или zero-dollar hard cap. Masked get_app
key не использован как доказательство полного совпадения ключа. MCP account summary
**не содержит plan tier и account usage limit**. Дополнительная bounded UI проверка через
browser skill вернула `No browser is available`; discovery list пуст. Billing page
не открывалась, account settings не менялись. Категория `browser_runtime_unavailable`
сохранена в `work/collector-d49/billing-access.json`. Неизвестность не заменена словом Free.

Открытый [PAYG FAQ](https://www.alchemy.com/docs/reference/pay-as-you-go-pricing-faq)
указывает0,525USD/M CU: sensitivity для всего proposal≈$0,63504, **не разрешённая трата**.
Search snippet с0,45 устарел относительно открытой страницы и не используется.
Paid budget остаётся **$0**; оплат/upgrade/account mutations0. Full180RPC live окно
не запускалось. Для activation нужен проверяемый account-specific Free/no-charge режим,
его соответствие runtime credential и доступный shared allowance. Никаких секретов
в чат для этого не требуется; billing evidence получать read-only в доступном account.

### Engine, failure semantics и пределы квалификации

Новый stdlib `memecoin_collector` сохраняет bytes всех frozen D43–D48 packages.
Scheduler qualification engine: exact source root/schedule/proposal identity, flock,
exclusive fsync reservation перед worker, hash-chain terminal journal, idempotent reads.
Pending не собирается; due+900s inclusive допустим, позже missed/0RPC без catch-up.
Crash после создания окна расходует полные180RPC и становится failed без resume.
Partial JSON не «чинится» молча: fail-closed, исходные bytes сохраняются.
Первый worker error закрывает окно; после8failed новые окна не запускаются.
Clock regression, binding/journal changes, STOP, concurrent writer проверены до продолжения.

Window adapter использует existing create/resume/verify/export без изменения math,
targets, cadence, histories или availability. Failed segment не вызывает второй segment.
Frozen600s checks сохраняются. Отдельный killable subprocess boundary реально тестировался
на зависшем child: exit124, partial bytes остались, процесс завершён. Это **ещё не доказанный
production supervisor** с совместно работающими hard210s/segment и600s/window deadlines.
`collect_window` — внутренний adapter; вызывать его напрямую в долгосрочном сервисе нельзя.
Scheduler callback сам должен проверять raw/seal; scheduler status не является ML admission.
D48 registry независимо перепроверяет completed window перед импортом.

Не создана команда activation и не установлен LaunchAgent. Перед выпуском production
service необходимо связать supervisor, проверку immutable candidate/runtime перед каждым
окном, storage floor/stop, credential/budget review и безопасные operational summaries.
При неполном journal/изменённом runtime — остановка для incident review, не auto repair.
Owner, способный переписать весь ledger вместе с hashes, не исключён этой схемой;
head/candidate SHA нужно pin отдельно. Никакие рыночные outcomes не нужны для status.

### Feasibility, exact binding и тесты

На текущем Mac M4 Pro24GiB свободно135,67GB. Один full synthetic source window занимает
4 738 917bytes: линейная projection source336+registry raw copies224≈2,65GB. Это **shape
projection**, не upper bound: raw response ceiling1MB даёт60,48GB source и100,8GB с копиями
development в registry, ещё без metadata/converted/dossiers. Максимум response storage
помещается в текущий free space, но будущая чужая запись/рост outputs/sleep не контролируются.
Нужны disk guard/reservation policy и проверка host uptime; pmset metadata сохранена в
`work/collector-d49/environment.json`, настройки power/login не менялись.

Полный synthetic fixture180fake calls прошёл новый scheduler/window→D48 admission→raw
conversion/dossier за4,10s wall time теста. Synthetic clock исключает реальное
ожидание5min: это **не** измерение сетевой скорости или устойчивости calendar runtime.
После installation wheel без исходного checkout в import path точно воспроизведены
6live checkpoints, synthetic raw/dossier и прежний пустой actual D48 dossier.
Raw conversion допускает только прежнее исключение physical conversion path.
Audit hooks запрещали network/D43 opens при fixture/reproduction. Actual F01 windows0.

`work/collector-d49/activation-candidate` — sealed review artifact. Candidate SHA:
`e4100009208e0b7b563d6aa013fe4227f847be15d39e26ba44ad26cb1c184f21`.
Он связывает exact proposal/spec/source root, package sources, Python executable bytes/
version/platform, reference captures, credential fingerprint, budget/evidence/tool files
и wheel artifact. Без ключа/полного URL. Все permissions false, activation_decision=null.
Это конкретный проверяемый input следующего решения, не разрешение и не deployed runtime.
Подмена credential, evidence, runtime или rehashed permission отвергается тестами.
Изменение кода потребует **нового** candidate с сохранением нынешнего.

16new tests;481base+2optional skips;502combined. Ruff/format,strict mypy,make smoke/build
прошли. Сохранены logs/fixture/raw/wheel artifacts. При разработке lint обнаружил стиль
lambda declarations; static mypy — неверную сигнатуру verify_execution_targets(parent,
output). Исправлены до full fixture; сетевых retries/extra probe не было. Небольшие
lookup ошибки отсутствующих doc paths не имели отношения к provider/runtime отказам.
Remote CI не проверен.

## Решение и следующий D50

**Не активировать proposal сейчас.** HTTP/profile2 пригоден для bounded snapshot source
по наблюдённым данным; engine/ingestion/reproduction conformance подтверждена. Но тариф,
zero-dollar cap, exact billing mapping и production service envelope пока не подтверждены.
F01 dataset adequacy, conditional labels/economic uncertainty и holdout gates прежние.

D50 — закрыть operational activation blockers: read-only account/budget evidence;
production supervisor210s/600s +disk/runtime guards; один отдельно зарегистрированный
full180RPC qualification window только после подтверждения no-charge режима; staged service
и restart/sleep/clock semantics. Затем отдельное итоговое решение о запуске именно этого
binding. Если календарь успеет начаться, missed остаются missing; никаких сдвигов T или
ретроспективного заполнения. При необходимости нового calendar — новый versioned proposal
и сохранение старого. D43 не переустанавливать, protected data не открывать, обучение не начинать.
