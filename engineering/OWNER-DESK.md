# Founder Desk — 8 ноября 2026: консолидация, компания на паузе

Основной код: `~/Developer/memecoin-research`, GitHub `avariceJS/memecoin-ai`, ветка `develop`.
`main` не изменена: `2f8da5f92da521b511e0d4fb20205981a000cab5`. На GitHub осталось 2 ветки вместо 45;
удалены 116 временных worktree. История всех 117 прежних checkout HEAD сохранена в опубликованной
истории. Brain и организация объединены; необходимые Linux/deployment-инструменты сохранены.
Точные SHA текущей разработки проверяются через `git rev-parse HEAD` и удалённую `develop`.

Из 9 основных локальных каталогов осталось 2: проект и единственное приватное хранилище
`~/Developer/memecoin-runtime`. Их суммарный `du` уменьшился с 154,23 до примерно 20,0 GiB
(данные SSD не входят; ссылки не обходятся). Сам проект занимает примерно 0,63 GiB.
Удалены старые `brain-org-build` и три fix-копии, `brain-org-state`, прежний `brain-paperclip-lab`,
`~/Desktop/memecoin-ai`, `~/BrainRestore`; нужное состояние lab перемещено в runtime.
Дополнительно удалена обычная копия ResearchData на внутреннем BrainDevelopment.

Канонические данные — `/Volumes/Kingston/BrainData`, около 40,8 GiB. Сохранены рыночные наблюдения,
модели, champion, целые SQLite-семейства, manifests и защищённые кандидаты. Рабочие ссылки Brain
ведут в `BrainData/brain/{owner,research}/work`. UUID/root marker Kingston и порог 50 GiB сохранены;
BrainDevelopment — другой внутренний том. Защищённые данные не читались и не хэшировались.

С SSD удалены `BrainMigrationV1`, `BrainRestore-20261018`, устаревшие software-only host kits v1/v6b,
воспроизводимые ML/browser/Rust окружения и 23 старых дерева исходников. Найденный точный Agave v5
observer и уникальные неутверждённые CPI/WSOL/Token2022 исходники опубликованы до удаления копий.
Общее измеренное увеличение доступного места Kingston — примерно 53.2 GiB; фактическое свободное
место меняется от фоновых записей. Из смешанного Browser/Codex/Cursor удалена только доказанная
копия Brain (~109 MiB); личные браузеры, пароли, кошельки, переписка и другие проекты оставлены.

Paperclip отвечает на HTTP/API с прежней точной версией `d06cbd5`. Его 44 собственных commit
воспроизводятся из закреплённого upstream и опубликованного source delta, без старых bundles.
Astra High, 6 Sol 6.1 High, 40 Luna и 5 необязательных reviewer сохранены. Все 59 исторических
профилей сохраняют model/effort/env/provider/runtime/reporting/permissions/holds; исходные задачи
и ID/статусы запусков совпадают с состоянием до переноса. Все 6 компаний PAUSED, новых запусков нет.

Действующий publisher переведён на `develop`; инструкции содержат CLEAN-AS-YOU-GO. Собственный
временный publication ref удаляется после подтверждения. Принятые source workspaces передаются
существующей native cleanup через проверенную базу сравнения; активные/dirty/неопубликованные
состояния не удаляются. Новых сервисов и обязательной QA для каждой мелкой задачи нет.

Прошли synthetic Brain smoke, 355 Linux/shared проверок (5 skipped), focused publisher/receipt tests,
31 plugin test и build, 40 UI-тестов с точным воспроизведением projection, проверки переносимого
worker-интерпретатора, storage/guard/frozen-importer checks, точная реконструкция Paperclip, независимое
review существенных изменений, read-only runtime readback и проверки Git. Наборы пересекаются;
это не полный acceptance campaign и не доказательство качества моделей, Linux deployment или
нового автономного цикла компании. Реальная торговля, коллекторы, обучение и платные API не запускались.

Вне GitHub неизбежно остаются секреты, живая БД Paperclip, durable STOP/receipts и данные/модели.
В runtime нужны два технических checkout: закреплённый сторонний Paperclip и независимый sanitized
worker seed с приватным interpreter capsule. Это явное исключение из буквального «один Git-root»:
native sandbox даёт доступ к общему Git metadata, поэтому связывать workers с основным `.git` нельзя.
Неподтверждённые 19 исходных custody bundles оставлены при исторических holds; текущая установка
от них не зависит. Небольшое непрочитанное защищённое состояние осталось на Mac (~31 MiB включая
конфигурацию/custody) и в двух исходных D43-контейнерах BrainDevelopment (~39,5 MiB allocated).
Смешанные непрозрачные host-recovery записи и недоступная `.Trashes` SSD не уничтожались.
Новых резервных копий, архивов или страховочных веток не создано.

Следующий запуск компании требует нового явного разрешения Founder и свежей сверки состояния.
Исторические v9 bridge missions остаются held/fail-closed; их старые policy/intent не переписывать.
