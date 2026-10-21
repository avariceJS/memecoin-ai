# Founder Desk — Brain Engineering Organization

Новый A10 вертикальный путь принят Sol в source-only synthetic объёме. Controller создаёт планы
отделов только по eligible fixed-card меню через явно инъецируемый transport; полный trusted card
hash (включая scope/verifier) проходит через nomination и повторно проверяется при admission.
Проверены fencing проигравшего intent creator, STOP/menu drift, commit-time Store revision, scope-only
drift, истёкшая lease и replay до и после commit плана без повторной отправки. Интеграционная цепочка
создаёт Experience-план и выбирает PLAT-001 внутри `supervise`, затем тем же supervisor-путём
обрабатывает Platform после contract/dependency перехода. Полный gate: Ruff/format, 665 тестов и 193
subtests; smoke reproducible, `edge_established=false`. Sol принял diff read-only, но тесты не запускал.
Injected transport не квалифицирует модель/usage; `outcome_unknown` сохраняется без resend и требует
отдельного ремонта; replay ограничен 100 journal identities; A03, A04 runtime/deployment и
operational release открыты. Отдельный `make check` останавливается на 107 существующих Ruff
findings в `src/`, `tests/`, `tools/` и не доходит до последующих проверок.

Новый integrated-cycle checkpoint доводит PLAT-002 через настоящий loop `supervise`: synthetic
launcher вызывает реальный `run_attempt`, следующая итерация сама выполняет независимую `verify_one`,
trusted acceptance, reviewer и GitGate integration. Точный receipt, parent PLAT-001, active-ref CAS,
неизменность `main` и два integration events проверены без ручного вызова execution/verification.
STOP проверяется перед каждым selected-task claim и ещё раз прямо перед `Popen`; multi-slot loop
прекращает dispatch после veto. Pre-spawn STOP и синхронный `Popen` error сохраняются как
`cancelled_before_spawn`. Manifest-bound синтетический deployment запускает реальный локальный child:
он атомарно сохраняет PID/birth owner до любого provider-контекста; повторный child не может
повторить attempt. Перед каждым metered-client сохраняется `attempt_work_started`. STOP на входе
child фиксируется как `stopped_before_work` только при отсутствии work marker, provider session,
usage и candidate; этот явный no-work outcome не расходует retry. Падение до или после записи custody
остаётся unknown/expired и блокирует claim до reconciliation. Новый `child-start-v1` marker разрешает
`no_work_before_custody` только без child, session, work, usage и candidate; legacy claim без marker
остаётся на hold. Поздно запущенный child всё равно обязан пройти expired attempt fence до работы.
Без durable process-reap receipt прямой `Store.claim` и
`ready_tasks` блокируют повторный запуск; `readiness_report` показывает attempt IDs, не скрывая
независимые готовые задачи. Reaper сверяет child/session runner identity, подтверждает исчезновение
процесса или смену PID birth, затем транзакционно записывает receipt с attempt, epoch и PID/birth до
JSON marker. Неудачный kill, живой PID с unreadable identity и конфликтующая/повреждённая история
оставляют claim заблокированным. После падения до receipt claim остаётся закрыт; после receipt, но до
marker, reopen восстанавливает marker и допускает один следующий epoch. Reaper требует macOS process
witness. Receipt — доверенное утверждение локального reaper, не аутентифицированное доказательство:
прямой Store/SQLite writer может его подделать. Legacy expired attempts без receipt остаются на hold
до подтверждённого reconciliation.
Синхронный `Popen` failure теперь атомарно отменяет unstarted attempt и сохраняет per-task
exponential cooldown до пяти минут. Claim, readiness и supervisor соблюдают deadline; независимая
готовая задача продолжает работу, planning вызывается один раз на изменившееся durable state, а
pending contract reviews обрабатываются до ожидания ближайшего cooldown даже при отдельных dependency
blockers. Child custody сбрасывает streak, бюджет attempts не расходуется.
Каждый нормальный bounded exit или выход по STOP теперь сохраняет idempotent passive episode
checkpoint: identity deployment root, STOP bytes, quiescence revision/readiness digest, exit reason и
ближайший retry. Runtime/accounting/quota/process остаются `unknown`, действие всегда `hold`; read-only
projection может показать `due_for_recheck`, но не вызывает следующий episode, не читает
`launch_request` как право запуска и не выдаёт dispatch authority. Новый episode — только явный вызов
`supervise`, который заново проверяет обычные gates. Synthetic process exit до checkpoint call и после подтверждённого commit, STOP, stale readiness/revision
и отдельный повторный episode с прохождением cooldown и интеграцией PLAT-002 покрыты.
Полный gate: Ruff/format, 649 тестов и 193 subtests; `make smoke` воспроизводим,
`edge_established=false`.
Worker/reviewer остаются синтетическими; падение до регистрации владельца остаётся неизвестным,
это покрывает только синхронную ошибку до создания процесса; неоднозначный child start требует custody
recovery. Это не qualification A04, provider/usage settlement,
A03 или operational release.

A10 теперь связывает department planning с реальным fixed-card admission: planner видит карточку только
если текущий fenced plan её команды ссылается на `catalogue-card:<id>` в рамках той же цели. Открытый
конфликт по точным plan revisions удерживает nomination, но не меняет readiness уже существующих
независимых задач. Admission в одной SQLite-транзакции повторно сверяет team, objective, owner epoch,
plan revision/digest, evidence refs и conflict state; scope, контракты, verifier и полномочия остаются
из trusted card/controller. Публичный `admit()` также требует nomination и повторяет проверки, поэтому
фиксированную карточку нельзя провести в обход department-plan boundary. Тест PLAT-001 Experience →
contract reviews → PLAT-002 Platform проверяет provenance в admission и интеграцию, а snapshot/restore
сохраняет department plans; exact persisted nomination теперь включается в независимый review packet
под canonical packet digest и сверяется с admission record для обеих команд в интеграционном тесте.
Reviewer не получает исходную plan row или повторную проверку её currentness. Generic
`put_decision()` не может писать в namespace `admission/`. Новая проверка на границах supervisor
требует для fixed-catalogue task persisted nomination и точное совпадение trusted-card полей до
claim, до worker dispatch и до acceptance/recovery; off-catalogue task требует отдельный task-spec
admission. Прямые Store-only fixtures сохраняют совместимость. Controller с catalogue не выбирает
их для spawn; direct-claimed child блокируется до provider work, verifier и recovery не принимают
candidate. Idle handoff фиксирует `controller-assignment-required` с исходным Store readiness snapshot.
Проверяется зафиксированный при admission card digest, а не live lease отдела.
Sol независимо принял этот controller-boundary candidate на базе `36a2834`. Full gate: Ruff/format,
670 тестов и 193 subtests; smoke воспроизводим, `edge_established=false`. Generic `Store.claim` и
`ready_tasks` остаются Store-only совместимыми API, не универсальными admission fences; конфигурации
без `catalogue` сохраняют legacy-поведение. Прямой SQLite tampering отдельно не аутентифицируется.
Это synthetic source-only evidence, не qualification операционного A04/A03/runtime.

Новый A05 source-only вертикальный путь связывает свежий восстановленный controller с пассивной
reconciliation-командой `restore-reconcile`. В синтетической disposable-копии команда принимает
только manifest-bound controller и требует присутствующий Pilot STOP, валидный restore receipt и
host-requalification gate; она записывает ограниченный hold и возвращает bounded worklist, не
импортируя supervisor или metered provider client. Тест жёстко завершает дочерний процесс после
durable hold и освобождения owner lease, но до readback; повтор после SQLite reopen находит ровно одну
запись. Удалённый STOP и изменённый receipt закрываются fail-closed. Sol принял точный source/test diff
`df12b50414bc52abe776b3d861c25aa1d3dbf67fecd6d99011dfa6d4c5fbd562d` на базе `97894c8` без
блокеров; рекомендация включала повторную сверку источников после освобождения lease. Полный
`make engineering-release-check`: Ruff/format, 619 тестов и 189 subtests; `make smoke` воспроизводим,
`edge_established=false`. Это synthetic source recovery, не host/runtime qualification. Одинаковые
байты STOP/receipt, удалённые и созданные снова между чтениями, остаются неразличимыми; authorization
generation и lifecycle consumer отсутствуют. Полный PLAT-002 integrated-cycle test также запускает
CLI восстановленной копии на уже интегрированном Git/contract checkpoint; focused regression и полный
release gate проходят. Продолжается source-only интеграция.

Новый A10 recovery-срез проверен как синтетическая source-only вертикаль. Четыре дочерних процесса
жёстко завершаются после durable dispatch intent, после сохранения session, после возврата synthetic
review до custody ответа и после custody ответа до local replay. Первые три состояния после SQLite
reopen остаются `hold_no_resend`; параллельный независимый prepared review продвигается. Локальный
replay разрешён только для уже сохранённого ответа: он создаёт ACK и допускает активацию без вызова
transport. `provider_usage` остаётся `unknown`, resend authority отсутствует. Sol принял exact diff
`9de1e75b0409d1bb2e915c34e90848509a5aa8bd91df562a1d36671c1cd47c56`. Полный
`make engineering-release-check`: Ruff/format, 618 тестов и 189 subtests; `make smoke` воспроизводим,
`edge_established=false`. Потерянный до custody ответ остаётся unresolved; synthetic transport и
SQLite child-process crash не подтверждают provider identity/settlement, A03, runtime или live recovery.
Pilot STOP, запрет `main` и source-only граница сохраняются; работа продолжается.

### Текущая работа — 2026-10-21

Native Goal `01a15f55-903b-7de2-adec-19733734ac78` проверен как `active` и остаётся единственным
владельцем инженерной разработки после контролируемой передачи от приостановленной Goal
`01a15b1c-c263-71b0-bfca-c5b46d17a89b`; история, счётчики и handoff сохранены. Сквозной синтетический
тест проходит через настоящий `plan_next`
для двух допустимых карточек с локальными детерминированными ответами mock-модели, без live provider.
Planner основывает следующую задачу на активном GitGate ref; тест проверяет, что её base SHA равен
уже интегрированному кандидату. Допуск карточки и terminal planning decision теперь атомарны;
инъецированное жёсткое прерывание откатывает оба, а повторяющиеся JSON-ключи предложения
отклоняются. Ошибка cleanup после commit остаётся видимой вызывающему коду, не меняя durable
`admitted`. Далее путь проходит через claim и настоящий `supervisor.verify_one`: временный trusted
acceptance harness реально исполняется fixed-policy verifier-командой в readonly Seatbelt;
synthetic reviewer проверяет task, scope, diff и passing acceptance result в packet и возвращает
deterministic approval, после чего GitGate создаёт receipt и делает ref CAS. Это legacy path-only
policy: bound A04 manifest/executable identity остаются непроверенными.
Инъекция прерывания после CAS проверяет SQLite close/reopen, восстановление интеграции и claim
следующей зависимой задачи. Повреждённый
candidate SHA и отсутствующий receipt блокируют recovery без изменения Store/ref; валидное
восстановление создаёт ровно одно событие интеграции. После интеграции producer и custody одного
contract response test ставит STOP и вызывает настоящие `backup_snapshot`/`restore_snapshot` с
manifest-bound synthetic deployment; обычный, не подменённый deployment preflight проходит.
Fresh-path restore сохраняет receipt, intent и SQLite-состояние, оставляет STOP и
host-requalification barrier; обычный preflight восстановленного пути его отклоняет, а restore
receipt указывает `runtime_ready=false`. Fixture не квалифицирует настоящий runtime или потерю
хоста; скопированный controller manifest-bound, но не запускается, исходник остаётся доступен во
время restore. Producer Experience, consumer Platform и
контрактная версия связаны реально: test запускает обе durable review через idle-loop `supervise`
с явно инъецированным synthetic transport; после первой ACK consumer остаётся заблокирован,
после второй точная версия активируется. Затем supervisor планирует и claim-ит PLAT-002. Его
spawn перехвачен; после этого test явно запускает настоящий `run_attempt` и `verify_one` с
synthetic worker/reviewer. Узкий `src/consumer.py` использует интегрированный результат PLAT-001,
trusted acceptance исполняет consumer и проверяет вычисленное значение, reviewer проверяет точный
PLAT-002 packet, а GitGate/Store интегрируют второй commit. Test повторно хеширует canonical
receipt и связывает его с candidate SHA, проверяет первый parent на SHA PLAT-001, неизменность
`main` и ровно одно событие интеграции на задачу. Отдельная отрицательная регрессия двигает уже
существующий active ref после построения кандидата и подтверждает отказ публикации. Это исправило реальный
стык: planner брал базу с `research/active`, а GitGate доверял только `HEAD`; теперь GitGate
разрешает active-ref историю при неизменном `main`. Это ручное синтетическое продолжение после
перехваченного spawn, не доказательство lifecycle restart. В restored copy STOP сохраняется,
worker dispatch отсутствует. Вызов `activate_contract` на STOP возвращает без изменения review state
и не публикует contract v2; отдельный Store replay сохраняет локальную ACK без provider dispatch.
Это source-level STOP regression, но copied controller не исполняется и исходник остаётся доступен
во время restore. Packet содержит candidate SHA и declared GitGate receipt ref/digest, но Store не валидирует внешний
receipt; не admitted на момент contract review `PLAT-002` отсутствует в Platform task inventory,
поэтому тот review contract-level, а не task-specific. Обычный CLI и quiescence path transport не
получают и сохраняют inert Sol handoff; test также закрывает/переоткрывает SQLite после durable
response custody и проверяет локальный replay без повторной отправки до dispatch второго review.
Sol отдельно принял восстановительный replay test diff `f8a3f9da4fbdf7db68567690e103c84e7f886ce829f065b8b9dab97feb4b81a6`;
focused integrated test и полный gate прошли: Ruff/format, 596 тестов и 189 subtests.
Теперь controller сам готовит participant review только из integrated Store row, совпадающих attempt/epoch,
успешной verifier записи и строгой GitGate receipt; dispatch получает точный request allowlist и
повторно проверяет STOP/source непосредственно до transport start и review. Evidence revision не
меняется при появлении unrelated descendant active tip; после ACK controller повторно проверяет
request/packet/raw hashes перед признанием уже опубликованного контракта. Synthetic regressions
покрывают SQLite close/reopen между подготовкой participant requests, возобновление с тем же request
ID, STOP после записи session и самосогласованную подмену ACK hashes. RuntimeError перехватывается
контроллером: проверено durable retry после переоткрытия Store, не жёсткий crash процесса. При STOP
между begin/start или после session состояние остаётся unresolved и не посылается повторно; отдельный
repair path не квалифицирован. Sol принял точный source/test diff `960e4ed5e2614de432d21b0c43babb9389154f428b3949cddb42e2fb3fd9a81a6`; gate: Ruff/format, 609 тестов и 189 subtests.
Live provider/runtime dispatch не квалифицирован. Sol независимо проверил recovery-путь, выявил
и перепроверил исправления точной привязки родителей кандидата, receipt, attempt/epoch и канонической
записи проверки. В этом bounded source-пути после исправлений обхода не найдено.

