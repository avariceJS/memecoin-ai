# История изменений решений

История концепции восстановлена из обсуждения при создании памяти 2026-10-01. Точные даты двух предыдущих reviews отдельно не зафиксированы; ниже используется порядок обсуждения, а не выдуманная хронология экспериментов. Экспериментов не было.

## Исходная пользовательская концепция

Предполагались collector → historical dataset → simulator → pretraining → token selection → trading/RL → out-of-sample/walk-forward → paper. Рассматривались individual encoders, cross-token attention, portfolio state/memory, policy/value heads; действия SKIP/ENTER/HOLD/REDUCE/EXIT плюс size. Пользователь прямо просил критиковать любые предположения, включая необходимость RL.

Цель — самостоятельная обучаемая модель на PyTorch, два M4 Pro по ~24 GB, вероятно Solana, Axiom лишь рассмотрен. На старте без реальных денег. Упоминание «LLM» в конце исходного запроса уточнено как независимая торговая ML-модель, не языковая модель.

## Первый архитектурный review

Предложены shared TCN + heads, отдельный portfolio/risk/execution, отсутствие обязательного RL, target positions и conservative simulator. В качестве стартовых примеров указаны 1–5M параметров, top-K 100–300, решения 5–15 s, horizons 1/5/15 min, один тип пула и более поздняя стадия жизни токена. Cross-token aggregates и ограниченные wallet features предлагались раньше сложных attention/GNN.

Эти значения были предположениями, не результатами измерения. Следующий review существенно ослабил их нормативный статус.

## Второй критический review и текущая база

| Ранее | Теперь | Причина |
|---|---|---|
| TCN основной стартовый backbone | TCN, GRU, небольшой Transformer сравниваются | Нет доказательств превосходства на наших данных |
| Размер 1–5M | Scaling от ~0.5M до 30M; 100M условно | Размер должен следовать данным, diversity и learning curves |
| Forecast + optimizer как достаточная основа | Обязательное рассмотрение sequential MPC baseline | Однопериодность теряет стоимость будущего капитала |
| RL просто отложен | Конкретный условный опыт recurrent PPO после imitation; IQL при подходящем dataset | Разделены interactive simulator и logged offline data |
| Жёсткая модульность | Возможное partial joint decision training после pretraining | Промежуточные targets могут ограничивать полезность |
| Дешёвый scanner на 100–300 | All-token lightweight encoder равноправен; K экспериментален | Scanner может потерять лучшие ранние возможности |
| Агрегаты как раннее ограничение | Pooling/attention проверять рано | Возможна значимая миграция капитала и групповой контекст |
| Фиксированная сетка позиций | Сетка как baseline, adaptive/continuous optimizer позднее | Учесть quantization против jitter/turnover |
| Несколько фиксированных future returns | Action-conditioned net outcomes + рыночные targets; horizons сравнить | PnL требует continuation policy и исполнения |
| Решения 5–15 s | Определить из arrival/execution latency и decay edge | Возможно слишком медленно; данных для выбора нет |
| Regular sequence для TCN | Hybrid bars/events как гипотеза | Агрегация теряет порядок быстрых событий |
| Простые wallet features | Soft clusters → graph features → локальный embedding | Промежуточный вариант до большого temporal GNN |
| Ограниченный рынок целиком | Широкий сбор, узкая достоверная симуляция | Не исключать потенциальный edge вместе с удобством реализации |
| TCN + heads как возможный конечный вид | Гибридная V2/V3 по ablation | Архитектура должна развиваться по измеренным bottlenecks |

Сохранены: приоритет достоверности исполнения, независимые risk limits, строгая временная оценка, отсутствие гарантированной прибыли, optional RL, champion/challenger вместо автоматического promotion.

## 2026-10-01 Создание памяти проекта

Пользователь принял последнюю концепцию как текущую базу и поручил создать локальный Git-репозиторий и Memory Bank в добавленном проекте `memecoin-ai`. Реализацию явно отложил до своего подтверждения.

Выбран Markdown + перекрёстные ссылки + стабильные ID + Git. GraphRAG отложен как неоправданная сложность для небольшого корпуса. Созданы цель, архитектура, решения, эксперименты, требования к данным/исполнению, evaluation/retraining, roadmap, status, история и references. Содержание оформлено как самостоятельный контекст для новых сессий.

Проверены 13 Markdown-документов: непустое содержимое, закрытие code fences, все внутренние файловые ссылки, последовательность D01–D17 и E01–E14. Ошибок не найдено. Локальный Git инициализирован с веткой `main`; remote не создавался. Проверены исключения секретов, данных и тяжёлых результатов и отсутствие whitespace-ошибок в Git diff. Историю snapshot и hash коммитов читать непосредственно из Git. Код торговой системы не создавался.

## 2026-10-01 Начало разрешённого архитектурного прохода

Пользователь разрешил автономную реализацию фундамента и push в существующий origin/main. Прежняя пауза до подтверждения снята. Выбран Python 3.11+ со stdlib в runtime и pytest/ruff/mypy для проверок. Первые компоненты: неизменяемые события с observed/assumed/unknown availability, SQLite журнал, content-verified bundles и point-in-time observations. SQLite — локальный reference store, не решение для полного потока Solana.

## 2026-10-01 Reference replay и бухгалтерия

