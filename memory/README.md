# Карта памяти проекта

Реализация и команды: [корневой README](../README.md), точные ограничения: [контракты](../docs/contracts.md), доступ к источникам: [аудит](../docs/source-audit.md).

Memory Bank — канонический, пригодный для чтения человеком и агентом контекст проекта. Он фиксирует смысл решений и неопределённость, а не только список будущих компонентов. Создан 2026-10-01 на основе двух архитектурных reviews и подтверждения пользователем последней концепции как текущей базы.

## Минимальный маршрут

Новая сессия: [краткий актуальный контекст](now.md) → Git/runtime → `python3 tools/agent_context.py --task ...` → первоисточники только по вопросу. Полная историческая память ниже — поиск по нужному разделу, не обязательное чтение целиком. Это не заменяет инструкции [AGENTS.md](../AGENTS.md).

| Вопрос новой сессии | Канонический документ |
|---|---|
| Краткий актуальный статус, hard boundaries и роли моделей/узлов | [now.md](now.md) |
| Ограниченный пакет контекста по задаче | `python3 tools/agent_context.py --task operator|data|ml|architecture|review|docs` |
| B12: matched curriculum, frozen models и отдельный future paper exam | [brain-curriculum-comparison.md](../docs/brain-curriculum-comparison.md) |
| B11: archive-first, phase policy, curriculum audit и2025raw qualification | [brain-archive-curriculum.md](../docs/brain-archive-curriculum.md) |
| B10: operational incident, exact recovery, daily candidate publication | [brain-operational-recovery.md](../docs/brain-operational-recovery.md) |
| B09: hot/background cadence, restored paper, 72h rollout и soak | [brain-hot-lane.md](../docs/brain-hot-lane.md) |
| B08: incremental corpus, provider outage, отклонённая weekly activation | [brain-continuity.md](../docs/brain-continuity.md) |
| Зачем строим и чего не обещаем | [project.md](project.md) |
| Как устроены компоненты и их границы | [architecture.md](architecture.md) |
| Что принято, почему и когда пересмотреть | [decisions.md](decisions.md) |
| Как объективно выбрать спорный вариант | [research.md](research.md) |
| Какие данные и исполнение необходимы | [data-and-execution.md](data-and-execution.md) |
| Как воспроизвести economic dataset и quote probes | [economic-dataset.md](../docs/economic-dataset.md) |
| Causal observations, masks и prospective corpus | [causal-forward.md](../docs/causal-forward.md) |
| Возобновляемые сегменты и execution-conditioned targets | [prospective-campaign.md](../docs/prospective-campaign.md) |
| Checkpoint coherence, protocol support и corpus quality | [checkpoint-coherence.md](../docs/checkpoint-coherence.md) |
| Новый AmmConfig creator fee share и snapshot-only профиль | [creator-fee-share.md](../docs/creator-fee-share.md) |
| Доступность RPC, persistent TLS и target coverage | [persistent-source.md](../docs/persistent-source.md) |
| Alchemy HTTP/WSS, handoff, gaps и отрицательный v1 результат | [alchemy-pilot.md](../docs/alchemy-pilot.md) |
| Matched cadence, coalescing и решение WSS off | [alchemy-pilot-v2.md](../docs/alchemy-pilot-v2.md) |
| D41: prospective HTTP, connect timeout и censored QA | [qualified-http-corpus.md](../docs/qualified-http-corpus.md) |
| Cold diagnosis, календарные окна и execution uncertainty D42 | [cold-connection-corpus.md](../docs/cold-connection-corpus.md) |
| Активное 48h расписание, управление и реальные execution observations D43 | [calendar-execution.md](../docs/calendar-execution.md) |
| Optional ML/training, causal bundles, encoders, checkpoint/resume и M4 benchmarks D44 | [ml-foundation.md](../docs/ml-foundation.md) |
| D45 registry: подключение sealed окон, global folds, qualification и reserved holdout | [corpus-registry.md](../docs/corpus-registry.md) |
| D46 F01: research specification, readiness, selection budget и launch/release границы | [research-readiness.md](../docs/research-readiness.md) |
| D47 frozen runner, trial/exposure ledger, scoring/bootstrap и conformance | [f01-runner.md](../docs/f01-runner.md) |
| D48 future sampling/source binding, sealed adapter и readiness dossier | [f01-sampling.md](../docs/f01-sampling.md) |
| D49 source/budget/scheduler qualification и закрытый activation gate | [f01-collector-qualification.md](../docs/f01-collector-qualification.md) |
| D50 subprocess supervisor, frozen runtime, budget receipts и staged service | [f01-operational-runtime.md](../docs/f01-operational-runtime.md) |
| D51 owner review,180RPC live PASS, offline replay и calendar operating conditions | [f01-live-qualification.md](../docs/f01-live-qualification.md) |
| B01: независимый Brain Development, history, supervised training и continuous shadow | [brain-development.md](../docs/brain-development.md) |
| B02: data coverage, learning curves, matured shadow и остановленный Brain source | [brain-data-scale.md](../docs/brain-data-scale.md) |
| B03: pool quarantine, расширенная история, learning curves и отрицательная activation | [brain-pool-quality.md](../docs/brain-pool-quality.md) |
| B04: source availability, normalization ablation и активный collector-only job | [brain-availability-normalization.md](../docs/brain-availability-normalization.md) |
| B05: source architecture, Alchemy raw evidence, capacity и bounded reliability audit | [brain-source-architecture.md](../docs/brain-source-architecture.md) |
| B06: PumpSwap trace reconciliation, ABI drift, range journal и indexed STOP | [brain-pumpswap-events.md](../docs/brain-pumpswap-events.md) |
| B07: live virtual paper loop, candidate refresh и Control Center | [brain-paper-loop.md](../docs/brain-paper-loop.md) |
| Как проверять edge и выпускать новую модель | [evaluation.md](evaluation.md) |
| В каком порядке работать | [roadmap.md](roadmap.md) |
| Что уже существует и что дальше | [state.md](state.md) |
| Что менялось по сравнению с прежней концепцией | [changes.md](changes.md) |
| На что опиралось обсуждение | [references.md](references.md) |

