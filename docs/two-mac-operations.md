# Управление двумя Mac

Дата: 2026-10-06. Development checkout проверен на Mac 2:
`/Users/sl-aleksandr/Developer/memecoin-worker`, M4 Pro / 48 GiB.
Mac 1 остаётся владельцем source, registry, AUTO, короткого training и paper.
SSH `root1@192.168.31.66` и synthetic artifact round trip проверены между двумя машинами.
Live runtime и production activation ниже на Mac 1 не квалифицированы.
Read-only GET `/api/status` вернул schema `operator-status-v1`, mode `auto`, phase `waiting`,
`domain_stop=false`. Listener PID 64937 совпал с LaunchAgent `ai.memecoin.brain.operator`
и runtime command для pinned `runtime08`. Release rehearsal на Mac 1 не проводилась.

## Разработка

Bootstrap receipt найден на Desktop, все пять SHA256SUMS совпали. Ветка
`worker/mac2-bootstrap` fast-forward обновлена с `eee7ce1` до `a594474` перед работой.
Python 3.13 установлен в `/opt/homebrew/bin/python3.13`; dev env — `.venv`.
Системный `python3` по-прежнему 3.9, поэтому setup:

```sh
make setup PYTHON=/opt/homebrew/bin/python3.13
make check
make smoke
```

Изменения → review → checks → commit → push. Git передаёт код и curated evidence;
`work/`, модели, credentials и live базы через него не передаются. Push не запускает
production restart. `.codex/config.toml`, возникший локально во время сессии, не включён
в изменения проекта.

## Подключение Mac 1

Нужен `root1@host` и включённый macOS Remote Login для этой учётной записи.
Проверка ключа host выполняется по fingerprint с Mac 1 через доверенный канал.
Пароли/private keys в чат не отправлять; `StrictHostKeyChecking=no` не применять.
Для unattended transport нужен уже проверенный SSH key/agent; `BatchMode=yes`
сознательно откажет при интерактивном password prompt.

2026-10-06: Mac 2 использует отдельный ключ `memecoin_mac2_ed25519`; Mac 1 добавил его
в `authorized_keys`. Host fingerprint сверён: `SHA256:hkTc4KWC0pjJKWvpafiv1I0yc3ue9A3fgdqvPXkEUMA`.
SSH отдал `arm64`; system Python на Mac 1 — 3.9.6. Для проверки команды явно задавали
IdentityFile и временный known_hosts. Permanent SSH config Mac 2 не менялся.

Для Control Center после подключения можно создать SSH tunnel:

```sh
ssh -N -o StrictHostKeyChecking=yes -L 18792:127.0.0.1:8792 root1@HOST
```

Тогда панель Mac 1 доступна на Mac 2 по `http://127.0.0.1:18792`.
Это инструкция, не уже работающий tunnel. Mac 2 не получает второго source writer.

## Артефакты

`tools/worker_artifacts.py` использует существующий формат `manifest.json`:
точное отображение relative filename → SHA256, без manifest в самом отображении.
Вход — явно выбранный sealed root и независимо проверенный manifest pin.
Классы: `report`, `dataset-shards`, `sealed-snapshot`, `completed-model`.
Role — заявка отправителя, а не автоматическое доказательство training admission/completion.
После транспорта существующие domain validators и canonical owner обязаны проверить bundle.

Пример с заранее проверенным небольшим exploratory report:

```sh
.venv/bin/python tools/worker_artifacts.py pack /ABS/SEALED_REPORT /ABS/report01.tar \
  --id report01 --role report --manifest-sha256 MANIFEST_SHA256
.venv/bin/python tools/worker_artifacts.py receive /ABS/report01.tar \
  --store /ABS/worker-exchange --sha256 ARCHIVE_SHA256
```

`pack` возвращает archive SHA256. Новый output обязателен; старый файл не перезаписывается.
Приём проверяет archive pin, каждый member, полный manifest, duplicate paths, regular-file
types и bounds. Staging лежит в `.incoming/`; только проверенный каталог атомарно становится
`STORE/ID/payload`. Неудачный staging остаётся для разбора. Один writer на store через flock.
Повтор ID с тем же envelope/payload возвращает `already_present`; конфликт/порча — отказ.
При обрыве канала передача безопасно повторяется целиком, byte-offset resume не реализован.

SSH-передача тем же проверяющим кодом, после отдельной проверки host key:

```sh
.venv/bin/python tools/worker_artifacts.py send /ABS/report01.tar --host root1@HOST \
  --remote-python /ABS/PYTHON311_OR_NEWER \
  --remote-tool /ABS/CHECKOUT/tools/worker_artifacts.py \
  --remote-store /ABS/worker-exchange \
  --identity-file /ABS/SSH_PRIVATE_KEY --known-hosts /ABS/VERIFIED_KNOWN_HOSTS
```

