# D50: operational supervisor frozen F01 source

## План до реализации — 2026-10-03

D49 принят пользователем; продолжается core roadmap. UI/control-center worktree не
менять. Код в core main, новый package memecoin_operational сохраняет fingerprints
D43–D49. Existing D48 proposal/calendar/source root и D49 candidate не переписывать.

1. Одна bounded read-only проверка account usage/billing доступа. No-charge/credential
   mapping нельзя вывести из USD0. Без account-specific evidence — реальные RPC0;
   условный budget одного отдельного live qualification180RPC/3600CU, без retries.
2. Реальный subprocess supervisor:210s segment /600s whole window; process group kill,
   parent-death fencing, restart no retry, STOP,disk floor,monotonic+wall clock guards.
   Disk floor2GiB + reserve512MiB/window; bounds принадлежат operational envelope,
   не меняют frozen scientific semantics или60480RPC ceiling.
3. Immutable staged runtime bundle: frozen packages/interpreter/references/proposal,
   exact artifact/source/credential pins, runtime guards before child and each RPC;
   separate budget review and activation receipt. Никакого install/load/activation.
4. Pure/fault fixtures и полный180fakeRPC subprocess integration→D48 admission→offline
   reproduction. Ускоренный synthetic clock явно маркирован; real transport0,training0.
   Проверить timeout/parent crash/kill/restart/tamper/low disk/clock/partial publication.
5. Staged launchd plist и due-only operational entrypoint (default gate closed), без
   регистрации сервиса. Read-only host metadata не равно доказанному awake uptime.
6. Полные checks/wheel reproduction, concrete verdict, Memory Bank, commits/push main.

First error closes window; no catchup/backfill/calendar shifts.8failed stop. Raw/реальные
availability не меняются, protected holdout не открывается. Нет торговли, upgrades,
платежей, QuickNode, изменения D43, F01 training или освобождения holdout. Если access
не появился — завершить всю независимую инженерную часть и явно перечислить blockers.

## Фактический результат

**Engineering envelope qualified; activation остаётся BLOCKED.** План0be583f,
implementationbee897e,integrity8d072ac.
[Evidence](evidence/f01-operational-runtime-2026-10-03.json).
D50 выполнен в core main `/Users/root1/Desktop/memecoin-ai`. Ветка/worktree control-center
не редактировалась этой работой и не включалась в main. При финальной проверке там
обнаружены параллельные изменения (HEAD5bf1700 и untracked apps/control-center/web);
они оставлены без вмешательства. Source packages D43–D49, F01 spec и D48 calendar
не изменены. Actual F01 windows0; новый source root отсутствует; launchd не установлен.

### Что реализовано

Новый stdlib пакет `memecoin_operational` отделяет supervisor от frozen scientific code.
Sealed runtime bundle содержит копии всех нужных Python sources, proposal/spec и public
reference captures. SHA pin manifest задаётся отдельно. Сверяются full inventory,
imported code identity, interpreter bytes/version/platform, reference/source binding;
worker использует frozen runtime с `-B -S`, без editable checkout/site packages.
D49 candidate не перезаписан и всё ещё валиден; для D50 создан новый artifact.

Реальные отдельные процессы выполняют init,segment0,segment1,QA. Поддержаны:

-210s hard child deadline на segment;600s whole-window budget **от durable reservation**,
  включая подготовку до запуска worker. Остаток общего времени ограничивает каждый child.
  Проверка после child закрывает гонку завершения между ticks supervisor.
- Process group SIGKILL и wait при deadline/guard failure. Lease pipe имеет writer только
  у родителя; daemon watchdog worker завершает процесс при EOF после смерти родителя.
  Перед RPC дополнительно проверяется parent PID. Core workers не создают descendants.
- Прежний due-only scheduler:180RPC reservation до работы; crash/interrupt не retry;
  missed остаются missing;8failed останавливают schedule; no catch-up/backfill.
- Перед каждым RPC: exact bundle/code,credential/budget review,scope/path/reservation,
  STOP,disk headroom и host clock. Всего≤90RPC/segment; только fixed reference requests
  getMultipleAccounts. Ambiguous failure расходует попытку; первый error закрывает окно.
- Disk headroom≥2GiB+512MiB, window files≤512MiB; symlinks отвергаются. Это operational
  stop guard, **не filesystem quota/атомарная reservation места**. Конкурентная запись
  другого процесса и небольшой overshoot одной записи не исключены; OS errors fail closed.
- Сравниваются wall/monotonic progression и deadline. Regression/divergence>2s прекращает
  работу; просыпание после deadline закрывает окно. Эти дополнительные stops не исправляют
  временную ось и не отменяют исходные feature/target freshness/maturity guards.

Successful worker stdout не содержит рыночных labels; safe categories/exit codes,
фактические durations и исходные partial bytes сохраняются. Pre-worker исключения также
пишутся в отдельный supervisor-error; staged service имеет stdout/stderr paths вне bundle.
D48 admission независимо воспроизводит raw→features/targets. Восстановление scheduler
не создает прошлую availability и не разрешает resume частично собранного окна.

Границы гарантий: kill checks дискретные, с polling50ms и обычной OS scheduling/I/O
задержкой; это не real-time OS SLA. Parent-death fence и process group kill проверены
на реальных процессах, clock/sleep divergence — fault fixtures. Реальный sleep/reboot
Mac и установленный launchd service **не проверялись**, чтобы не воздействовать на D43.
Наличие launchd plist не подтверждает awake uptime84days или доступность сети.

