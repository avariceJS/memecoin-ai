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
(SHA-256 `30a9fac250685cb73ad28db0575b6d75322c196c9f44d43bb122682b3c4c47f4`). Автоматическая
свежесть trigger, generic contract migration, department plans/leases и полный A10 остаются открыты.

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
