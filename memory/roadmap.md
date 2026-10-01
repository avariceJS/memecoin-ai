# Roadmap с критериями перехода

R0 завершён и принят. Пользователь отдельно разрешил архитектурный проход поперёк будущих этапов: reference-компоненты реализованы, но рыночные gates R2–R8 этим не пройдены. Календарные сроки не установлены: они зависят от источника, качества истории и benchmark.

| Этап | Результат | Критерий перехода |
|---|---|---|
| R0 Memory Bank | Локальный Git, AGENTS, канонические документы | Завершено: пользователь принял базу и разрешил разработку |
| R1 Research specification | Сегмент-кандидаты, цели, капитал/units, risk budget, provenance и план оценки | Неизвестные явно разрешены или оформлены как исследовательские допущения |
| R2 Аудит и сбор данных | Исторический и live sample, manifest, timestamps, births/deaths, quality report | Доступность и ограничения понятны; данные пригодны выбранным горизонтам |
| R3 Ledger и минимальный replay | Один достоверный механизм исполнения, проверки балансов/fills/failures | Ошибки оценены; нет очевидной искусственной прибыли |
| R4 Baselines и первая supervised-модель | Cash, простые стратегии, linear/boosting, небольшой temporal encoder | Есть честная оценка наличия/отсутствия сигнала после издержек |
| R5 Контролируемые архитектурные опыты | Backbone, data format, size, targets, scanner и interactions | Выбор на development periods, без подбора на финальном тесте |
| R6 Портфель и последовательность | Sizing, action-space comparison, MPC | Улучшение сверх фиксированного sizing при одинаковом риске |
| R7 Зафиксированная система и проверка | Walk-forward, stress, uncertainty, закрытый тест | Выполнены заранее заданные критерии либо честный отказ/пересмотр |
| R8 Forward paper | Живой путь данных/решений, shadow comparison, execution residuals | Достаточное независимое наблюдение, объяснены расхождения с replay |
| R9 Необязательные V2 исследования | Graph, joint learning, long memory, RL, расширение сегмента | Каждый блок доказал пользу относительно сильной предыдущей версии |
| R10 Потенциальный live | Отдельный проектный этап | Только новое явное разрешение и конкретные ограничения; сейчас не планируется к запуску |

В R2 можно проводить описательный анализ сегментов. В R4/R5 ранний небольшой эксперимент не требует завершения всей сложной архитектуры. Для режима, где impact велик, R3 может выявить необходимость сузить область применимости до продолжения.

## Состав MVP

- Research specification и версии данных.
- Широкое наблюдение в рамках доступного качественного источника.
- Узкий подтверждённый execution domain, point-in-time features и ledger.
- Обязательные простые baselines.
- Минимальное сравнение небольших temporal моделей, без фиксации TCN заранее.
- Распределительные targets, проверяемый scanner, фиксированный затем ограниченный sizing.
- Независимый риск, временная оценка, журнал всех попыток.
- Сильный sequential baseline по мере появления необходимости и forward paper после offline gates.

MVP не обязан включать все 14 экспериментов сразу. Его задача — получить достоверную оценку edge и основных ограничений. При отсутствии сигнала остановиться на исследовании причин.

## Вторая версия

Point-in-time graph features, дополнительные execution mechanisms, pooling/attention, calibration/uncertainty, partial joint training, MPC с terminal value, champion/challenger retraining. RL только при условиях из [evaluation.md](evaluation.md).

## Отложено без автоматического обязательства

Большие foundation models, полный temporal GNN, full all-token attention, MoE, distributed training двух Mac, субсекундная торговля без подходящей инфраструктуры, социальные сигналы без качественной истории. Их можно рассмотреть при новых доказательствах.

## Недопустимые исследовательские практики

Будущие данные в pretraining/features, survivorship selection, прибыль по неисполнимой цене, исключение катастрофических исходов ради метрик, оптимизация на финальном тесте, обход risk limits, автоматическое promotion по максимальному PnL, обещание гарантированного улучшения от continual learning.

## Обновление R2 — bounded audit

Первый диагностический Raydium CPMM adapter проверен на реальных данных; CLI и raw
regression captures реализованы. R2 market gate не пройден: coverage, births/deaths,
полная реконструкция исторических состояний и независимая сверка остаются открытыми.
Следующий practically scoped pilot — forward collection с gap recovery и backfill
reconciliation. Цифры и отказы не дублируются здесь: см. docs/source-audit.md.

## Обновление R2 — forward pilot

Ограниченный fixed-cohort сбор и recovery проверены: интервальная реконструкция classic
SPL vault amounts совпала с endpoints. Это не полное protocol state и не passing всего R2.
Следующий gate — protocol transitions/fee counters и независимая полнота источника.
Измеренные результаты и ограничения: docs/forward-pilot.md.

## Обновление R2 — экономические переходы CPMM

Conditional quote math, fee counters и internal LP supply проверены на classic-SPL
swap интервалах. Это частичное продвижение R2/R3, не завершение source/execution gates.
Следующий этап: Token-2022 whitelist semantics и mainnet coverage creator/liquidity/
collection; config-history и независимая полнота источника остаются зависимостями.
Факты: docs/protocol-audit.md; текущий статус: state.md.

## Обновление R2 — ограниченный Token-2022

Allowlist, transfer fees, creator fees и harvest-to-mint проверены на новом bounded
mainnet pilot. Полные config/mint history, epoch boundaries, редкие liquidity/collection
переходы и независимая source completeness остаются открытыми. Следующий сбор следует
ориентировать на эти операции; масштабирование public RPC не доказано. См. docs/token2022.md.

## Обновление R2 — история параметров

D28–D30 реализованы; source completeness всё ещё условна. Новые bounded интервалы
проверяют зависимости, а LP witnesses — арифметику, не полноценные historical anchors.
Следующий практический gate: синхронный ограниченный сбор с независимой сверкой
провайдера, измерение задержек/объёма и targeted rare-operation coverage. До этого
не переходить к ML/backtest claims. Детали — docs/parameter-history.md.