До последней правки область mock не включала PLAT-002 `verify_one`; один focused run до обнаружения
этого мог вызвать установленный Codex reviewer. Внешний dispatch и usage settlement не установлены:
считать usage нулевым или settled нельзя, резервы не освобождались. Полный gate был остановлен после
451 теста. Owner/production ledger не менялся; test использовал временное состояние. Сейчас patch
охватывает lead, worker и reviewer и проверяет role trace; исправленный полный gate прошёл ниже.

Sol принял точный source diff PLAT-002 после исправления mock scope. `make engineering-release-check`
прошёл: Ruff, format, 596 тестов и 189 subtests; `make smoke` воспроизводим на синтетическом
временном хранилище. Ранее прерванный gate на 451 тесте был повторно пройден после mock-scope fix.
`make check` блокируется 107 существующими Ruff finding в
`src/`, `tools/`, `tests/` до остальных шагов. Worker теперь запускается через штатный supervisor
dispatch и Seatbelt tool callback; verification проходит через `verify_one`, выполняя временный
acceptance harness. Worker/lead transport, reviewer, acceptance fixture и contract reviews
синтетические. Это не квалифицирует A04 bound-manifest/executable identity, реальную модельную
экспертизу, провайдера или эксплуатацию. A03, Pilot STOP, запрет
runtime и запрет `main` сохраняются; цель организации остаётся открытой.

Восстановленная копия теперь даёт source-identified snapshot для пассивного A05 `hold`: Store
считывает fixture STOP, точный restore receipt с привязкой к `snapshot.json` и controller-parse
recovery history. Store записывает DB event sequence и время; файловые источники нельзя включить
в атомарную SQLite sequence, поэтому они читаются дважды и сверяются по hash. Перед записью Store
под `BEGIN IMMEDIATE` перечитывает файлы и DB gate, отвергает изменение источника и возраст более
30 секунд. Digest входит в planner hash и durable request; worklist заново проверяет схему и
консервативные hold observations. Тест меняет STOP между захватом и записью, истекает snapshot во
время SQLite lock wait, затем проверяет read-only worklist, reopen и fencing старого owner epoch.
STOP остаётся. Это synthetic disposable restore: runtime/accounting/quota/process/authorization
не квалифицированы, runtime жёстко считается недоступным. Consumer, стабильная authorization
generation и process/service action не добавлены; A05 и operational recovery открыты.
Ограничение: одинаковые байты STOP/receipt, удалённые и созданные снова между чтениями, неотличимы
от неизменного источника; Store adapter связывает receipt с manifest hash, но сам manifest заново
не валидирует (это делает отдельный интегрированный restore path).
Sol принял exact source diff `1122381c685f623c9d314016dbc789e6ec9ddc3a2f40765b8e656bfa3f2582e3`
без блокеров. `make engineering-release-check`: Ruff/format, 613 тестов и 189 subtests;
`make smoke` прошёл с `edge_established=false`.

Ограниченный A04 source increment: `SandboxRunner.run` фиксирует строку профиля в начале вызова,
сверяет её с digest при создании runner и передаёт те же байты в `sandbox-exec` и
`SandboxResult`. Тесты подтверждают, что drift до запуска останавливается до `Popen`, а изменение
профиля во время проверки executable не меняет запущенные байты или digest результата. Sol принял
точный diff `6f944e39fa3f156b23fc091654b4475730cca2486210b8accc4b4b0908bfedde`; engineering release
gate прошёл Ruff/format, 598 тестов и 189 subtests. Это только custody байтов внутри Python-объекта:
у private digest нет внешнего immutable anchor. Квалификация sandbox, запуск того же inode, loader
closure, descendants и stdout provenance остаются открыты. Планы сохраняют
`execution_qualified=False`; A04 и эксплуатационный выпуск не приняты.

Ограниченный A11 source increment: общий `GitGate.load_receipt` используется перед публикацией и
восстановлением supervisor. Он ограничивает объём чтения, отвергает symlink/non-regular receipt,
проверяет canonical content-address, точную схему, candidate/policy identity, commands, environment
и строковый результат со `status="passed"`. Открытие требует `O_NOFOLLOW|O_NONBLOCK`: FIFO по
digest-пути отвергается без зависания; при отсутствии этих флагов loader закрывается. Синтетическая
recovery-регрессия оставляет задачу `accepted` и ref без изменений для receipt с новым корректным
digest, но изменёнными командами; после возврата исходного receipt интеграция происходит один раз.
Повторная проверка уже существующего digest при создании теперь тоже использует общий loader:
попытка эксклюзивной записи при занятом пути переходит к bounded no-follow валидации. Child-process
регрессии с timeout проверяют FIFO и symlink именно по вычисленному valid digest на пути `verify`,
а также FIFO при recovery reader. Общий gate прошёл Ruff/format, 607 тестов и 189 subtests. Это
локальная проверка custody/replay, не подпись и не внешний trust anchor: обладатель совместного
контроля receipt, intent и Store может подделать запись. Sol принял точный code/test diff
`ddc68f2b69812b2b3c1fd78f735dd00a4999dfeaf4c3c945d0480e640ec1d17e` на базе `71b3aeb`. При
одновременной выдаче того же receipt второй поток может fail closed, если увидит первую ещё не
дописанную запись; это предел надёжности, не обход проверки. Подлинность reviewer/provider, A03 и
operational acceptance открыты.

**2026-10-21. Независимый аудит завершён; основатель разрешил продолжить инженерную разработку.**
Пилот остаётся остановлен. Проверенный исходный код аудита: `org/development`, `b2dcd6d`;
документационный checkpoint: `0054a9e`. Ни эксплуатационный запуск, ни снятие STOP не разрешены.

Текущий source checkpoint `org/development`: merged review diff ограничен 50 КБ, inventory путей —
256 КБ; stderr — 1,500 байт, время capture — 30 секунд. При ошибке группа Git завершается и
процессы собираются до вызова reviewer. Допустимые байты декодируются строго, без нормализации
CRLF и пробелов. Sol принял точный diff
`1fcbda8eba27793c002289b21a1799fee812b8c5891d0b254ad3c99761ebed33`; полный engineering gate:
Ruff, format, 585 тестов и 189 subtests. Артефакты каждой verifier-попытки разделены по invocation
и привязаны к receipt. По единственной контролируемой передаче native Goal
`01a15f55-903b-7de2-adec-19733734ac78` подтверждена как `active`. Прежняя Goal
`01a15b1c-c263-71b0-bfca-c5b46d17a89b`, её история и счётчики сохранены в private handoff. Новая
цель — интегрированный синтетический цикл организации и восстановление после прерывания. STOP,
запрет runtime и блокировка выпуска по A03 сохраняются.

Предыдущий исходный checkpoint A04 на базе `200870b` устраняет неограниченное ожидание verifier
после ошибок сбора вывода: группа процесса завершается, оба потока закрываются, исходная ошибка
сохраняется. Sol принял точный diff `652f72acd610caaa82e1b5ca2e3ccaec5f426acf16db1f868f40cd842480df25`;
полный gate прошёл: Ruff, format, 556 тестов и 127 subtests. При отказе системного завершения
процесс может остаться жив; provenance и полная квалификация A04 открыты. Это не меняет STOP,
запрет runtime и блокировку операционного выпуска по A03.

Следующий source increment A05 закрывает гонку второго parser fault при уже открытом circuit:
первая circuit/recovery пара сохраняется, а текущие attempt и task завершаются вместе с failure
event в одной транзакции. Тест проверяет две одновременно активные попытки, оба события, сохранённый
usage, версии первой пары и stale retry. Sol принял точный diff
`0000787d1d13c17471651fb73b120b99f82ff14046943ee8f2814d7a955ccfe0`; полный gate: Ruff, format,
557 тестов и 127 subtests. Это не добавляет отмену остальных attempts или recovery scheduler.

Следующий A04 фикс снимает повторное чтение изменяемого `argv`: sandbox runner один раз фиксирует
команду в tuple и использует её для pin-проверок, запуска и результата. Регрессия меняет исходный
список во время executable hash и проверяет, что запуск получает исходную проверенную команду.
Sol принял exact diff `fe2e69d3b35ebb12bf6593646cc14b11dacac06d6a3a6e1989613c4832c086f1`; полный gate:
Ruff, format, 558 тестов и 127 subtests. Замены файлов, provenance вывода и полная квалификация A04
остаются открыты.

Ещё один A04 increment фиксирует `env_overrides` в отдельном dict до allowlist-проверки и передаёт
дальше только эту копию. Регрессия после проверки добавляет запрещённый DYLD key и NUL в LANG;
дочернее окружение сохраняет только исходные допустимые значения. Sol принял exact diff
`826cee994266453afa0403cb8b9e64f6de6ccb617784e300165f0fe8ca9aadae`; полный gate: Ruff, format,
559 тестов и 127 subtests. Loader qualification и provenance окружения остаются открыты.

Последний A04 increment требует полного чтения закреплённого executable/script до EOF и включает
режим файла в сравнение metadata дескриптора. Регрессии проверяют ранний EOF с правильным хэшем
префикса, закрытие fd и изменение mode для обоих readers. Sol принял exact diff
`a8af8365a603d0d4a99c8b29a9daf3d7210be1a89b3b89ad5249192f8a70af51`; полный gate: Ruff, format,
561 тест и 131 subtest. Последующее выполнение по пути и continuous process identity остаются
отдельными незакрытыми границами.

Исправлена валидация границ A04: timeout допускает только конечный exact `int`/`float` в `(0, 3600]`,
лимит вывода — только exact `int` в `[1, 10_000_000]`. NaN, infinities, bool, строки и выход за границы
отклоняются до hash/Popen; отдельно проверен custom metaclass, сравнивающийся как `int`. Sol принял
исправленный exact diff `0eeac99fce04a92cd61c60c6073a72ab52d7bef2cbdc341c73704bc7c24bd06c`; полный gate:
Ruff, format, 563 теста и 158 subtests. Нативные Security-вызовы остаются непрерываемыми; execution
provenance не подтверждён.