## Автономный оператор B13

- [Текущий machine/runtime статус](state.md) — читать первым.
- [Operator manual](../docs/brain-operator-manual.md) — кнопки, bounds, STOP и restart.
- [Протокол/архитектура](../docs/brain-autonomous-operator.md).
- [Acceptance/evidence](../docs/brain-b13-acceptance.md), [история и отчёты](../docs/brain-operator-evidence.md).
- [Forensic первых суток](../docs/brain-experience-review.md), [historical vacuum](../docs/brain-vacuum-results.md).
- [Actual training expansion 24 → 86](../docs/brain-training-expansion-results.md),
  [зарегистрированный протокол](../docs/brain-training-expansion.md).

## Типы утверждений

- **Принято:** пользователь принял принцип или процесс как базу. Это может быть изменено новой аргументацией и решением.
- **Стартовая гипотеза:** подход для первого сравнения; его превосходство не установлено.
- **Определить экспериментом:** нет окончательного выбора, есть план проверки.
- **Отложено:** не входит в текущий этап, но не запрещено навсегда.
- **Заменено:** прежняя рекомендация больше не является текущим выбором; причина остаётся в истории.
- **Факт состояния:** проверяемое действие или результат, реально состоявшийся в проекте.

Статус решения и уверенность не тождественны. Пользователь принял исследовательскую базу, включая необходимость сравнивать неопределённые решения; он не подтвердил превосходство каждой гипотезы.

## Почему без GraphRAG

Сейчас корпус небольшой, структурированный и часто меняющийся. Markdown, ссылки, ID `Dxx` и `Exx`, текстовый поиск и Git дают прозрачный контекст без отдельной базы, embeddings и синхронизации индекса. Это лёгкая связанная документация, не GraphRAG.

Вернуться к retrieval-инфраструктуре можно, если накопятся сотни отчётов и источников, поиск регулярно будет пропускать существенные связи, а обычный индекс окажется недостаточным. Сначала измерить качество поиска; затем сравнить текстовый поиск, embedding retrieval и графовый retrieval. Даже тогда канонические решения остаются в version-controlled документах, а индекс должен быть воспроизводимым производным артефактом.

**Не путать:** граф кошельков и deployer — потенциальная входная модальность торговой модели. Его необходимость не означает необходимости GraphRAG для Memory Bank.

## Правила поддержания

Каждый факт хранить прежде всего в одном тематическом документе, из остальных ссылаться. В `state.md` держать компактный актуальный срез; в `now.md` — точку входа и карту ролей, обновлять при изменении текущих фактов. `changes.md` добавляет историю; не выдавать реконструкцию обсуждения за историю проведённых экспериментов.

Для нового решения указывать контекст, статус, выбор, причину, слабость, альтернативу и условие пересмотра. Для эксперимента — гипотезу, контроль, меняемый фактор, метрики и статус. Результаты будущих запусков хранить в отдельных кратких отчётах с provenance и ссылками из реестра.

Изменения документации отслеживаются локальным Git. GraphRAG, сервисы, плагины, автоматизации и специальные инструменты памяти не установлены и не требуются.
