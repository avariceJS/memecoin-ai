# Два Mac: один проект, изолированные workers

Дата решения: 2026-10-06. Состояние подключения: Mac 1 проверен; Mac 2 ещё не доступен этой
сессии. Переход live runtime не выполнялся. Следующий шаг — перенести bootstrap kit или
дать SSH `user@host` (без пароля в чате), затем проверить Mac 2 и выполнить одну repo-задачу.

Комплект создан: `work/two-mac-bootstrap-20261006-01.zip`, source commit
`eee7ce1a473e97b67047c487ea66dbd631d01c36`. Git bundle проверен, новый checkout на том же
Mac воспроизвёл exact commit/clean state; занятый путь и altered checksum отклонены.
9 tooling tests и synthetic smoke прошли. Это ещё не two-host acceptance и не запуск Qwen.
[Machine-readable receipt](evidence/two-mac-bootstrap-2026-10-06.json).

## Почему именно такое разделение

Последнее измерение: 8 fits вместе со snapshots/audit — 28.64s, peak RSS 1.81GB; acquisition
100 successful GET — около 85min, включая cooldown. Train 24→86 markets не улучшил общий
test: broad GRU хуже control на8.92%; effective retained pools6.19→9.55, несмотря на86market
IDs. Новые pools получили72.09% sampling mass при16.64%строк;20pools имеют<16decisions.
[Первичный результат](brain-training-expansion-results.md). Главные ограничения сейчас —
qualified contiguous data, разнообразие фактических наблюдений, sampling и ширина future exam.
Скорость матричного обучения пока не главное ограничение. Distributed training не вводим.

| Workload | Сейчас/целевая машина | Причина |
|---|---|---|
| Live source, AUTO, registry, future paper, Control Center | Mac 1 | Сохранить работающие pins, журналы, экспирации и единственного владельца состояния |
| Разработка, тесты, review, локальная Qwen | Mac 2 после checkout | Убрать engineering и большие LLM-контексты с Astra и live host |
| Historical campaigns, decode, replay | Mac 2 после bounded qualification | Непересекающиеся ranges, локальный raw journal, импорт sealed результатов |
| Нынешний короткий AUTO training | Пока Mac 1 | Миграция сейчас дороже 28.64s compute; operator adapter ещё локальный |
| Более тяжёлые snapshots/independent fits | Mac 2 после artifact handoff test | 48GB полезны для больших shards/cache, если появится очередь или RAM pressure |

Потеря Mac 2/сети не должна остановить текущий live/paper контур. Сумма24+48GB не является
общей памятью одной модели. Заявленные39GB Qwen могут описывать файл модели; фактические
resident RAM/KV-cache/context/throughput не измерены. На48GB не планировать Qwen35B и тяжёлое
MPS training одновременно: сначала inventory/benchmark, затем отдельные фазы LLM и training.

## Код и работа моделей

Один Git remote и reviewed `main`. Worker получает exact base commit, свою branch/worktree,
task ID, file ownership, acceptance command и запреты по semantics. Возвращает commits/patch,
краткий результат, tests/exit codes и receipts. Интеграция на Mac 1 после review; worker не
меняет pinned live runtime. Один файл не принадлежит двум параллельным задачам.

- Astra: architecture/integration, causal/availability/admission, leakage/holdout,
  preregistration, selection/promotion, execution/risk. Engineering worker возвращает вопросы,
  если изменение пересекает эти границы.
- Grok/Cursor: основной implementation worker после проверки фактически доступного model ID
  через `agent models`. UI, bounded APIs, adapters по утверждённым contracts, тесты, tooling.
- Local Qwen: поиск и сортировка переданных больших текстов, группировка ошибок, краткие
  summaries с `path:line`/hash и пропущенными случаями. Сначала сравнить качество на одном
  известном incident; никакого торгового управления или model promotion.
- Kimi/GLM/DeepSeek/external Qwen: независимый reviewer/critic, тестовые сценарии или bounded
  engineering по результату маленького сравнения. Без проверки доступности и качества не
  назначать одному из них научную authority и не делать fan-out одного вопроса всем моделям.

Qwen-summary — недоверенный производный индекс: первичный текст остаётся доступен Astra.
Противоречия/ошибки/STOP нельзя отфильтровывать; при критическом решении Astra получает
первичные строки, а не только положительное summary. Секреты и protected holdout не передаются
в task packet. Модели не нужны в штатном trading/paper runtime.

## Данные и артефакты

1. Mac 1 — единственный координатор заданий и главный registry. Задание закрепляет task ID,
   code/decoder version, chain/namespace, markets и полуоткрытые ranges `[start,end)`, budget,
   account alias, expiry и as-of. Для current2026 unsupported layouts сохраняется quarantine.
2. Mac 2 пишет только свой campaign/raw store. Диапазоны выдаются до запросов, не на основе
   доходности. Natural request key не содержит account ID: смена credentials не создаёт новый
   полезный запрос. Одинаковый source range не скачивать заново ради использования обеих квот.
3. Общий append-only request/reservation ledger хранится у координатора; во время отсутствия
   связи worker продолжает только свой уже выданный bounded envelope. Сбой/неясный receipt
   означает reconciliation, а не бесплатную повторную выдачу задания другой машине.
4. Передавать завершённые sealed captures/shards/bundles в новый `.incoming/<task-id>` через
   проверенный SSH/SFTP/SCP либо вручную. Сверить manifest membership, hashes и code/data role,
   затем опубликовать отдельным immutable root. Повтор того же hash идемпотентен; конфликт —
   quarantine. SHA проверяет целостность, доверие к отправителю обеспечивает канал.
