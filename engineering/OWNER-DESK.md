# Founder Desk — Brain Engineering Organization

**2026-10-21. Независимый аудит завершён; основатель разрешил продолжить инженерную разработку.**
Пилот остаётся остановлен. Проверенный исходный код аудита: `org/development`, `b2dcd6d`;
документационный checkpoint: `0054a9e`. Ни эксплуатационный запуск, ни снятие STOP не разрешены.

## Решение директора

Сохранить SQLite, журнал, fencing, изоляцию исполнителей, независимую приёмку и единственного
publisher. Они образуют пригодный фундамент. Следующий этап должен собрать и доказать полный
рабочий цикл компании. Расширение штата и новые уровни управления сейчас преждевременны.

**Готовой автономной компании пока нет.** Есть инженерный прототип с серьёзными проверенными
компонентами. Первая реальная задача ещё не прошла весь путь до независимой приёмки и интеграции.
Недельную эксплуатацию сейчас разрешать нельзя; полная перепись Brain или контроллера не нужна.

## Что подтверждено

- Оба прежних Goal через текущий `thread/goal/get` имеют статус `paused`. Последняя запись `active`
  в rollout основного владельца была устаревшей. Native metadata и последний turn context подтверждают
  модель владельца Luna High. Проверки статуса ничего не возобновляли.
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

## Следующая последовательность после отдельного разрешения

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

Native-состояние показало: текущий аудитный поток — Astra без Goal; прежний Luna-root
`01a15657-3558-79c1-86dc-6ed033c264df` — `gpt-6-luna/high`, его Goal приостановлен, активных
попыток в пилотной БД нет. Передача продолжится только через этот единственный сохранённый Luna
поток с его историей и счётчиками. Организационный runtime и LaunchAgent не запущены.
Эксплуатационные ограничения основателя остаются в силе; рынок, обучение, AUTO, торговля,
защищённые данные, production, `main` и платные ресурсы не затрагиваются.

Goal `01a15a6e-0e03-77d2-b3ce-d81b95b5a89f` сообщил `blocked` после повторных исследований A03;
Goal и история сохранены. Поскольку нативный API не возобновляет заблокированный Goal, основатель
разрешил ровно одного successor — `01a15b1c-c263-71b0-bfca-c5b46d17a89b` (`gpt-6-luna/high`).
Он продолжает ограниченную разработку A05/A10/A04. Связь записана во внешнем handoff. Свежая
read-only проверка пилота: 6 неудачных попыток, 0 активных leases, 0 acceptances и 0 integrations;
STOP сохранён, LaunchAgent не загружен. Единственный активный Goal продолжает независимую разработку
и проверку A05/A10/A04. A03 остаётся fail-closed и блокирует только полный release; неизвестный usage
и его резерв сохраняются. Повторный запуск пилота остаётся отдельным разрешением.

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
независимые release gates. Native Goal пока отображается как `blocked`: доступный Goal API позволяет
читать и менять статус, но не менять objective и не возобновляет blocked Goal. Существующий ID,
счётчики и история сохранены; новый Goal не создавался.

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
подтверждений. Dependency obligations, department plans/leases и широкое закрытие A10 остаются
открыты. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-durable-contract-transition-review-20261021.json`
(SHA-256 `d94296b79abae78b7d22cce4348b750a2123046dbb860250ff13407490e9424e`).

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