В A04 parser закрыт exponent overflow: `1e9999` и `-1e9999` теперь отклоняются независимо от
вложенности в object/array, а конечное `1e300` сохраняет обычную поддержку. Проверен полный путь
отказа до reviewer/acceptance. Sol принял exact diff
`0d1c2d831c0071cdc82fe435a3eb7396c70eae365065d61ff63f9895776e168f`; полный gate: Ruff, format,
566 тестов и 161 subtest. Provenance stdout и execution qualification не заявляются.

## Решение директора

Сохранить SQLite, журнал, fencing, изоляцию исполнителей, независимую приёмку и единственного
publisher. Они образуют пригодный фундамент. Следующий этап должен собрать и доказать полный
рабочий цикл компании. Расширение штата и новые уровни управления сейчас преждевременны.

**Готовой автономной компании пока нет.** Есть инженерный прототип с серьёзными проверенными
компонентами. Первая реальная задача ещё не прошла весь путь до независимой приёмки и интеграции.
Недельную эксплуатацию сейчас разрешать нельзя; полная перепись Brain или контроллера не нужна.

## Что подтверждено

- На момент предыдущей проверки оба прежних Goal через `thread/goal/get` имели статус `paused`;
  последняя запись `active` в старом rollout была устаревшей. Позднее основатель явно подтвердил
  завершение audit hold и продолжение работы в рамках той же Goal
  `01a15b1c-c263-71b0-bfca-c5b46d17a89b`. Текущий native readback снова показывает `paused`;
  доступный Goal API не возобновляет эту Goal. Основатель разрешил продолжать source работу с тем же
  владельцем, не создавая замену и сохраняя историю, счётчики и durable state.
- В A10 исправлен split-read `config.json`: deployment validator читает одни и те же UTF-8-байты,
  разбирает и хэширует их; синтетический тест проверяет замену файла и digest между чтениями.
  Sol принял точный diff `9113f9f41660bd052b30257216c17d959184ba3906b9ca04f19aa2f311353390`;
  полный engineering release-check прошёл: Ruff, format, 527 тестов и 112 subtests. Последующие
  изменения файла, отдельная гонка `config.sha256` и общая генерация конфигурации в SQLite не
  закрыты. Выпуск/запуск не разрешены.
- Следующий A10 increment сравнивает review snapshots как canonical JSON: дрейф `1` → `true` в
  source/producer/participant данных блокирует dispatch и ACK, но сохраняет полученный response;
  порядок ключей и пробелы не меняют идентичность. Sol принял exact diff
  `ef3120dd01547830b844e7547365b2bce7d12d348581b0ecb257470b480c8701`; release-check: Ruff,
  format, 533 tests, 112 subtests. Некорректный JSON task field разбирается раньше в `_task()` и
  остаётся отдельным дефектом. A03 и operational acceptance остаются открыты.
- A04 bound JSON proof stdout теперь декодируется строго как UTF-8 после завершения process group;
  malformed bytes блокируют проверку до parsing результата. Совместимость legacy/`exit_zero` и stderr
  сохранена. Sol принял diff `1308d5b8917912f0e3c800c822aca3977a5ad90f46a895697c27160e26623677`;
  focused: 64 supervisor tests/10 subtests и 3 decoder tests/4 subtests, Ruff/format прошли.
  Авторство stdout, same-inode, loader/sandbox qualification не доказаны.
- Пилот сохранил STOP, конфигурацию, manifest, SQLite и все шесть неудачных попыток.
  Состояние: 3 задачи, 106 событий, **0 acceptances, 0 integrations**. Engineering LaunchAgent не загружен.
- В локальном наборе: **216 тестов и 42 subtests прошли, 2 старые проверки упали** на обязательном Permit.
  Первые два дополнительных сбоя пути временного каталога устранены настройкой тестового окружения,
  без изменения исходников. Реальные тесты Seatbelt вошли в этот запуск.
- Отдельная текущая native-проверка прошла **21/21 без модельных запросов**. Engineering Ruff и
  синтетический Brain smoke прошли. Общая Ruff-проверка репозитория сохраняет 107 замечаний;
  полностью зелёный проект не заявляется.
- Новый синтетический опыт с настоящими временными Git/SQLite подтвердил сохранение двух независимых
  правок и идемпотентное согласование после остановок между intent, публикацией и записью интеграции.
  Это не физический crash/reboot-тест и не приёмка работы реальных моделей.

## Основные проблемы

| Проблема | Практическое следствие |
|---|---|
| A01: ключ хэша CLI не совпадал | Кандидат исправляет имя ключа; требуется точная проверка выпуска и review Sol |
| A02: bootstrap не создавал usage ledger | Кандидат создаёт ledger после qualification и в одной транзакции проверяет схему/миграцию; restore/migration ещё не приняты |
| Нет квалифицированного завершения учёта реального модельного хода | После успешного хода остаются резерв и неопределённая атрибуция; следующая выдача работы блокируется |
| Task spec сохраняется, но verifier привязан к старому каталогу | Новая допустимая задача доходит до `submitted`, затем получает `KeyError` |
| Нет общего владельца жизненного цикла | Успешно завершившийся bounded supervisor не перезапускается этим plist; handoff Sol и quota recovery не имеют законченного исполняющего пути |
| Backup неполон | Не сохраняет STOP, новый ledger, Git, intents и попытки; перенос в другой путь не является восстановлением компании |
| A07: watchdog игнорировал критический quota stop | Кандидат отменяет работу по явному `stop`/critical/reserve reason; отсутствие новых admission slots само по себе активную работу не останавливает |
| A08: обычный `make check` не включает engineering | Добавлена отдельная цель `engineering-release-check`; локальные Ruff/pytest не установлены |
| A09: ошибка парсера тратила retry задач | Кандидат атомарно и под lease fence сохраняет circuit/obligation/failed attempt; recovery привязан к версиям решений |

Дополнительно: инфраструктурные ошибки расходуют попытки задач; контракты отделов пока привязаны
к одному примеру; каждый retry заново требует Sol lead; reviewer видит максимум 50 тысяч символов
patch без доступа к файлам; история свыше 1024 задач несовместима с текущим входом валидатора.
Это конкретные пробелы в соединении компонентов и длительной эксплуатации, а не доказательство
утечки данных или повреждения production.

## Почему пилот не принял ни одной задачи

Все шесть попыток остановились на JSON-разборе ответа Sol lead до запуска стадии Luna worker.
Между первой и последней ошибкой прошло около 41 секунды. Старый адаптер собирал все agentMessage,
включая commentary; source-исправления не были выпущены в сохранённый пилот. EXP-001 и CTX-001
исчерпали лимиты, PLAT-001 ждёт производителя. Безопасный STOP сохранил этот результат.

Большое число коммитов отражает работу над инфраструктурой вне рабочего конвейера пилота.
Независимые проверки находили реальные ошибки, но приёмка отдельных файлов не закрывала сценарий
«развернуть → выполнить → проверить → интегрировать → продолжить → восстановиться».
Системный владелец этого сценария и его обязательная приёмка должны стать центром следующего этапа.

## Эффективность и масштаб Brain

Сообщённые основателем 14ч28 общего времени, 7ч59 зарегистрированной активности и 4ч37 простоя
после HTTP 403 соответствуют примерно 55,2% и 31,9% общего интервала. Остаток 1ч52 этими данными
не классифицирован. Аудит не доказывает причину 403 и не превращает активность в полезную продуктивность.
Сравнить экономичность Luna и Sol по стоимости принятого результата пока нельзя: таких результатов
у пилота нет, а полная атрибуция расхода не квалифицирована.

Нужны пять предметных направлений — Senses, Experience, Cortex, Evaluation, Policy/Paper — и
обеспечивающая их Platform. Это владельцы контрактов и очередей, а не пять постоянно работающих чатов.
Начальная эксплуатационная параллельность остаётся в пределах двух команд. Увеличивать её следует
по росту принятого полезного результата, качеству и доступному ресурсу.

Существующие snapshots, generation jobs и независимые evaluation protocols Brain нужно использовать.
Организации недостаёт привязки исследовательской задачи к версии данных, compute/storage lease,
артефактам, зарегистрированному сравнению и отрицательному результату. Коммит и успешное обучение
не равны научной приёмке или продвижению модели.

## Продолжение инженерной разработки — 2026-10-21

Основатель подтвердил, что временный hold от 20 октября исполнен и завершён, и явно разрешил
длительную автономную разработку исходников. Luna-6 High — единственный владелец реализации; Sol-6.1
High ведёт сложные технические решения и независимую проверку. Astra High остаётся эпизодическим
CEO и не вызывается автоматически. После одной разрешённой передачи активна Goal
`01a15f55-903b-7de2-adec-19733734ac78`. Прежнюю Goal `01a15b1c-c263-71b0-bfca-c5b46d17a89b`, её
идентификатор, историю, счётчики и durable state сохранить без изменений; не создавать ещё одну
Goal или конкурирующего владельца.

Разработка и интеграция организации идут в `org/development`; Brain развивается в
`brain/development`; `brain/linux` остаётся отдельным непринятым кандидатом; `research/active` —
замороженный совместимый алиас; `main` не изменяется. A03 остаётся блокером эксплуатационного
выпуска, но не останавливает несвязанную работу над исходниками.

Эта директива не разрешает эксплуатационный запуск: pilot STOP остаётся активным. Не запускать
непроверенный supervisor/runtime, сбор рынка, обучение, AUTO или торговлю; не читать или менять
protected datasets, production либо `main`; не использовать платные ресурсы. Следующий ниже план
остаётся историческим планом будущей эксплуатационной квалификации, а не разрешением включить пилот.

Последний исходный checkpoint A10 — `6919e8e`: конфигурация contract proposals теперь проверяется по
plain types целиком и фиксируется как canonical JSON snapshot до SQLite reads. Sol принял точный diff;
engineering gate прошёл: Ruff, 526 tests и 112 subtests. Snapshot защищает от смешивания настроек при
последующих DB reads, но не от мутации во время самой сериализации и не создаёт общей generation
authority между файлом конфигурации и БД.

A10 follow-up в `org/development` после `e5773be` превращает ошибки JSON/UTF-8 при чтении task
snapshots producer и participant в stale blockers. Повреждённый ответ закрывается статусом blocked,
сохраняя исходный response и без ACK; global replay продолжает обрабатывать независимые запросы.
Sol принял точный source/test diff
`2e26d89872276415c60bcf0d2814cbfc2759740be6d16cb6832aa611d0118c4f`. Transition suite: 103 tests;
engineering gate: Ruff, format, 542 tests и 116 subtests. Отсутствующая задача и SQLite-ошибки
сохраняют прежнюю обработку; общий A10, A03 и эксплуатационная приёмка остаются открыты.

## Исторический план первого эксплуатационного выпуска

1. Luna — единственный владелец реализации; Sol — сложная архитектура и независимая проверка.
   Сначала исправить запуск, учёт и квотный контроль на одной версии конечного пилота, включить
   engineering в локальную проверку выпуска и обеспечить восстановимый checkpoint. Новые динамические
   задачи и непрерывный сервис пока явно отключены. Общая система пяти отделов не должна задерживать
   первый полезный принятый результат.
2. Выпустить проверенный контроллер с сохранением STOP и истории. Только после отдельного разрешения
   восстановить исходные задачи в той же БД и принять EXP-001, CTX-001, PLAT-001.
3. Доказать новую полезную задачу вне первоначального каталога, согласование контракта отделов
   и независимый прогресс другой команды. Критерии приёмки фиксируются до реализации.
4. Проверить потерю связи/доступа, гибель worker/controller, дубликаты/поздние ответы, границы
   публикации, полный restore, upgrade/rollback и отдельно logout/reboot.
5. Подтвердить 24ч, затем 72ч, затем 7 суток на реальном полезном backlog без ручного подталкивания
   при покрытых восстанавливаемых сбоях. Нужны принятый результат после восстановления,
   классифицированный простой, ограниченные неопределённый расход и рост диска, ноль непроверенных
   интеграций и неразрешённых действий. Пустая очередь и безопасное ожидание не доказывают продуктивную автономию.

Astra остаётся эпизодическим директором. Нужен обычный немодельный механизм пробуждения и восстановления
с устойчивыми причинами остановки; ожидание не должно расходовать модельные ходы. Никакого нового
допуска к рынку, тренировкам, AUTO, production или торговле этот план не даёт.