5. Canonical import имеет одного writer. Mutable SQLite/WAL не синхронизировать через iCloud,
   Dropbox, сетевой shared folder или `rsync --delete`. Только штатный consistent snapshot /
   sealed archive. Уже существуют `tools/brain_shard_snapshot.py` и shard verification.
6. Event time, original receipt/availability, worker clock checks и время импорта разделены.
   Импорт старого event не делает его доступным старой модели в прошлом. Raw admission ещё не
   training admission; F01/D43/reserved holdout и отдельный unseen test не передаются в train.
7. Remote train допускается только с reserved attempt у владельца AUTO, точными data/code/
   recipe/seed pins и split roles. Sealed candidate возвращается на Mac 1; incomplete fit не
   candidate. Admission и future activation происходят на Mac 1; promotion отдельно. Пока
   remote adapter не квалифицирован, существующий AUTO обучается локально.

Это контракт следующего подключения, не уже установленный distributed scheduler. В этом
этапе не создан второй acquisition writer, не выданы remote training reservations.

## Отдельные provider accounts/quotas

Машина не является бюджетной единицей. Для каждого доступа нужны account alias, фактический
plan/no-paid bound, account-wide/per-key/IP ограничения, monthly/request budgets и provenance.
Credentials живут локально на нужном узле; в заданиях только alias и безопасная fingerprint.
Общий account budget делится между узлами, а не умножается числом ключей. Одна Wi-Fi сеть
может также сохранять общий IP limiter.

- Alchemy запрещает multiplex/spread usage across accounts и обход limits (Terms2.2(x));
  throughput общий для account, не для отдельных apps. Второй free account нельзя включить
  как механическое удвоение квоты одного проекта. Нужен реально разрешённый provider scope,
  например отдельные условия/подтверждение provider. [Terms](https://legal.alchemy.com/),
  [throughput](https://www.alchemy.com/docs/reference/throughput).
- CoinGecko запрещает обход rate/monthly limits. Второй host сам по себе не снимает GT
  historical180day/coverage ограничения. [API terms](https://www.coingecko.com/en/api_terms).
- Независимые разрешённые provider budgets полезны: можно разделить non-overlapping birth
  windows/event hydration и GT USD bars, либо квалифицировать второй источник. Их данные
  сливаются только после semantic qualification; 2providers не равны2independent USD feeds.

Никаких signup, новых credentials, paid plans или market-data запросов этот этап не сделал.
Приоритет — useful admitted/disjoint windows и новые cohorts на GET/CU, не raw requests/sec.
Нельзя скрывать429/authSTOP и продолжать тот же campaign через другую учётную запись.

## Подключение второго Mac

Готовится локальный `work/two-mac-bootstrap-20261006-01/` с Git bundle, SHA256SUMS, exact commit
и `BOOTSTRAP.command`. Перенести папку целиком на Mac 2 через AirDrop/проверенный канал;
открыть `BOOTSTRAP.command`. Он создаёт **новый** `~/Developer/memecoin-worker`, сверяет commit
и запускает только read-only inventory. Если путь занят — отказ, без перезаписи. Не запускает
сервисы, market requests, pip install, model inference или training. Не отключать macOS
защиту ради скрипта: в случае блокировки использовать команды из README комплекта.

Альтернатива — обычный clone этого репозитория в отдельный путь и checkout exact commit.
Development окружение (`make setup`) и model catalogue (`agent models`) проверяются следующим
шагом после inventory. Credentials основного Mac, runtime/LaunchAgents, `work/` и datasets
через source Git bundle не передаются. Для работы без ML достаточно dev env; ML env отдельный.

`tools/worker_preflight.py` можно скопировать отдельно ещё до clone. Он использует stdlib,
читает RAM/disk/Python/Git/Cursor version и metadata Ollama только на loopback: `/api/tags`
и `/api/ps`, без proxies/redirects/inference. Не выбирает модель по выдуманному ID, не читает
ключи и пишет новый JSON с отказом перезаписать старый. API:
[installed models](https://docs.ollama.com/api/tags), [loaded models](https://docs.ollama.com/api/ps).

## Проверка реального выигрыша

Каждая задача возвращает receipt: task/base/final commits, node/model (фактическая строка из
CLI/response, если есть), ownership, input/output hashes, start/end, tests/exit codes,
unresolved issues, review/rework time и accounting source. Input/output token usage и quota
delta — только если сервис реально их показал; иначе `null`. Tool-call count не token count.

Сравнить первые5полезных задач по lead time, accepted/reworked доле, Astra input/output usage
если доступен и объёму возвращённого контекста; учитывать повторный review/failed attempts.
LLM-модель без внешней token quota всё равно расходует RAM/энергию/время. Цель2–3x — гипотеза,
не measured result. Предыдущие72.9k относились к исходному объёму tool output ДО truncation,
а не доказанному billed контексту. Размер файлов в bytes также не равен символам или токенам.

Сейчас `agent_context.py` гарантирует полные AGENTS/now, bounded остальные excerpts и source
hashes. Он не измеряет weekly quota. После подключения Qwen отслеживать фактическое уменьшение
входа Astra и стоимость исправления потерянных/искажённых summaries.

Ближайший научный прирост: широкий contiguous qualified USD corpus и зарегистрированная
проверка support-aware sampling на новых будущих случаях; не ускорение нынешнего28sfit и
не sweep по уже раскрытому test. Продолжительность future exam остаётся реальным временем.