### Budget/activation boundary

MCP usage сейчас27 660CU и reported USD0,00, dataThrough2026-10-03T19:10UTC. Это прежний
account usage без новых market RPC. Plan tier, account hard monetary cap и exact mapping
runtime credential→billing account инструмент не вернул. Browser skill проверен один раз:
`No browser is available`, discovery `[]`. Категория `browser_runtime_unavailable`,
повторных попыток доступа/оплаты/upgrade нет. **Live180RPC qualification не проведён.**

Receipt API принимает только отдельно pinned decision и evidence. Нужны exact bundle/
proposal/credential identity, account Free/no-paid-overage assertion с привязанным
read-only billing/mapping evidence, ограниченная validity и CU allocation. Никакого
receipt generator, automatic approval по `$0.00` или public Free allowance нет.
SHA проверяет bytes/связи, но сам по себе **не доказывает правдивость billing review**:
нужен substantive review владельцем доступа; shared quota не резервируется у Alchemy
этой локальной записью. Paid limit остаётся0; никакой PAYG fallback не реализован.

Scope `bounded_qualification` имеет180RPC и exact единственный attempt root; повторный
каталог не может переиспользовать тот же receipt. Calendar scope имеет60480RPC и exact
D48 source root/windows/index/due/start. Review expiry/rotation/tamper закрывает работу,
а новые правила/даты/источники не появляются автоматически. Launch/release F01 не связаны
с collection receipt; ни одного настоящего approval receipt D50 не выпустил.

### Артефакты и воспроизведение

Итоговый `work/collector-d50/runtime-candidate-final`, manifest SHA:
`a06f1d69ff8fb1c0f8647fc5622c3e2d8c1811e697d40eb8be15deb1663da880`.
Sealed candidate сохраняет D48 proposal, source root и все научные правила. Credentials
внутри отсутствуют: только fingerprint и путь существующего settings file.
`ai.memecoin.f01.prospective01-final.plist` проходит `plutil -lint`; activation receipt
и его hash оставлены отсутствующими/`PENDING_FINAL_DECISION`. Сервис не регистрировался.

Контрольный вызов service из installed wheel без receipt: exit1,
`runtime_or_receipt_invalid / FileNotFoundError`; actual source root не создан.
`launchctl print` подтверждает отсутствие нового service. Старый D43 job не трогали.
Этот отказ ожидаем и не является failed F01 window: reservation ещё не создавалась.

Финальный fixture `work/collector-d50/raw-fixture-final-02` реально выполнил4процесса,
180fake RPC, supervisor wall5,828s; полный pipeline с D48 admission/conversion/reproduction
8,435s. **Виртуальные5s ожидания**: эти durations не network performance и не time-to-market.
Все времена Clock/slot/receipt явно synthetic; исходные3reference responses — старый D40.
Никакого экспорта этих наблюдений в actual prospective registry. Предварительные2успешных
fixture×180fake calls сохранены; суммарно540fake, market RPC0, research training0.

Installed wheel точно воспроизвёл final raw conversion (кроме прежнего physical conversion
path), dossier и пустой actual D48 dossier; проверил final runtime bundle и старый D49
candidate. Ни protected holdout, ни D43 raw для этого не открывались. Внешние raw/wheel/logs
и pin manifest перечислены в curated evidence; large artifacts остаются вне Git.

15new property/fault tests;496base+2optional skips;517combined. Ruff/format,strict mypy,
make smoke/build успешны. Проверены реальные kill/group/orphan/partial bytes, final deadline
race и reservation origin, guards, zero-USD non-admission, expired/rotated/wrong-scope
receipts, immutable code/pins, first error/no second segment, one-receipt-one-attempt,
calendar path/due binding. D49 tests сохраняют restart/idempotency/8failure coverage.
Remote CI не проверен.

История разработки сохранена: fault-test сначала поймал post-child deadline race;
поздний review — начало whole deadline от launch вместо reservation. Исправлено без
ослабления600s; prior bundle/raw artifacts сохранены отдельно, final имеет новый SHA.
Lint ошибки строк/форматирования исправлены, tests-first/check-01 logs не удалялись.

## Решение и следующий D51

Принять D50 engineering envelope; **не активировать сбор сейчас**. Технический блокер
supervisor/disk/runtime binding D49 закрыт инженерным evidence. Сеть с реальными данными
и эксплуатационная доступность Mac этим evidence не квалифицированы.

D51 — получить account-specific no-charge/mapping evidence через доступный read-only
billing интерфейс либо несекретное подтверждение от владельца доступа; substantive review
и отдельный bounded receipt для одного180RPC qualification. Провести его по этому runtime,
оценить freshness/coherence/coverage/deadlines и независимо воспроизвести raw. Зафиксировать
условия awake/login/disk monitoring, staged final service/receipt и **отдельное итоговое
решение** о calendar activation. При новых code changes — новый bundle/pin, не rewrite.
Если доступ не появится, live остаётся `не проведён`; не повторять transport tuning и не
подменять evidence искусственными approvals. T не сдвигать молча, пропуски не backfill.
D43 и reserved holdout сохранить; настоящее F01 обучение по-прежнему закрыто.