## Git и сохранённые материалы

Консолидация Sol качественна как сохранение истории и создание основы разработки. Подтверждены все
69 исходных пинов, точное выбранное содержимое metering/validator/Store, четыре грязных файла в архиве,
контрольные суммы 11 bundles и трёх private dirty archives, `git fsck`, текущие remote-пины.
Код Brain в `brain/development` и `org/development` относительно `main` не изменён.
Потери среди проверенного инвентаря не обнаружены. Это не означает готовность объединённой системы.

`main` остаётся `2f8da5f`; Brain — `brain/development` (`dabf14a`); Linux — отдельный
непринятый `brain/linux` (`897154f`); `research/active` — замороженный алиас. Архив
`archive/consolidation-20261020` не является исходным деревом для merge. Незавершённые backup/restore,
UI и DR сохранены, но не приняты. Девять ignored-artifact restore trials заново не повторялись.

Технический вывод и условия приёмки: [engineering-organization.md](../docs/engineering-organization.md).
Квитанция: [brain-org-independent-audit-2026-10-20.json](../docs/evidence/brain-org-independent-audit-2026-10-20.json).
Скрипты и сырые результаты: `/Users/root1/Developer/brain-org-state/audit-20261020-independent/`.
## Решение о возобновлении — 2026-10-20

Sol самостоятельно изучил аудит и сверил текущее состояние кода, приватных доказательств и
контрольных сумм STOP/пилота. Native thread `01a15a5e-7fc3-7bf0-9a10-73199636bfcd` подтверждён
как `gpt-6.1-sol/high`. Он не менял код или пилот.

Текущий приоритет — не повторить широкую серию исправлений до проверки причины блокировки учёта.
Sol требует сначала подтвердить, откуда реально получать terminal usage, связанный с dispatch,
thread/turn, моделью и epoch. Завершение модельного хода само по себе не доказывает нулевой расход;
неизвестный usage остаётся зарезервированным. Luna затем собирает один конечный STOP-preserving
кандидат с исправлениями запуска, ledger, учёта, активной квоты, circuit breaker и локального
release gate. Sol независимо проверяет точный кандидат.

До любой модификации исходного пилота обязательны полный checkpoint и восстановление в новом
пути. Исходные шесть неудачных попыток, STOP, SQLite и архивы сохраняются. Возобновить исходные
задачи можно только после независимой приёмки выпуска и отдельного разрешения основателя. Затем
следуют универсальная привязка задачи к verifier, восстановление после отказов, координация отделов
и испытания длительностью 24 часа, 72 часа и семь суток. Условия подробно записаны в
`docs/engineering-organization.md`.

В интегрированный synthetic path добавлен off-catalog PLAT-003 на текущем tip PLAT-002. Store теперь
сверяет digest task spec с точными canonical bytes опубликованного контракта; для v2 перепроверяет
transition hash, activation, integrated producer и provenance ACK. Повреждённое содержимое, замена
`1` на `true` в activation и исчезновение строки spec блокируют readiness/claim и verifier fallback.
Изменённый каталог не заменяет сохранённый verifier binding. Настоящий `run_attempt` PLAT-003
заканчивается до checkout, worker dispatch, transport и spawn, потому что verifier manifest/executable
остаётся неквалифицированным. Sol принял точный diff
`04f44369c5a2434a6136b835ad2a8e90a897b6a20417f1cee3bd892778c230f9`; release gate: Ruff/format,
617 тестов, 189 subtests; `make smoke` воспроизводим с `edge_established=false`. Это закрывает
bounded A04 admission-to-gate vertical, но не off-catalog execution, полную A04 qualification,
provider/runtime или operational release. A03 и Pilot STOP остаются.

### Исторический снимок владельца на этапе аудита

Native-состояние тогда показало: текущий аудитный поток — Astra без Goal; прежний Luna-root
`01a15657-3558-79c1-86dc-6ed033c264df` — `gpt-6-luna/high`, его Goal приостановлен, активных
попыток в пилотной БД нет. На тот момент передачу планировали продолжить через этот сохранённый Luna
поток с его историей и счётчиками; позднее единственным владельцем стала существующая successor Goal
`01a15b1c-c263-71b0-bfca-c5b46d17a89b`. Организационный runtime и LaunchAgent не запущены.
Эксплуатационные ограничения основателя остаются в силе; рынок, обучение, AUTO, торговля,
защищённые данные, production, `main` и платные ресурсы не затрагиваются.

Goal `01a15a6e-0e03-77d2-b3ce-d81b95b5a89f` сообщил `blocked` после повторных исследований A03;
Goal и история сохранены. Поскольку нативный API не возобновляет заблокированный Goal, основатель
разрешил ровно одного successor — `01a15b1c-c263-71b0-bfca-c5b46d17a89b` (`gpt-6-luna/high`).
Он продолжал ограниченную разработку A05/A10/A04. Связь записана во внешнем handoff. Свежая
read-only проверка пилота: 6 неудачных попыток, 0 активных leases, 0 acceptances и 0 integrations;
STOP сохранён, LaunchAgent не загружен. На тот момент successor Goal продолжал независимую разработку
и проверку A05/A10/A04. Последующее founder-подтверждение от 2026-10-21 завершило исторический hold
и разрешило дальнейшую исходную работу; текущие полномочия и ограничения записаны выше. A03 остаётся
fail-closed и блокирует только полный release; неизвестный usage и его резерв сохраняются. Повторный
запуск пилота остаётся отдельным разрешением.

Первичная read-only квалификация источника usage завершена на закреплённом Codex CLI `0.163.0`:
уведомление содержит счётчики `threadId`/`turnId`, но не признак terminal/finality; RPC адаптер
считает его progress. `account/usage/read` возвращает estimated агрегат без привязки к dispatch/turn.
Sol принял receipt только как static correlation и отклонил terminal settlement: финальность,
порядок, полнота и привязка к модели, dispatch и epoch не доказаны. Release остаётся blocked,
неизвестный usage зарезервирован. Дополнительная read-only проверка закреплённого CLI нашла
`rawResponse/completed` с `responseId`/`threadId`/`turnId` и точным upstream usage либо `null`, но
первая схема была сгенерирована без `--experimental` и скрыла opt-in фильтром, а не потому, что
его нет в pinned бинарнике. Исправленный экспорт схемы v0.163.0 с `--experimental` показывает
`experimentalRawEvents` и помечает его internal-only; `RawResponseCompletedNotification` тоже
помечен internal-only. Source review подтверждает событие после upstream completion,
thread/turn binding и публикацию до turn completion. Но `response.incomplete` может содержать spending, который core записывает отдельно
без `RawResponseCompleted`; pinned tests проверяют отсутствие raw event при ненулевом учтённом usage,
затем `TurnComplete`. Поэтому сумма raw events неполна даже при идеальной доставке. Runtime
delivery/reconnect/duplicates/late corrections не проверялись. Payload не связывает запись с
dispatch/model/epoch. Это не разрешённый owner-adapter источник. Подробный probe receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-raw-response-source-probe-20261020.json`.
Сверка official source подтвердила: в pinned v0.163.0 поле есть в experimental schema, но оно
помечено internal-only; `rawResponse/completed` также internal-only. Первая отрицательная находка
была следствием schema filtering при отсутствии `--experimental`. Корректирующий receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-schema-filter-correction-20261020.json`
(SHA-256 `89ef2e6a6017b5dd9a6bff446742a3263e43238e4e298c3c2084e927903e1640`). Подробный
source-semantics receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-raw-response-source-semantics-20261020.json`
(SHA-256 `86ce357cbd63162a63e5aa4792875b4ef26a1b0e73325c842812029f9f7b77b2`).
Supplemental incomplete-usage receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-raw-response-incomplete-gap-20261020.json`
(SHA-256 `44f2730660e1591ae6fe24de5390aaaf28c9d72290cf2a19279a019be9083e6d`).
Стабильный `codex exec --json` экспортирует последний progress snapshot
`thread/tokenUsage/updated` как cumulative `usage` только при успешном `turn/completed`; при
отсутствующем snapshot usage становится нулевым, а failed/interrupted не дают terminal usage.
Это не независимая terminal квитанция. Pin и adapter не менялись, internal opt-in не включался;
A03 остаётся fail-closed.
Публичный cumulative snapshot тоже не закрывает полноту: completed response без `usage` может
оставить core без `TokenUsageInfo`, после чего app-server не публикует `thread/tokenUsage/updated`;
более поздний положительный snapshot скрывает этот неизвестный расход относительно нулевой базы.
Sol отверг settlement по такому потоку. Узкий guard с заранее доказанным provider state мог бы
аннулировать любой in-turn snapshot без прироста, но неизвестно, как публично подтвердить полноту и
происхождение исходной базы; событие без прироста не отличает null usage от отсутствия новых трат
или служебной публикации. Нет квалифицированной привязки к модели/epoch и поддерживаемого пути
инициализации такой базы. Это только условный guard, не settlement contract. Receipts:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-public-snapshot-null-gap-20261020.json`
(SHA-256 `810efd6528d1cdcdcb0d0283ac2a0877ae93bb5d049f56a136cddf88b42fff38`) и
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-public-snapshot-guard-review-20261020.json`
(SHA-256 `3e082f264a19e92f11b3023556150613059e03ee186573080a9b1573bfa89f8e`). A03 остаётся
fail-closed; неизвестный и поздний usage продолжает удерживать reservation.
Core также сохраняет внутренние `TokenUsageRecord` с response ID и usage, включая известные
incomplete расходы, когда есть и ID, и usage. Публичная проекция `thread/read` явно пропускает эти
записи; list/read items возвращают только проекцию items, resume переигрывает агрегированный
`TokenCount`. При null usage запись не создаётся, а сами записи не имеют dispatch/model/epoch и
признака полноты. Sol независимо отверг этот путь как публичный или достаточный settlement source.
Частные rollout-данные не читались. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-token-record-public-api-review-20261020.json`
(SHA-256 `a72445772e0a4aabff3b59621deb881b30ebc8d0161164b22c1d7ba08a6a26e3`).
Найден отдельный условный parser gap: optional
`cacheWriteInputTokens` сохраняется в raw receipt, но мешает canonical progress snapshot; реальная
частота поля неизвестна. В конечном кандидате добавим синтетические варианты на 5 и 6 полей и
уточним семантику cache-write, не используя его для settlement. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-source-qualification-20261020.json`; review: Sol thread `01a15a5e-7fc3-7bf0-9a10-73199636bfcd`.

Условную потерю canonical progress snapshot исправил: пятифилдовый формат и optional шестое
`cacheWriteInputTokens` сохраняют те же пять канонических счётчиков; raw шестое поле остаётся в
receipt, некорректные значения отвергаются, terminal settlement не включался. Добавленные focused
на момент первичной правки прошли прямой запуск и компиляцию; позже точный кандидат `3c16388`
прошёл pinned pytest/Ruff release gate, что зафиксировано ниже.

Sol отклонил release qualification source candidate `8a28908`, затем проверил `f73e030`:
подтвердил A01, семантику A07, порядок bootstrap и атомарность миграции A02, но отклонил
совместимость схемы A02, проверку типов выходов A09 и полную release qualification. Кандидат
`47d0770` также отклонён: review обнаружил, что валидатор ledger терял группировку composite FK,
а snapshot принимал повреждённый Git object graph и неполную SQLite schema; restore зависел от
старого executable path, мог удалить не принадлежащую ему destination, обходил containment через
symlink parent и публиковал копию до установки requalification barrier. Кандидат `d4d6590`
исправил эти случаи: exact grouped FK checks; organization column/type/null/default/
PK/FK/unique signatures and data integrity; Git fsck with sanitized config and rejection of
promisor remotes; source-path rebinding for bundled tools; missing host Python/Codex accepted only
behind requalification; canonical containment; safe cleanup; and barrier before publication. Также
квота читается на первом цикле независимо от system uptime. Snapshot требует STOP/lock и сохраняет
`runtime_ready=false`; host requalification остаётся обязательной.