`--identity-file` и `--known-hosts` необязательны при корректном системном SSH config/agent.
При явной передаче ключа host key проверяется строго по указанному файлу; global known_hosts
для этой команды выключен.

Обратное направление выполняется с другой машины той же командой. Отправитель и принимающая
машина должны иметь проверенную копию tool. SHA256 подтверждает целостность, SSH — peer.
Первый реальный probe должен быть synthetic report, не market/holdout dataset.

Двуххостовая проверка 2026-10-06: Mac 2 передал `synthetic-probe01` через SSH stdin receiver
в `/private/tmp/memecoin-exchange-validation-20261006` на Mac 1. Archive SHA256
`df3609852d43a7c8ca67d8eae0047afc3adf378ba3bc5851fd6abe5ff0a52f64`, manifest SHA256
`6f3839a1410c015c840781d633537322ea5d6d5949f86a5d9cbb27181d75fe81`; статус на Mac 1 —
`published_transport_only`. Mac 1 перепаковал тот же report как `synthetic-return01`, Mac 2
скачал архив и принял его с pin `2fed84a2d933df5467e512836b24edbddeeebaba22d48ec7a6b7cfdd6e86112f`.
Manifest и hash `report.json` совпали на обеих сторонах. Повтор той же отправки через CLI `send`
с явными identity/known_hosts вернул `already_present`. Это доказывает канал и целостность
transport для маленького synthetic report, но не domain admission, live exporter или
production release activation.

Не упаковывать mutable root и не создавать transport manifest над открытой базой.
Для SQLite сначала штатный consistent snapshot/backup, затем seal; для обучения предпочтительны
`tools/brain_shard_snapshot.py` и portable shards. WAL/SHM, links, credentials/protected names
отвергаются. Проверка имени не заменяет review содержания: секрет под произвольным именем
автоматически не распознаётся. Absolute references внутри bundle не переписываются;
целостность транспорта не делает такой bundle переносимым или admitted.

## Обновление Runtime

Первый интерфейс — CLI, действия в Control Center ещё нет. `tools/worker_release.py`:

1. `prepare EXACT_40_CHAR_COMMIT NEW_RELEASE_DIR --repo CHECKOUT` извлекает committed code,
   запускает ruff, format, mypy, pytest и synthetic smoke, сохраняет hashes/logs. Не использует
   грязные исходники checkout. Выполнять Python-интерпретатором целевого runtime с dev deps;
   receipt закрепляет interpreter hash. Это не cross-host перенос виртуального окружения.
2. `bind RELEASE_DIR OPERATOR_ROOT NEW_BUNDLE` проверяет release и прежний deployment,
   stage без активации, сохраняя config/source config/expiry/historical corpus refs.
   Возвращает и записывает `NEW_BUNDLE-update.json` рядом с bundle.
3. Дождаться завершения exams и jobs, нажать штатную «Пауза» в Control Center.
   `activate UPDATE_RECORD NEW_JOURNAL_DIR` проверяет состояние, выгружает только operator
   LaunchAgent, получает owner/background locks, переключает deployment/plist и запускает
   operator. Проверяет schema `/api/status`, PID LaunchAgent, владельца listening socket и
   pinned runtime в process command; при startup failure пытается вернуть прежний код.
   После успешной проверки возобновить AUTO штатной кнопкой. Source LaunchAgent не меняется.
4. `rollback UPDATE_RECORD NEW_ROLLBACK_JOURNAL_DIR` выполняет такие же gates, но переключает
   на прежний code bundle. Новые ledger events не удаляет и старую SQLite не восстанавливает.
   Если expiry наступил во время неудачного запуска, recovery возвращает код, но оставляет
   expired service выгруженным. Ошибка recovery сохраняется в отдельном receipt.

Активация отказывает при AUTO без паузы, queued/running jobs, unexpired managed exams,
истёкшем rollout, занятом owner/background lock, pin/config/interpreter drift или изменении
операторской функции SQLite schema. Frozen exams, models, expiry и budget не продлеваются.
Независимые legacy/expansion exams не переключаются и не относятся к operator update.

Проверки rollback на synthetic roots сохраняют и события, добавленные после активации.
Это не универсальный migration manager: schema migration и semantic rollback после изменения
форматов требуют отдельного протокола. Перед первым production use необходим host rehearsal
и review конкретного revision. При аварии питания между двумя заменами файлов сервис остаётся
выгруженным; intent journal содержит обе версии и прежний plist. Автоматическое crash recovery
для такого разрыва пока не реализовано; не делать новый update поверх незакрытого intent.

## Модели И Локальная Очередь