Реализованы две временные линии, immutable observations, integer CPMM reference, reservations/settlement, отдельный risk manager, target-to-intent conversion, seeded synthetic outcomes и консервативная ликвидационная оценка. Реальный origin и неизвестная availability отклоняются. Выход за impact-domain делает весь replay недействительным, не превращается в удобную simulated failure. Точные ограничения — `docs/contracts.md`. Это проверка инфраструктуры, не E01–E14 и не калибровка Solana.

## 2026-10-01 Research contracts и воспроизводимый запуск

Добавлены отдельные Features/Target, зрелость labels, purged walk-forward, train-only standardizer, unconditional empirical forecaster и pinball score, block bootstrap, promotion blockers. CLI связывает fixture → bundle audit → reference replay → run manifest/metrics/checksums. Отрицательный synthetic round-trip используется как инженерная проверка; прибыльность не выводится. Backbone, neural training и действительная разметка рынка не выбирались.

## 2026-10-01 Ограниченная проверка RPC

Добавлен read-only RPC client с allowlist, timeout/size limits, raw receipts и редактированием чувствительных endpoint errors. Публичный mainnet endpoint ответил на getVersion/getSlot/getFirstAvailableBlock; локальные raw captures вне Git. Это подтверждает только связность: market_dataset_qualified=false, historical arrivals неизвестны. Qualified pool decoder, реконструкция account state и live/history audit остаются следующей практической зависимостью.

## 2026-10-01 Аудит и завершение локального фундамента

Добавлены causal sampling/label reference, lock-файл dev tools, Make targets и CI definition. Аудит исправил disappearance-during-latency, immutable run finalization, source snapshot provenance, strict schema/duplicate JSON checks и конфликт одинаковых pool versions. Проверены 45 тестов, strict mypy, Ruff, CLI smoke с идентичными повторными результатами, sdist/wheel build. RPC проверка реальная, market qualification не выполнена. Документы синхронизированы с кодом; добавлены D18–D21. Это инженерные результаты, не доказательство торгового edge.

## 2026-10-01 Квалификация реального CPMM adapter

Реализованы pinned layout decoder, единый finalized account checkpoint, bounded signature/
transaction audit, offline verification и сверка vault transfers с pre/post balances.
Добавлены компактные реальные regression captures. Первые RPC запросы с max version 0
пропускали v1; после проверки официальных docs добавлено чтение v1. Legacy-only domain
оказался узким: добавлен структурный Token-2022 inventory, но исполнение extensions не
объявлено поддержанным. SOL/USDC используется только как контрольный pool, не как memecoin
research segment. Состав и ограничения проверок зафиксированы в docs/source-audit.md.

Финальный аудит: 67 тестов, Ruff/strict mypy, repeatable synthetic smoke, offline real
checkpoint/inventory/flow reproduction и sdist/wheel сборка. В новые qualification specs
добавлены hashes локальной реализации decoder; старым captures они не приписываются задним
числом. Исторический coverage gate остаётся открытым, исполнение не объявлено калиброванным.

## 2026-10-01 Durable forward collection и восстановление

Пользователь утвердил ограниченный непрерывный сбор и сверку между snapshots. Добавлены
fixed-cohort polling, SQLite/WAL raw journal, атомарные pagination cursors, pending transaction/
block jobs, source-error history и возобновление после перезапуска. Аудит использует реальный
порядок getBlock и проверяет каждый pre-balance плюс конечные vault residuals. Неподдержанные
переходы и отсутствие checkpoint остаются blockers. Synthetic-only execution gate сохранён.

Практическая корректировка: page size 5 слишком затратен для активного пула после паузы;
добавлен регулируемый размер до 100 и reuse идентичных finalized pages между интервалами.
Найдена и исправлена ошибка передачи recovery параметров CLI; отдельный тест проверяет
фактические запросы. Прежние receipts/ошибки не переписаны. HTTP status теперь можно записать
без endpoint/credential; старым ошибкам неизвестный статус не приписывается.

## 2026-10-01 Результат реального forward pilot

10 checkpoints / 8 интервалов / 189 транзакций (46 successful, 143 failed), 15 blocks.
После дозагрузки нет pending scans/transactions/blocks; все промежуточные pre-balances
согласованы, terminal residuals равны нулю. Сохранены gap-open и recovered evidence, 56
issue records, включая HTTP 429; старые ошибки не удалены. Архив воспроизведён без сети
двумя установками; повторное recovery не делает запросов. Пройдены 82 теста и проверки
сборки. Рыночная полнота, full pool state, execution calibration и edge не объявлены.

## 2026-10-01 Протокольная реконструкция CPMM и quote checks

Добавлены отдельная integer venue math и CLI audit-protocol. 44 mainnet swap совпали
с events/transfers до atomic unit; fee counters и internal LP supply совпали между
checkpoints во всех 8 интервалах. 143 failures применяются атомарно, два успешных маршрута
не считаются swaps. Строгое предположение «ровно 13 swap accounts» скорректировано: pinned
handler допускает неиспользуемые trailing accounts; обнаруженный no-op router допустим
по ownership/CPI правилам. Raw accounts и старые reports не переписаны.

Source-derived liquidity/collection/creator math покрыта тестами; реального подтверждения
этих переходов пока нет. Дополнительная выборка 24 tx и четыре pool reads показали
Token-2022 у всех creator-fee кандидатов. Это основание для следующего ограниченного
extension этапа, а не для снятия strict gate сейчас. 126 тестов, smoke, build и одинаковые
аудиты из dev/wheel прошли. Memory Bank сохраняет различие evidence и предпосылок.