Sol проверил точный кандидат `3c16388` и принял его для bounded source repair, включая guard,
требующий `restore_requires_host_requalification=true` при relaxed structural validation.
Полный source gate `make VENV=/Users/root1/Developer/memecoin-research/.venv/bin
engineering-release-check` прошёл на Python 3.13.6 с точными версиями из
`requirements-dev.lock`: Ruff lint, Ruff format (44 файла), pytest — 251 тест и 42 subtests.
Внешняя pinned-среда не изменялась. Sol принял точный `3c16388` как bounded source repair;
отдельная новая проверка выполнена `gpt-6.1-sol/high`, без приписывания ей native thread ID.
Это не полная release qualification: `ORG-A03` fail-closed, unknown/late usage сохраняет
резервы, а operational restore и host requalification остаются отдельными проверками. Полный
синтетический checkpoint/fresh-path restore по-прежнему не является восстановлением owner runtime
или pilot. Пилот, STOP, runtime и protected data не трогались.

Дополнительно напрямую прошёл полный синтетический сценарий `test_snapshot_uses_sqlite_backup_and_copies_policy_and_receipts`: checkpoint → перемещение источника → restore в новый путь; проверены SQLite backup, inventory/файлы, integration Git ref, сохранение STOP, persisted host-requalification barrier и `runtime_ready=false`. Тест использовал временное состояние и синтетический Git repo; owner runtime и pilot не копировались и не восстанавливались.

Текущий Mac отдельно прошёл no-inference host checks: native permission profile — 21/21, OS confinement — 27/27, Python 3.13.6 и pinned Codex CLI 0.163.0. Native receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/native-profile-qualification-20261020.json` (SHA-256 `dfbdddeb79533198f27c63c35e85026d641b2d3c45cc281dea73a4b33d821889`). Confinement receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/confinement-qualification-20261020.json` (SHA-256 `d17acddff1ea8f8521b80bee4b9508906d9dce1adcdfcdb64908e56691bf546f`).

Adapter hash совпадает с exact `3c16388`; Codex executable hash — `8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31`. Эти receipts квалифицируют текущие host controls/pin, но не requalification конкретного restored deployment и не снимают persisted barrier; он остаётся `runtime_ready=false`. Model/account/thread/turn requests не выполнялись.

### Возобновление разработки и внешний блокер ORG-A03 — 2026-10-20

Founder явно возобновил работу того же Luna Goal после трёх повторных исследований A03. A03
больше не останавливает независимую разработку: Sol-6.1 High подтвердил, что закреплённый Codex
интерфейс не даёт полной terminal settlement квитанции для всех расходов и исходов. Неизвестный,
поздний и неоднозначно привязанный расход остаётся зарезервированным; неизвестный расход нельзя
считать нулём. Полный эксплуатационный выпуск остаётся закрыт.

Запрос к upstream: поддерживаемый версионированный settlement интерфейс с полным набором terminal,
incomplete, failed и cancelled исходов, уникальными response IDs, привязкой dispatch/thread/turn/model/
attempt epoch, явной полнотой/потерями/поздними коррекциями и проверяемым полным denominator. Абсолютный
лимит расходов требует enforcement со стороны провайдера. Сейчас live-dispatch остаётся запрещён.
В будущем отдельно разрешённом bounded режиме: reservation до отправки, максимум один активный
dispatch и один слот неразрешённого расхода, reservation сохраняется при завершении/рестарте, а
неизвестный исход закрывает дальнейшие dispatch. Это ограничивает число расходов, но не цену одного
запроса. Не использовать internal или paid fallback и не включать auto-resume.

Luna параллельно продолжает A04 trusted execution binding, затем A11 verification/recovery packet,
A05 пассивный lifecycle, A10 durable contract/dependency flow и A12 bounded history projection.
Синтетическая source-проверка не разрешает pilot, supervisor runtime, market collection, обучение,
AUTO или торговлю. Для supervisor, pilot и полного эксплуатационного приёма обязательны отдельные
независимые release gates. Последний native readback показывает Goal
`01a15b1c-c263-71b0-bfca-c5b46d17a89b` как `paused`; доступный Goal API её не возобновляет. Явное
разрешение основателя на source работу сохраняется. ID, счётчики и история Goal защищены; замена не
создавалась.

Source-продолжение уже дало отдельный прогресс: A04 persistence/resolver scaffold принят Sol в
ограниченной области; supervisor/recovery wiring и A04 operational acceptance остаются открыты.
В bounded A11 source repair убрано молчаливое усечение diff: пакет фиксирует brief,
submitted/base/integration-tip/merged SHA, submitted и точные merged paths, acceptance, verifier
policy, результаты проверок и полный diff. Binary summary, неверная кодировка Git path, diff свыше
50 KB и final packet свыше 256 KB блокируют вызов reviewer. Sol принял точные bytes; полный pinned
gate прошёл: 265 тестов, 42 subtests и Ruff (47 files). Git CAS по-прежнему блокирует устаревший
integration tip и требует нового candidate/review. Это bounded repair, не broad A11; reviewer file
inspection, routing, A04 wiring и полное operational acceptance остаются открыты. Supervisor и pilot
не включались.

A05 получил ограниченный source scaffold в `91c6cfaf85aaebf62f6bd36290d7fcaf27a1d2b6`: чистая
детерминированная функция проверяет STOP, runtime/accounting/quota gates, process/lease observations,
recovery и backoff; start представлен только inert request с owner и epoch. Sol принял точные
исходники. Полный `engineering-release-check` прошёл: 274 теста, 42 subtests, Ruff lint/format
(49 файлов). На этом scaffold checkpoint durable fencing и persistence оставались открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-passive-lifecycle-scaffold-review-20261020.json`
(SHA-256 `87596c56a7e60b86a9ab85a70a2e03b15ab9ac613f2e9b8539a7fa2eafc88d97`). Продолжаются A04
supervisor/recovery wiring, A10 dependencies and department contracts, A12 history projection и
ограниченные source checks. Operational STOP и все пользовательские запреты сохранены.

Отдельный A05 increment принят Sol только в bounded source scope и включён в `org/development`:
`55fa10b` (проверенный source tip `10e127a`). Store теперь durable сохраняет эксклюзивный owner,
монотонный epoch/lease и inert lifecycle request; generic decision writer закрыт для reserved
namespace, повтор запроса сверяется с исходной версией и canonical JSON. Pinned gate прошёл
298 тестов, 45 subtests и Ruff (51 файл). Consumer, cross-epoch deduplication, retry policy,
scheduler/process/service wiring, operational recovery и полный A05 остаются открыты. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-fencing-review-20261021.json`
(SHA-256 `2052d58cf12bbc3a37d56b7804e8e42e26e6cd7b7fb34b685cf02e318d1f2af4`).

В A05 добавлен bounded source increment
`db04f23468744f736622ad5ea33f13d0166c4446`: Store теперь сам пересчитывает launch plan внутри
транзакции текущего owner fence и сохраняет точные detached observations рядом с plan.
Старый API с одним plan больше не принимает `launch_request`; для hold/wait/observe остаётся
совместимым. Sol принял точный source diff; gate прошёл 337 тестов, 73 subtests, Ruff на 56 файлах.
Это связывает заявленные входы с детерминированным решением, но не доказывает истинность, свежесть,
полноту или trusted origin наблюдений и не подтверждает текущую launch authority. Consumer,
повторная проверка gate при потреблении, cross-epoch deduplication, recovery и полный A05 открыты.
Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a05-observation-binding-review-20261021.json`.

Для будущего A05 consumer сначала нужны единый согласованный снимок gate с идентификатором источника
и временем/sequence capture, отказ при unknown/stale и повторная проверка database gates внутри
текущего owner fence по часам Store после возможных ожиданий блокировки. Deduplication должна
опираться на стабильный controller-owned authorization generation, отличный от заменяемого owner
epoch, и разрешать не более одного terminal passive disposition на generation. Consumer может
сохранять только disposition; process/service action требует отдельной квалификации. Нынешняя
привязка наблюдений этого не обеспечивает; consumer не реализован, полный A05 открыт.

A04: на macOS 26.7.1 не найден поддержанный public descriptor-to-exec путь для проверенного
интерпретатора; вывод ограничен этим хостом. Apple `SpawnConstraint` относится к запуску launchd,
а остальные описанные launch constraints являются требованиями в code signature target. Они не
доказывают, что `sandbox-exec` запустил тот же inode, который ранее был проверен. Не использовать
их как замену отсутствующей связи path-to-invocation; A04 остаётся fail-closed. Проба:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-darwin-fd-exec-probe-20261021.json`.
Повторный read-only осмотр установленного macOS 27.0 SDK на macOS 26.7.1 подтвердил: публичный
`unistd.h` объявляет только pathname `execv`/`execve`, а SDK text stubs не экспортируют
`fexecve`/`execveat`. Это сужает доступный поддерживаемый интерфейс данного host, но не исключает
все альтернативы и не квалифицирует запуск. Hash-bound receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-macos-sdk-descriptor-exec-surface-20261021.json`
(SHA-256 `b80848f7fdea1898e5e00d2496c73f80f7b121d1c4c0b7a3bdb5c8d250345a8b`).
Отдельный source-only обзор tracked `pilot_acceptance.py` видит импорты stdlib, dynamic import и
subprocess для candidate кода внутри sandbox. Hash harness и флаги `-I -S -B` не закрепляют
содержимое stdlib/native extensions и зависимости macOS loader. Deployed manifest и runtime closure
не исследовались; исполнения не было, A04 остаётся неквалифицированным.

A04 получил следующий bounded source increment в отдельной ветке: manifest v4 привязывает
canonical runtime path и code-directory hash подписанного executable. Перед принятием результата
первой bound verifier-команды `SandboxRunner` один раз наблюдает PID через публичный Security API;
ожидаются совпадающие hash и main executable path, иначе process group завершается. Disposable
macOS-тест подтвердил сохранение identity исходного подписанного образа при замене pathname и
отказы при несовпадении hash/path. Engineering gate прошёл: 348 тестов, 83 subtests; Ruff check и
format прошли. Sol принял exact candidate в ограниченном source-only review. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-identity-review-20261021.json`
(SHA-256 `dfbed547202fe6ec40cbc09c8612681264319d637e273944e2e6a91cfe783ec5`).

Это только одно post-launch наблюдение: оно не доказывает literal inode, не исключает `exec` в другую
программу до/после проверки и не связывает output со стартовым образом на всей длительности.
Проверяется только первая verifier-команда; Python imports, native extensions и macOS loader closure
не закреплены, production и sandbox не квалифицированы. Планы остаются `execution_qualified=False`,
полный A04 открыт. Supervisor/runtime не запускался.

Следующий source increment в `org/development` (`f4148cb7a57b2416fba3180f456d530d0c23216a`) переносит
manifest hash trusted harness в `BoundVerifier` и проверяет файл no-follow непосредственно перед
`Popen`: canonical path должен стоять ровно в `argv[4]` после `-I -S -B`, файл regular, чтение до
16 MB, metadata descriptor совпадают до/после hash. `prelaunch_script_sha256` записывается в command
evidence. Sol принял exact diff `3d69f91ff4b844d0c3fc030c36568c649a60048a5f6fd570380276a5338121d2`;
focused suite: 31 тест, 10 subtests; Ruff прошёл на 58 файлах. Ранние полные прогоны дали 400 тестов,
96 subtests и intermittent `PermissionError` в существующем cleanup `test_running_identity_survives_path_replacement`;
тот же сбой был на чистом base `78ff669`. Изолированный и инструментированный диагностический прогоны
прошли; затем обычный полный прогон exact candidate прошёл: 401 тест, 96 subtests. Причина cleanup
сбоя остаётся неизвестной; patch cleanup не менял. Prelaunch hash не доказывает, что Python открыл те же
bytes: замена path или мутация после проверки остаются возможны; синхронное чтение не имеет жёсткого
deadline. Loader/import closure, descendants, stdout provenance и deployment qualification открыты;
планы остаются `execution_qualified=False`. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-harness-custody-review-20261021.json`
(SHA-256 `319bc7da1f94aa5366daa0ab690cf8cf6bdccfcfa033314546ba91d446182919`). Gate rerun receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-harness-custody-gate-rerun-20261021.json`
(SHA-256 `02197749030f0be284acd1fbc5e1bcdae0fe0598dbf78fb3b09ac6c3caa3a77e`). Полный A04 открыт.

A10 получил read-only readiness report в `57f496f5f74258d00a09dc37b5818e409495dffc`: единый SQLite
snapshot показывает non-integrated/missing dependencies и несовпадающие версии contracts. Blocked
задача не скрывает независимую ready работу; transactional `claim` остаётся авторитетным и повторно
проверяет готовность. Sol принял exact source; полный gate прошёл: 275 тестов, 42 subtests и Ruff
(49 файлов). Этот scaffold остаётся основой.