Cursor CLI `2026.10.01-e373342` установлен в `~/.local/bin/agent` по
[официальной инструкции](https://docs.cursor.com/en/cli/installation).
`agent models` пока возвращает Authentication required; нужен `~/.local/bin/agent login`.
Grok/Kimi/GLM/DeepSeek не объявляются доступными без каталога и проверенной задачи.
Cursor MCP list пуст. Внешний сильный MCP-Qwen пока отложен по указанию пользователя.

Локальная Ollama модель отдельно: `qwen3.5:35b-a3b-q8_0`, reported 36.0B / Q8_0,
38,697,031,228 bytes на диске. Один bounded review: 2242 prompt / 900 output tokens,
39.04s server total, около 47.1 output tok/s; ответ закончился `length`, review не принят.
Ollama ps сообщил 36,989,095,114 bytes size/size_vram при context8192, это API accounting,
не независимо измеренный process RSS. Swap вырос 5447.50→14569.00 MiB, memory_pressure free
88→7%. Впоследствии модель выгрузилась. Это измерение одной задачи, не общий benchmark.
Фактические counters доступны через [Ollama generate](https://docs.ollama.com/api/generate),
residency metadata — через [ps](https://docs.ollama.com/api/ps).

Постоянная роль local Qwen: необязательные краткие annotations над предварительно сгруппированным
операционным evidence. Не architecture/code reviewer, не source of truth, не gatekeeper,
не operator commands, не интерпретатор STOP и не selector/promoter. Даже неполный/ошибочный
annotation не меняет deterministic summary и не скрывает источник от сильной модели.

`tools/local_evidence_queue.py` принимает **curated JSONL**, а не произвольный production log:
поля строго `at_ns`, `severity` (INFO/WARN/ERROR/STOP), `component`, `reason`, `source_ref`.
Текстовые поля — bounded machine tokens; никакого full provider response или credentials.
Exporter реальных Mac1 журналов ещё не подключён. Пока квалифицирован synthetic packet.

```sh
.venv/bin/python tools/local_evidence_queue.py enqueue CURATED.jsonl \
  --queue work/local-evidence --scope exploratory
.venv/bin/python tools/local_evidence_queue.py drain \
  --queue work/local-evidence --model EXACT_INSTALLED_TAG
.venv/bin/python tools/local_evidence_queue.py compute \
  --queue work/local-evidence -- YOUR_BOUNDED_TRAIN_COMMAND
```

`enqueue` сохраняет полный packet/hash, считает группы/времена/counts и примеры source refs.
STOP/ERROR не фильтруются. Повтор идемпотентен; перед annotation summary пересчитывается
и сравнивается с оригиналом. Attempt без завершённого ответа требует review, а не auto-retry.
`drain` берёт максимум один job: shared flock,
Ollama idle, memory free≥70%, модель≤60% физической RAM, input≤6000bytes, context4096,
output≤256tokens, keep_alive0. Порог RAM — консервативный engineering bound, не калибровка.
Текущая Q8 35B его не проходит; unattended inference не установлен. Для следующей qualification
подходит отдельно выбранная меньшая quantization/model, но ничего нового не скачивалось.
`compute` использует тот же lock и требует выгруженный Ollama; внешние training процессы,
которые обходят wrapper, этим lock не управляются. Запускать тяжёлое обучение отдельной фазой.

Периодический запуск drain (например, раз в15min) имеет смысл только после подключения
curated packets и qualification подходящей локальной модели. Сейчас нет LaunchAgent/24h loop.
Deterministic reducer уже работает без LLM и может быть дешёвым постоянным preprocessing.
Реальное сокращение billed quota Astra/Grok не измерено; размер summary в bytes не tokens.

LLM-derived market features не admitted и эксперимент **не проведён**. Потенциальная проверка:
только архивированные inputs с фактическим availability, exact model/prompt pins, annotation
receipt time, отдельный заранее заданный split, ablation против тех же deterministic features,
учёт latency/missing annotations. Никакого F01/D43/holdout, future outcomes в inputs или
подбора feature по уже раскрытому future test. Сейчас полезнее сокращение объёма evidence,
а не добавление LLM в training/runtime.

## Итоговая Приёмка

Code `ab8c1a7`: отдельная exact Git installation прошла ruff/format/mypy,978pytest tests
(12optional skips: Torch/Playwright) и synthetic smoke. Synthetic bind/switch/rollback
сохранил новое event после activation; production LaunchAgents не вызывались.
UI portability commit `3854eb2`; GitHub auth подтверждена как avariceJS.
[Machine-readable evidence, model receipts и review/rework](evidence/two-mac-worker-2026-10-06.json).
Это local acceptance, а не two-host integration или live rollout.
