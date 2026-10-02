# Причинные CPMM observations/features и bounded forward corpus

D35: следующий этап после economic state tape. Он использует **новые полученные сейчас**
finalized account snapshots; поздняя историческая реконструкция остаётся отдельной.
Обучение, trading и повторное открытие этапа второго RPC не входят в этот сбор.

## Контракт времени

`getMultipleAccounts` возвращает pool/config/vaults/mints и Clock одного bank.
Нормализация допускает только schema 2, allowlisted tokens и связанные счета. Raw
ответ сохраняется без преобразования. Старые checkpoints задают cohort/адреса, но
не становятся новыми наблюдениями. Live captures обязаны начаться после frozen plan.

Три разных времени:

- `received_ns` — завершение получения raw RPC response;
- `available_ns` — завершение декодирования наблюдения в текущем процессе;
- `as_of_ns` / `feature_available_ns` — время среза входов / завершение вычисления features.

В каждой строке `input_available_ns <= as_of_ns <= feature_available_ns <= decision_ns`.
В collector условное earliest decision time равно feature_available_ns. Это timestamps
read pipeline, не отправка ордера и не latency исполнения. Chain Clock не используется
как receipt time. Clock age включает возможную рассинхронизацию часов; отрицательное
значение блокирует строку, а не обрезается до нуля.

`CausalBook` общий для live вычисления и offline воспроизведения. Ingest идёт по времени
доступности; более поздние данные не меняют ранее выпущенные rows. Перемотка observation
clock запрещена. Offline verifier заново декодирует raw и сравнивает каждый online frame.
Экономический state tape с historical_available_ns=null не принимается этим интерфейсом.

## Признаки и маски

12 фиксированных признаков: log reserve-ratio price с decimals, log reserves в token
units, trade/creator fee rates, два transfer fee rates, receipt age, chain Clock age,
изменение log price, интервал между наблюдениями и slot delta. Это snapshot changes,
не trade returns/volume/flow. Никаких будущих endpoint-поправок, интерполяции или fitted
normalization. Признаки не заменяют полную conditional quote/execution model.

Каждая row содержит usable, причины, nullable values и явную mask для каждого поля.
Missing не заменяется нулём. Warmup требует два разных свежих bank snapshots. Повтор
того же bank с тем же содержимым не обновляет age и не создаёт ещё один price return.
Конфликт same-bank или regressed slot дают gap. После timeout, длительного пропуска
или непригодного предыдущего snapshot return history сбрасывается; нужна новая пара
валидных наблюдений. Из нового полного snapshot можно восстановить состояние, но
не пропущенные сделки, поэтому trade activity между polls остаётся unknown.

Первый план: max receipt age 25 s, max chain age 45 s, max gap 25 s. Это заранее
заданные инженерные ограничения, не оптимизированная торговая частота. Features
изменяются только после доступных на тот момент входов. Mints/units/cohort находятся
в plan references и raw captures; universe selection фиксирован до новых запросов.

## Targets и временная граница

Target — изменение **наблюдаемой** log reserve-ratio price через 20 s, без утверждения
об executable return. Берётся первое дальнейшее пригодное наблюдение не раньше
horizon end и не позже него более чем на 15 s. Его реальная feature publication time
задаёт maturity/available time target. Будущих значений нет в features.

Invalid start, gap/смена segment, слишком поздний endpoint и конец покрытия дают
явное censoring. Нет заполнения последней ценой за концом corpus. Если target calibration
строки становится доступен на/после frozen calibration cutoff, строка **purged**.
Остальные разделяются на calibration и engineering_evaluation по decision time.
Это диагностическое разбиение, не закрытый финальный holdout и не результат ML.
Сохраняются также цензурированные и purged строки; targets вычисляются отдельно offline.

## Collector и воспроизведение

```sh
mcai collect-forward-corpus --checkpoint PATH_TO_OLD_CLOCK_CHECKPOINT \
  --source-alias helius-forward --rounds 12 --interval 10 --out work/new-forward
mcai verify-forward-corpus work/new-forward
```

Для нескольких pools повторить `--checkpoint`. Endpoint берётся из отдельной
MEMECOIN_HELIUS_RPC_URL в окружении; CLI dotenv не исполняет/не загружает. URL/key
никогда не сохраняется в plan/logs. Runtime остаётся stdlib, read-only.

До первого запроса фиксируются cohort, exact request parameters, source/config,
absolute calibration cutoff, лимиты и отсутствие retries. Default: 12 rounds,
10 s cadence, ≤4 pools, per-call timeout 10 s / byte cap 1 MB, spacing 1 s. Это
кооперативный wall budget rounds*interval+2*timeout; socket timeout не объявляется
жёстким общим сетевым deadline. Первый transport/RPC/validation error останавливает
серию; raw/error, последний feature frame и censored targets остаются. Успешные ответы
не скачиваются заново. Недостающие rounds не изображаются нулевой торговой активностью.

Bundle хранит plan, code snapshot, references, каждый реальный capture, append-only
attempts и online frames, features, targets, report и manifest. Existing output запрещён.
Verifier проверяет hashes, requests/cohort, publication ordering, воспроизводит значения,
маски, partitions и report. Exit 0 означает завершённый план сбора; training_ready,
market_dataset_qualified, execution_qualified и edge_established остаются false.

Границы: маленький fixed cohort, finalized lag, неизвестные intervening trades,
неполное покрытие режимов/births/deaths, некалиброванные inclusion/costs. Next gate —
подходящий объём prospective data и наблюдаемые execution/label assumptions, а не
принудительное обучение на коротком engineering sample.

## Уточнение targets schema 2 по результатам первого сбора

Первый bounded corpus собран кодом `a666c5b`, с targets schema 1 (отсутствующий
маркер в его плане). В неполном последнем раунде frame может законно показывать
последнее известное состояние ещё не stale пула, который не успели опросить.
Для **features** это допустимый causal carry-forward с возрастом. Для **target**
такой frame не считается новым конечным наблюдением.

Поэтому новая default schema 2 требует `future.input_available_ns >= horizon_end_ns`
и `future.slot > start.slot`. Coverage end определяется отдельно по последнему
полученному snapshot каждого pool, а не по общему времени последнего frame.
Publication/maturity и временная граница остаются обязательными. Effective endpoint
может быть позднее nominal horizon в пределах tolerance; оба времени сохраняются.

Исходный corpus не переписывается; его schema 1 воспроизводится только как история.
Исправленные targets создаются отдельно, с SHA родительского manifest и новой версией
source, без RPC и без создания преобразованных captures:

```sh
mcai refine-forward-targets work/causal-forward-session-01/corpus \
  --out work/causal-forward-targets-02
mcai verify-forward-targets work/causal-forward-session-01/corpus \
  work/causal-forward-targets-02
```

Derived verifier сначала полностью воспроизводит исходные features, старые targets
и report из raw. Явный compatibility replay допускает только отличие версии source,
при **точном совпадении всех archived values**; проверки данных не отключаются.
Для самостоятельного old-corpus CLI это `verify-forward-corpus --compatible-version`;
без флага требуется совпадение source. Сбор новых corpora фиксирует target_schema=2.
Тот же sample использован для инженерной доработки контракта и не является final holdout.