21 октября в `org/development` добавлен durable contract transition (`ef953d5d95e0fb9bef50477957fcbd8f9fc90ed6`,
exact Sol-reviewed source `0879b5efed1cf0942d080e02624da13fd5a9aede`). Версии после начальной v1
публикуются через content-hashed transition, подтверждения producer и consumer хранятся как
неизменяемые записи, а активация требует integrated producer и все точные acknowledgements в одной
SQLite transaction. Supervisor сверяет transition с контрактом из конфигурации. Sol принял этот
ограниченный source; после merge прошли 312 тестов, 45 subtests и Ruff (52 файла). Поддерживается
только текущий специальный переход schema-validation v2; legacy v2 читается без ретроактивных
подтверждений. Generic contract migration, department plans/leases и широкое закрытие A10 остаются
открыты. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-durable-contract-transition-review-20261021.json`
(SHA-256 `d94296b79abae78b7d22cce4348b750a2123046dbb860250ff13407490e9424e`).

Новый A10 source increment `5e4d123ece95d6c87c0eb0ced6891d8473bedece` атомарно сверяет
`blocked-dependencies` obligations с текущим readiness report. Снимок сохраняет точные dependency и
contract blockers и независимые ready tasks; совпадающее активное obligation переиспользуется,
устаревшее закрывается ссылкой на immutable readiness event в той же SQLite transaction. Повторный
триггер не скрывает готовую работу и не меняет её состояние. Sol принял точный source diff; Ruff
прошёл, engineering gate: 341 тест, 73 subtests. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-dependency-obligations-review-20261021.json`
(SHA-256 `30a9fac250685cb73ad28db0575b6d75322c196c9f44d43bb122682b3c4c47f4`).

Следующий A10 fix убирает зависимость reconciliation от названия CEO trigger: каждый допущенный
episode после STOP/busy проверяет текущую readiness и обновляет duty в существующей fenced SQLite
transaction. Благодаря этому обычный `owner-review` создаёт duty, а следующий эпизод после
integration producer отменяет устаревшее duty с content-addressed evidence даже после открытия
нового Store. Sol принял exact diff
`64ccd7a1b3177b904b767dcfb9c4216ecac7e7da6af68f8072dc0821633f742b`. Engineering gate: Ruff,
format, 543 tests и 121 subtests. Reconciliation вызывается только при CEO episode, поэтому
непрерывная свежесть между эпизодами не заявляется; службы, dispatch и runtime не добавлены.
Generic contract migration, department plans/leases и полный A10 остаются открыты.

Следующий bounded source fix резервирует `blocked-dependencies/` за reconciler: общий API больше
не может создавать там обязанности, а неоднозначные старые записи останавливают сверку до любых
записей, сохраняя duty и journal. Канонические старые duties остаются совместимы; это проверка
формата, а не аттестация автора исторической записи. Sol принял точный diff
`f0c585cad833815e1399b32a61a96d2ea8fad5c1842d4369249be03207d2ee07`; полный gate прошёл 549 тестов
и 121 subtest с Ruff/format после изолированного повтора нестабильного macOS code-identity теста.
Не добавлены runtime или dispatch полномочия; неоднозначные исторические записи требуют отдельного
разбора.

Следующий A10 increment включён в `org/development` на `714bb573fc659ba00c849c9b7e0be7c1f470b472`:
идентичность quiescence handoff теперь учитывает coherent task/readiness snapshot, а Store сверяет
снимок и атомарно записывает или deduplicate-ит immutable handoff под `BEGIN IMMEDIATE`. Если
свежая readiness и capacity показывают доступную работу, quiescence откладывается. Sol принял
точный diff; Ruff прошёл, engineering gate: 344 теста и 73 subtests. Quota/resources остаются
отдельным наблюдением, task state может измениться после commit, hash-работа растёт с числом tasks
и blockers; second-connection lock-wait race не проверен. Generic contract migration, department
plans/leases, conflict adjudication и полный A10 остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-trigger-freshness-review-20261021.json`
(SHA-256 `efc80cca51087c30d66414069359e7516eef86d415dbbcb6e4ffc1e699114c87`).

Receipt scaffold:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-readiness-report-scaffold-review-20261020.json`
(SHA-256 `22037f53771b100bc1694f5f9133eec59ceeef519888389ff98f42d03af62176`).

A12 ограничил активную task projection на уровне SQLite (`e7e8f1d039cce909d91782c1cf51f4587ccbc016`):
выбирается максимум 100 active rows через индекс, полный state count сообщается отдельно, unknown
states сведены в `unclassified`, а report показывает omitted active rows. Интегрированная и failed
история не удаляется. Sol принял exact source; gate прошёл: 278 тестов, 42 subtests, Ruff (49
файлов). Подсчёт всё ещё сканирует task rows; dependency index, рост event/usage/receipt/checkout и
полный A12 остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a12-active-task-projection-scaffold-review-20261020.json`
(SHA-256 `3e921d550fc67c2d295f6024a4fe24ebfcbe0e1572bb73e260607cf70961ba77`).

A04 shared verifier plan is in `066227ef14fec28ed9d8879e0b61668cd3448f1b`. `verify_one` and `reconcile`
resolve one controller-owned policy; spec-backed work never falls back to the fixed catalogue, and
execution remains explicitly unqualified until manifest/interpreter/sandbox/result checks exist.
Reconciliation gates before acceptance and continues past blocked intents. Sol accepted the exact
bytes; full gate passed: 284 tests, 42 subtests, Ruff (50 files). `run_attempt`, executable binding,
production gates and full A04 remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-shared-verification-plan-review-20261020.json`
(SHA-256 `0d2383df79740ae3dff73acfa6967de4788a424db8a24ad9cebca011d25a8a7d`). Supervisor stayed off.

At `838ff0882758a311334c0c67057a5796b8a617bb`, `run_attempt` also resolves the plan before checkout
or model dispatch and stops unqualified work. Sol accepted this bounded source increment; the exact
gate passed 285 tests/42 subtests and Ruff (50 files). Attempt evidence still needs a verifier-side
plan hash comparison. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-run-attempt-verifier-gate-review-20261020.json`
(SHA-256 `7f8483c832e5bd14cdbf08da7b2e53f09edda9ba1e4549a326f2ca0f21df8ffb`). Supervisor stayed off.

At `b0e47312d496417c92efb5716babbd5713dce068`, exact verifier-plan identity travels in controller
candidate evidence and must match at verification and recovery before state transitions. Bound
missing identity and explicit `null` block; absent legacy evidence remains compatible. Sol accepted
the exact updated source; gate passed 286 tests/42 subtests and Ruff (50 files). Removing the key from
legacy evidence still uses compatibility behavior; full A04 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-attempt-plan-evidence-review-20261020.json`
(SHA-256 `7b773556fbe46f7aad7875c34ad2f53312c476660bf1203f097b3d1b31669ee8`). Supervisor stayed off.

`9487fc70057bc8ff5a467394fe7b70b06391e7b3` добавляет source-only resolver manifest для A04:
он проверяет canonical manifest hash, release/policy identity, digest immutable binding, exact
verifier metadata и содержимое trusted harness. Sol принял exact двухфайловый source scaffold;
engineering gate прошёл: 320 тестов, 55 subtests, Ruff (54 файла). Resolver ещё не подключён к
shared plan и не подтверждает interpreter, sandbox или result execution; связанные планы остаются
fail-closed, Supervisor не запускался. Полный A04 и operational acceptance открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-manifest-review-20261021.json`
(SHA-256 `723d22cc639e466e9beb1e5277b44200d3aef26fe5995a82faf32d97349e08a8`).

At `e96dbd2ee4af7b0f2496730c97a1815b1f69a411` manifest resolver включён в shared A04 plan.
Config pin `verifier_manifest_sha256` приводит к проверке `controller/verifier-manifest.json`,
immutable binding и trusted harness; hash входит в plan и attempt identity. Без pin plan остаётся
unqualified; invalid pin блокирует его. Даже с валидным manifest выполнение пока запрещено до
interpreter, sandbox и result qualification. Sol принял exact source; gate: 322 теста, 55 subtests,
Ruff (54 файла). Full A04 и operational acceptance открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-plan-manifest-review-20261021.json`
(SHA-256 `ae63abe99124eb4bfad6fe68b02b1b2177e1162037fdc2e347e9d747b9fc9e61`). Supervisor stayed off.


At `3c859e28eed86d9d7c6ecdcacdb9128c826fb387` A04 получил source-only result-contract wiring:
первый acceptance command обязан запускать pinned harness напрямую, результат проверяется по
уникальному JSON schema, acceptance ID и literal `true`, а environment override ограничен
allowlist. Sol принял exact scaffold; gate прошёл: 328 тестов, 62 subtests, Ruff check/format.
Планы всё ещё `execution_qualified=False`; interpreter, sandbox и executable integration не
квалифицированы, Supervisor не запускался. Full A04 и operational acceptance открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-result-contract-review-20261021.json`
(SHA-256 `ecfb8f8a9888a96b059e1fa2122eb6331aeb5de81353ad3f8ab43fc85a6e4278`).


At `d985cffdcbddee11d25f9972f6a1dafcf73f90fb` A04 verifier manifest поднят до v2: он pin-ит canonical path и SHA-256 interpreter binary, сверяет конфигурацию, запускает pinned interpreter в argv[0] и harness в argv[1]; path/hash входят в plan и attempt identity. Sol принял bounded source; gate прошёл 329 тестов и 65 subtests, Ruff check/format. Проверка наблюдает байты при построении plan, но не закрывает замену файла до последующего запуска и не квалифицирует loader/invocation semantics. Планы остаются `execution_qualified=False`; sandbox и executable integration открыты. Receipts: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-pin-review-20261021.json` и correction `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-pin-review-correction-20261021.json`.

Следующий bounded A04 source increment проверяет canonical interpreter path, regular-file/execute mode и SHA-256 непосредственно перед `sandbox-exec`: чтение идёт через no-follow descriptor и ограничено 512 MB накопленных байтов. Результат хранит это как `prelaunch_executable_sha256`. Это сужает окно замены после plan resolution, но `sandbox-exec` позднее разрешает pathname; проверка не доказывает запуск именно проверенного inode. Замена parent path или файла после проверки, loader/invocation semantics, sandbox, executable integration и production gates остаются открыты. План по-прежнему `execution_qualified=False`. Sol принял ограниченный diff; engineering gate: 332 теста и 65 subtests, Ruff прошёл на 56 файлах. Полный A04 и operational acceptance не пройдены.

Этот source increment вводит manifest v3 и фиксированный command prefix `interpreter -I -S -B harness`. Admission и resolver отклоняют иной порядок; manifest v2 не принимается этим контрактом. Эти CPython flags игнорируют `PYTHON*` и небезопасные пути, отключают автоматический `site`/`.pth` startup и запись `.pyc`. Synthetic harness прошёл с pinned interpreter в disposable Seatbelt profile; strict result contract, startup flags, prelaunch hash и отсутствие bytecode проверены. Sol принял ограниченный source increment; engineering gate прошёл: 336 тестов, 73 subtests, Ruff на 56 файлах. Это проверяет только synthetic host/profile/command tuple: exact deployment roots, полный loader graph и доказательство, что sandbox-exec исполнил именно hashed inode, остаются открытыми. План всё ещё `execution_qualified=False`; production admission и full A04 не приняты.

21 октября проверен отдельный disposable same-inode feasibility probe на macOS 26.7.1 / Darwin
25.6.0 (build 25G241): открытый `/bin/echo` descriptor нельзя исполнить через `posix_spawn` как
`/dev/fd/N` (`EACCES`) ни с `O_RDONLY`, ни с `O_EXEC`. В установленном SDK и текущем `libSystem` не
нашлись публичные `fexecve`/`execveat`; private API не используется. Это ограниченный вывод только
для проверенного host, не доказательство отсутствия всех альтернатив. A04 остаётся fail-closed,
планы `execution_qualified=False`, пока не будет независимо проверенного механизма или принятой
ограниченной residual policy. Loader/import dependency closure также не квалифицирован. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-darwin-fd-exec-probe-20261021.json`
(SHA-256 `1e22ca8a59053bf0b4b746a98b6d23b543391497ac7b92da2840defe92b0b07f`).

Новый source increment A04 принят Sol: `SandboxRunner` планирует повторную проверку hash и path image
identity каждые 100 мс, пока собирает вывод и ждёт первую команду каждого bound verifier; вызов
Security API синхронный, поэтому интервал фактических наблюдений может быть длиннее. Synthetic signed
binary regression подтверждает initial match, затем `exec` другого image и отказ до возврата
результата. Gate: 366 tests, 96 subtests, Ruff. Это выборочные наблюдения; короткие смены между
проверками, дочерние процессы и происхождение stdout не связаны с image; Security API не прерывается
command timeout. Same-inode, loader/import, sandbox, production qualification и полный A04 остаются открыты; планы остаются
`execution_qualified=False`. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-monitor-review-20261021.json`
(SHA-256 `5e65765997a18e9b75881156ef30c50aaf3b5ccbf78e9f9e8b1f101323aab342`).

A10 закрыл недостающий regression для lock-wait handoff: вторая SQLite connection держит
`BEGIN IMMEDIATE`, первая реально ждёт, вторая добавляет ready task до commit; старый snapshot
отклоняется, свежий принимается. Sol принял exact test-only diff; полный gate прошёл: 349 тестов,
83 subtests, Ruff (58 файлов). Timeout подтверждает практическую конкуренцию, но не все scheduler
interleavings; quota/resource остаются вне общей транзакции, а state может измениться после commit.
Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-quiescence-lockwait-review-20261021.json`
(SHA-256 `e8f16bbb23dcc955185586c72148de3a7bf18633500d8ee001547fb86ef5d254`).

Следующий A10 increment на `86a3b55` убирает полный пересчёт task/readiness каталога из write-lock handoff: SQLite triggers на task и contract таблицах обновляют revision, а Store проверяет выданный им snapshot digest и revision после получения `BEGIN IMMEDIATE`. Snapshot hashing выполняется до write transaction. Текущий Store также принимает предыдущую organization schema v2 и устанавливает миграцию при открытии базы. Sol принял точный diff `ab487e9f2898e1c36c41945f8009e9527c3751c2ff8ca84c69dcf624cd0e38c2`; gate: 369 tests, 96 subtests, Ruff. Quota/resource остаются вне общей транзакции, состояние может измениться после commit, а построение snapshot и fingerprint всё ещё растёт с retained tasks/blockers. Runtime и полный A10 не квалифицированы. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-quiescence-revision-review-20261021.json`
(SHA-256 `8e89894afd372733cf75baa07823994ebd1ff292ceb43269de539bebf794158f`).

A10 теперь сверяет все configured contract transitions независимо по имени, версии, producer
identity и consumer set; один ещё не готовый переход не мешает другим. Store сохраняет требования к
интегрированному producer, точным immutable acknowledgements и повторной проверке activation
history. Исходная config и hash `schema-validation v2` сохранены; для этой legacy схемы применяется
узкая связь `EXP-001`, а новые generic transitions должны явно назвать producer task. Sol принял exact
diff `259867174de4f18c7a0f36bbc66c7301e175cde6e388e599564d13286903f518`; gate: 351 тест, 83 subtests,
Ruff. Следующий bounded increment на `886a2e2` теперь маршрутизирует настроенные proposals только
lead-командам producer/consumer и принимает exact name/version/hash acknowledgements; stale source
version закрывается fail-closed, legacy schema-validation config/hash сохранён. Sol принял exact diff
`41b2b8f182a739bdbabb674dc2a7ba7428f59bb24431b551ed83ced8c4629dcc`; gate: 354 теста, 87 subtests,
Ruff. Generic proposal/ack routing покрыт; department plans/leases, conflict adjudication и полный
A10 остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-generic-contract-ack-routing-review-20261021.json`
(SHA-256 `472a67f60b6669393416a3adb02a1fad69da2d4bfe9fbd40d8c70e72ff735cb5`).

Следующий A10 increment принят Sol на точном diff
`ec616e1ece9e8f6eedc226d2d7a1cff8e8f057c338eb6555b142704dfb2d45e5`, source commit
`9b18609b6a658594fd2846bf9d2c78902170d4b4`. Добавлены epoch-fenced department leases максимум
для двух команд, immutable owner-attributed plan revisions, review deadlines и ограниченные
snapshot/report projections. `get_department_plan` читает обе выборки в одной SQLite snapshot;
cross-connection regression коммитит следующую revision между запросами и подтверждает coherent
status. Gate: 362 tests/96 subtests, Ruff. Это coordination bookkeeping: admission, execution,
scheduling, runtime и services не добавлены. Новый A10 conflict increment принят Sol на exact diff
`15d7a553edf4c0627181af0928ee991a26c24c186a0b5c55d57f0d82d1f73de3`, source `49c311c`. Эскалации
привязаны к текущим версиям планов обеих команд; CEO-fenced adjudications и решения append-only;
supersession требует изменения хотя бы одного плана. Snapshot v3 разделяет bounded open/stale
counts. Gate: 365 tests, 96 subtests, Ruff. Это conflict evidence, без readiness/admission/execution
authority; оставшаяся trigger freshness и полный A10 открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-conflict-adjudication-review-20261021.json`
(SHA-256 `cbb8a0f9eeeea6e0716b46332361cfcf93655bc066e9df03ac25592a84f75925`). Previous receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-department-plan-leases-review-20261021.json`
(SHA-256 `7ea47eacb174c8c2723c385b00d84977453554ac7f14fbbd71f19912b3fdb84b`).

A05 consumer пока не реализован: Store имеет owner epoch как lease fence, но не имеет отдельного
controller-owned authorization generation; `launch_authorized` приходит как caller observation.
Consumer не должен создавать или повышать launch authority. Нужен отдельный владелец generation;
пассивный disposition и любые process/service actions остаются разными границами.

A05 source `6691a2f` добавляет read-only `lifecycle_request_worklist`: bounded 1..100 список durable
requests и owner lease из одной SQLite snapshot, с проверкой истории, request identity и планов,
построенных из observations. Это advisory projection; `owner_current` — только моментальный lease
match, без dispatch/consumption authority. Sol принял exact diff
`7d0b176f1792d6571447be4dbb175c6e0a6445abe30948edbee7970aa2a2e21b`; focused suite: 17 тестов и 3
subtests, full gate: 408 тестов и 96 subtests, Ruff. Ограничение числа результатов не ограничивает
полную стоимость обхода истории/размер сохранённых observations. Sol подтвердил, что текущие
Executive, lifecycle lease и dispatch Permit не задают controller-owned authorization generation;
A05 consumer не добавлен. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-projection-review-20261021.json`
(SHA-256 `800f6620cacf320ac36bf19bf7d362d2a3c250f56ef02f5c23daa284aa1ce006`).

Follow-up `aebb77c` додаёт cross-connection regression: второй Store коммитит owner release после
request query, но до owner-history чтения projection. Метод должен вернуть coherent старый active/live
snapshot, а свежий read — `released`. Sol принял exact test diff
`f67f6149f6e09c034d5ff4bbfe8923b52509c8a9221f612e57d5982a6729b321`; focused test passed, полный gate:
409 тестов и 96 subtests, Ruff. Один предыдущий полный прогон имел transient failure unrelated code
identity test; isolated и следующий полный gate прошли. Это одна synthetic interleaving, не exhaustive
concurrency proof. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-projection-snapshot-review-20261021.json`
(SHA-256 `21997923339dfd33ef6e5aceeaa51caa2bcb8e35417448cc3f736ad32bb66a84`).

Source `763fd5a` добавляет Store veto перед сохранением lifecycle `launch_request`: внутри текущего
owner fence проверяются все парные версии `controller-parse` circuit и recovery obligation. Открытый
или повреждённый цикл блокирует запрос; два последовательных resolved цикла остаются допустимы. Тест
через настоящий fault producer подтверждает отказ, если fault фиксируется, пока evaluation ждёт SQLite
writer lock. Sol принял exact diff `25208d1da11c3058413cfa31b074ee0539fe8eb666e6e8f175520d7dc1532668`;
focused suite: 22 теста и 3 subtests; итоговый gate: 415 тестов, 96 subtests, Ruff. Первый полный прогон
имел intermittent `killpg PermissionError` в существующем macOS code-identity тесте; изолированный и
повторный полный прогоны прошли. Это только проверка записанного controller-parse recovery: отсутствие
записей не доказывает полную готовность, refs отдельно не аутентифицируются, стоимость обхода зависит
от длины истории. Consumer, stable authorization generation и полный A05 остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-controller-parse-recovery-veto-20261021.json`.
(SHA-256 `53d5076ee97375fb3c025cc222f70eb5179017adc684865329ea4fbc46fe9b09`).

Дополнительно generic decision writer больше не может подменить `circuit/controller-parse` или
`recovery-obligation/controller-parse`; dedicated recovery API и проверка ожидаемых версий сохранены.
Sol принял exact diff `02d8b8b7`; полный gate прошёл 550 тестов и 123 subtests с Ruff/format.
Старые неоднозначные записи не исправляются автоматически, ссылки recovery отдельно не
аутентифицируются; consumer/runtime authority не добавлены.

A04 synthetic integration increment принят Sol на exact diff
`5e03ada485c56a4635da07d89c7cdb6679c21b3ac4cecf790b87af09ca6316cd`, source `62ab4ce`. Один
macOS test связывает persisted admission/binding, manifest v4, command expansion, Seatbelt и строгий
result parser. Полный gate прошёл: 370 tests, 96 subtests, Ruff. Это ограниченное plumbing evidence:
план остаётся `execution_qualified=False`; same-inode, loader/import closure, deployment, continuous
identity, descendants и provenance результата не квалифицированы. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-plan-sandbox-integration-review-20261021.json`
(SHA-256 `8f2472b36d9285a0184acdb531a6c74fd4b27faef2fa9b70804e48cc1bf5dacb`).

Ограниченный A10 source `dc6f7ba` показывает в executive snapshot недостающие отзывы producer и
consumer независимо от наличия готовой consumer-задачи. Каждый пункт привязан к имени, версии и
hash контракта и участнику; acknowledgement автоматически не создаётся. Для опубликованных версий
проверяются activation receipt и исторические acknowledgements. Sol принял точный diff
`b6a173748f99b5163466a1a3ca41878cf3a267a8484bb58d1eb8c3e0e4485849`; gate: 372 tests, 96 subtests,
Ruff. Это только видимость: dispatch отзывов и deadlock из-за task readiness остаются открытыми;
обход ограничен 1,000 proposals/participants и 99 consumers на proposal. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-lane-review-20261021.json`.

Свежий A10 source increment `58a95c5` включает bounded список pending participant reviews в
coherent quiescence snapshot. Если quota доступна, supervisor сохраняет отдельную причину
`contract-reviews-pending` для Sol; при недоступной quota сохраняется `quota-unavailable`. SQL
projection использует те же case-sensitive namespace границы, что reservation и revision triggers.
Sol принял exact diff `20d1ab3b352574fc7faa20a4c8eb572fe6484d3577070de5aad3e11c23fafea1`; gate: 392
tests, 96 subtests, Ruff. Это inert routing для handoff, не review dispatch/recovery. A03 settlement,
полный A10 и operational acceptance остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-aware-quiescence-review-20261021.json`.

Новый A10 source increment `a386bd4` добавляет проверку currentness review request перед записью
одноразового dispatch intent. Store повторно сверяет proposal, producer snapshot, участников и
acknowledgement после получения `BEGIN IMMEDIATE`; устаревшая заявка отклоняется без записи, в том
числе при гонке с другой SQLite connection. Sol принял точный diff; engineering gate: 397 тестов,
96 subtests, Ruff. Это только проверка состояния в точке намерения: последующий drift проверяется
при завершении. Supervisor/provider dispatch и recovery не подключены; A03 settlement, полный A10
и operational acceptance открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-currentness-review-20261021.json`
(SHA-256 `f9c5cf1be1181d4309132c467abe318cd306ddc609aa3f3611edb6171ec8f04e`).

A10 source `8a68dd1` добавляет read-only worklist уже подготовленных review requests: один snapshot
проверяет журнал и freshness, выдаёт только ID, статус, hash и blockers; packet не раскрывается,
dispatch intent не создаётся. `dispatch_eligible` означает лишь current prepared state в момент чтения
и не даёт права отправки. Переполнение заданного предела (максимум 100 записей) отклоняется;
завершённые записи также учитываются, стоимость запроса может зависеть от истории. Sol принял exact
diff `043e73f781be21ad93390ea516b0fb19707fdd3601c840e16e618cee1dbaecc5`; gate: 399 тестов,
96 subtests, Ruff. Supervisor/provider dispatch и recovery не подключены; A03 и полный A10 открыты.
Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-review-20261021.json`
(SHA-256 `be7ca8ca9cd92c890227f363da874c5979ec1694598ebe6a50b99d7479adcc2e`).

A10 source `edbc1f4` включает bounded worklist review requests в coherent quiescence snapshot и
передаёт её без изменений в inert Sol handoff. Store сверяет worklist с выданным snapshot и после
получения `BEGIN IMMEDIATE` проверяет revision, поэтому новая заявка инвалидирует устаревший handoff.
При более чем 100 retained requests указывается `overflow=true`, неизвестное число и пустой список;
частичный список не выдаётся. Публичная форма worklist сохранена. Sol принял exact diff
`e168b3617d4636850c81a9a47a208abbe4a6bcaef5f9e79730e2004ac5b7f44d`; gate: 401 тест, 96 subtests,
Ruff. Provider dispatch/recovery не добавлены; query cost retained decisions, A03 и полный A10 открыты.
Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-request-handoff-review-20261021.json`
(SHA-256 `a08a2cd7e32c4f3cafdc5615b5d0d5471f469e7697d0627d1d62d202db57b600`).

A10 source `86550d9` добавляет локальный replay уже сохранённого contract-review response: точный
packet hash сверяется с текущим durable запросом, response bytes повторно проверяются по custody hash,
а строгий JSON parser отклоняет duplicate keys, неверный UTF-8, не-object и non-finite значения.
Транзакционная финализация заново проверяет состояние и атомарно записывает ACK; malformed/stale
ответы остаются без ACK, повтор точного ответа идемпотентен. Sol принял exact diff
`4e2fc44f80b3730d8437142c48eac30e090fd5eb3e872551e37734316c2758ae`; focused suite: 37 тестов,
full gate: 401 тест, 96 subtests, Ruff. Provider dispatch не добавлен; RPC custody, provider-turn и
usage provenance, A03 settlement и полный A10 остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-local-replay-review-20261021.json`
(SHA-256 `1ef4ae5e8c1db0178c7bd4fd0869311eab52248da71db9dd0608e9067b4f6b15`).

Follow-up A10 regression `d298bfb` exercises local replay while a second SQLite connection holds
`BEGIN IMMEDIATE`, then changes participant inventory before commit. Replay rechecks currentness after
the lock wait, retains the exact response hash, leaves the review blocked, and writes no ACK. Sol
принял exact test diff `5453fe0f828008e785ccd262f1ded02f297236ed72bea2ce6e7f8e523b16c4eb`; focused
test passed, full gate: 402 tests, 96 subtests, Ruff. Это одна synthetic interleaving, не exhaustive
concurrency proof. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-lockwait-review-20261021.json`
(SHA-256 `f5bcdef0fbec6b60a291408633c3e052d6a60d114f4f0614c8993b1b7aa90a5f`).

A10 source `0c32290` добавляет bounded local sweep сохранённых contract-review responses. Один
coherent worklist snapshot ограничен 100 записями; overflow возвращает пустой результат без partial
replay. Обрабатываются только `response_recorded`; prepared и terminal записи пропускаются.
`StateError` сохраняется как ограниченная per-request ошибка, после которой sweep продолжает другие
заявки. Повторно используется custody check и transactional currentness check; dispatch authority,
provider dispatch и resend отсутствуют. Sol принял exact diff
`bd4758b574333de217989fba40eb880b00e269dc87fa39148919b2c4aaa4dcbd`; focused suite: 2 теста,
full gate: 404 теста и 96 subtests, Ruff. Между заявками нет общей транзакции; integrity error
останавливает sweep до writes, а иная неожиданная ошибка может остановить его после предыдущих
commit. `evidence_revision` остаётся opaque: newest-revision selection и conflict adjudication не
добавлены; действует текущая уникальность participant ACK. A03 settlement, provider provenance, полный
A10 и operational acceptance открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-sweep-review-20261021.json`
(SHA-256 `3e776872f8c928c0b265c931caa5721510c29f0545064364761ba41c42c6e90f`).

A04 source `verifier_manifest.py` теперь сверяет descriptor metadata до и после хеширования
interpreter и проверяет, что прочитан точный начальный размер. Regression с изменением файла во
время чтения прошёл; Sol принял exact diff `415857999494287bc3b65f2e7d7035c4ecd6babbaaa187d90d9f23e361badeaf`.
Полный engineering gate повторён с Python 3.13.6: 410 тестов, 96 subtests, Ruff check/format.
Первичный запуск на Python 3.9.6 был несовместим с существующими `zip(strict=)` и JSON decoder
требованиями; результат на закреплённой среде зелёный. Descriptor-read
стабильность не доказывает, что позже был исполнен тот же inode; loader/import closure остаётся
открытым, `execution_qualified=false`. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-stability-review-20261021.json`
(SHA-256 `3d9f73874c37bd0992e96e8292bc9d2df246481087f3f2a873802ddb8090ff76`). Gate rerun:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-stability-gate-rerun-20261021.json`
(SHA-256 `fd14da053f07fa9a99fd0280f0a2edd7e8d3fb72a81d5a5cf3dfa2aedd6af4f3`).

A10 replay-capacity regression закрывает starvation после накопления history: limit=100 применяется к
latest `response_recorded` requests; старые prepared/in-flight/terminal записи не расходуют этот лимит.
Публичный worklist сохранён. Overflow текущих response возвращает пустой replay и unknown counts.
Sol принял exact diff `2313b4b89f305dc4286fd8edf1a18f060a74a5fa366137a1e463f543c3e5f833`; полный gate
на Python 3.13.6 прошёл: 411 тестов, 96 subtests, Ruff. Discovery всё ещё сканирует историю; пропущенные
journals не получают полный packet/currentness audit, а sweep не атомарен между requests. Dispatch,
resend и новая authority не добавлены. A03, provider provenance, полный A10 и operational acceptance
открыты. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-capacity-review-20261021.json`
(SHA-256 `09b11e458a1465fbf00a95d79b7e223dd5496fdf1a886e66ab08d8b4d0ff621a`). Gate:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-capacity-gate-20261021.json`
(SHA-256 `2c1ff6a3a57720680ab528c1b04eeb3c9e2a332e026b4150c05e4fcb3cc7c375`).

A04 `98f4ace` включает хеширование pinned executable и script в deadline команды, отклоняет поздно
вернувшиеся совпадающие Security identity и пересчитывает остаток времени после повторной проверки
перед ожиданием процесса. Если deadline истёк при prelaunch hash, `Popen` не вызывается; позднее
обычное завершение помечается timeout. Sol принял точный diff
`f3ed047e15fe4287b19fb63d1046ec34734b46c268b29064622c26a80b8346ff`; Python 3.13.6 gate прошёл:
419 тестов, 96 subtests, Ruff check/format на 58 файлах. Синхронный Security/filesystem-вызов
нельзя прервать, поэтому это fail-closed классификация после возврата, без гарантии prompt return;
прямой регрессии позднего normal completion не было в этом checkpoint. `execution_qualified=false`;
loader, inode, descendants, stdout provenance и полный A04 остаются открыты. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-deadline-review-20261021.json`
(SHA-256 `f96992390067dd843fdbdd6077674e811f2672219ac224fbe71511d9fe32f18c`).


A04 regression `dacd393` добавляет controlled-clock тест: fake process завершает `wait()` с кодом 0,
но после deadline; результат обязан иметь `timed_out=true`. Sol принял exact test diff
`d426704b4eda1a8c069c5c2bb51398444c4d19a8f0ba06020d8ac1583ee18ed2`. После одного известного
flaky `killpg PermissionError` в `test_running_identity_survives_path_replacement` его отдельный
повтор прошёл; окончательный Python 3.13.6 gate прошёл: 420 тестов, 96 subtests и Ruff check/format
на 58 файлах. Тест подтверждает классификацию на synthetic clock, не реальное время процесса или
cleanup. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-late-completion-regression-20261021.json`
(SHA-256 `09b4a823acc077d626ae4b1bf93f7f38bf6ed3190303f3a797bfee6fc1ba3c3e`).

A05 source: `run_attempt` теперь проверяет целостную пару controller-parse circuit/recovery
history в одном read snapshot до verifier planning. Открытое восстановление, повреждённый JSON,
несовпадающая история и чрезмерная вложенность останавливают попытку до verifier, Git, sandbox и
model work. Sol принял точный diff `d029f6483feaf29b479e78e2aaf2c962659111eac6a789dfc35ef0f2318cefa9`;
engineering gate прошёл: Ruff, формат, 553 теста и 123 subtests. Это только source guard. SQLite
operational errors продолжают распространяться; recovery provenance, A05 consumer/generation,
A03 и operational acceptance остаются открыты.

A05 follow-up: lead decision и связанные contract ACK теперь записываются одним Store transaction,
привязанным к текущим `attempt_id` и `epoch`. План отделяется от caller до lock wait; task/team
берутся из свежего fence. Если attempt устарел после записи usage, lead/ACK не появляются и worker
не запускается; неверный поздний ACK откатывает весь пакет. Sol принял final diff
`583b9a2e2ee46ad00732c1700b4f65b6378bb45a3910aa9fff1dd2ed6ae723d8`. Focused suite: 68 тестов,
10 subtests; финальный gate: Ruff/format, 555 тестов, 123 subtests. Известная macOS code-identity
deadline гонка сначала проявилась в полном запуске, но изолированный повтор и финальный gate прошли.
Это commit-time fencing, а не постоянная свежесть; прямой trusted ACK, A03, release и operational
acceptance остаются открыты.

A04 source increment в `org/development` на базе `9d37eea`: `SandboxRunner` сразу снимает snapshots
списков writable/read/protected roots. Regression доказывает, что мутация caller-owned read-list не
добавляет `/` в профиль, а удаление protected root во время проверки не снимает veto. Sol принял
exact diff `2d40e2bdcf6c3c51196bb592b91f6f6453473bfbe92c513c2a0fbb9fdc504ea5`; полный gate прошёл:
Ruff, format, 567 тестов и 161 subtest. Закрыта только мутация последовательностей во время
конструирования; filesystem identity races, execution provenance и полный A04 остаются открыты.
Исполнение supervisor/runtime не запускалось.

Следующий A04 increment на базе `a6c6499`: `BoundCommand` теперь отсоединяет result contract от
исходного dict и отдаёт неизменяемую mapping только со строковыми полями. Callback regression
меняет исходный контракт command 0 после создания plan: он остаётся `acceptance_json`, strict UTF-8
проверка сохраняется, а later JSON с `passed=false` блокирует diff/review/publication. Прямая
мутация mapping отклоняется; plan identity неизменен. Sol принял exact diff
`a8b2309b6614783d40cf64cd64253997b96879b5d88d47cea63e1a8cb185fc21`. Полный gate: Ruff, format,
567 тестов, 161 subtest. V4 single-command и v5 multi-command manifest binding checks сохранены;
spec-backed план остаётся `execution_qualified=False`, запуск или release authority не добавлены.

A11 source increment на базе `40ac964`: review packet строится из detached snapshot полей task и
command results. Scope, хеш, wire limit и возвращённые данные читают один snapshot. Копировщик
сохраняет tuple argv, не вызывает пользовательские `__deepcopy__` и отклоняет циклы. Regressions
подтверждают независимость от последующих изменений исходных task/check объектов, пересчитывают
`packet_sha256` и закрывают type-laundering попытки. Sol принял exact diff
`080d085c6ddd7aac64b7c5bdc1bc9354122225ec459024fd6af845b3fe885bef`; полный gate: Ruff, format,
571 тест и 161 subtest. Возвращённый dict сам остаётся изменяемым; это не подтверждает качество
independent review, provenance, A03 settlement или readiness к эксплуатации.

Следующий A11 guard на базе `5be5ec2`: independent-review decision теперь парсится отдельным
локальным parser. Он требует ровно `approved` (точный bool), `findings` (list строк), `rationale`
(строка), отклоняет duplicate keys, NaN/Infinity/exponent overflow и неверное fenced framing.
Callback regressions подтверждают, что conflicting `false`/`true` или unclosed fence сохраняют
`review-raw.json`, записывают отказ, не создают `review.json` и не принимают/публикуют candidate.
Sol принял exact diff
`3ee0c37fc8ac1986e1351eea06f10d09428cfe1ee79fdec605bce6c17c90b00c`; полный gate: Ruff, format,
574 теста и 173 subtest. Это локальная проверка формы решения; reviewer authenticity, качество
review, usage settlement, A03 и operational readiness остаются отдельными воротами.
