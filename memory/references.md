# Источники и границы применимости

Список перенесён из архитектурного обсуждения при создании Memory Bank 2026-10-01. Источники поддерживают свойства методов, а не наличие edge на Solana memecoins. Это не систематический обзор литературы. При реализации повторно проверять актуальную документацию, версии библиотек, протоколов и поддерживаемые платформы.

| Источник | Что использовано | Чего он не доказывает |
|---|---|---|
| [Bai et al. TCN](https://arxiv.org/abs/1803.01271) | Causal convolutions как сильный общий baseline последовательностей | Превосходство TCN в нашем рынке |
| [Gu and Dao Mamba](https://arxiv.org/abs/2312.00752) | Selective state spaces, длинные последовательности и структурная эффективность | Заявленные скорости на Apple M4 или торговый edge |
| [Официальная реализация Mamba](https://github.com/state-spaces/mamba) | Проверка требований kernels и платформ | Автоматическую совместимость всех операций с MPS |
| [PyTorch MPS](https://docs.pytorch.org/docs/main/notes/mps.html) | GPU backend Apple | Предсказанное время обучения конкретной модели |
| [Apple PyTorch](https://developer.apple.com/metal/pytorch/) | Поддержка PyTorch на Mac | Сравнительное качество архитектур |
| [Lee et al. Set Transformer](https://arxiv.org/abs/1810.00825) | Attention для множеств, inducing points | Прибыльность cross-token interactions |
| [Boyd et al. Multi-Period Trading](https://stanford.edu/~boyd/papers/cvx_portfolio.html) | Многопериодное планирование с исполнением первого шага | Точный оптимум или корректную модель memecoin market |
| [Schulman et al. PPO](https://arxiv.org/abs/1707.06347) | Семейство policy-gradient методов для interactive training | Превосходство PPO над MPC в нашем проекте |
| [Kostrikov et al. IQL](https://arxiv.org/abs/2110.06169) | Offline RL и ограничение обращения к unseen actions | Достаточное action coverage обычной истории цен |
| [Kumar et al. CQL](https://arxiv.org/abs/2006.04779) | Консервативная оценка offline RL | Исправление ошибочного симулятора |
| [Agrawal et al. Differentiable Convex Optimization Layers](https://arxiv.org/abs/1910.12430) | Совместное обучение через подходящие оптимизационные слои | Гладкость реальных fills, failures и routing |
| [Rossi et al. Temporal Graph Networks](https://arxiv.org/abs/2006.10637) | Обработка динамических графов событий | Качество inferred ownership или прибыльность кошельковых графов |
| [Ansari et al. Chronos](https://arxiv.org/abs/2403.07815) | Пример pretrained time-series model | Перенос на исполнимые доходности мемкоинов |
| [Bailey and López de Prado Deflated Sharpe](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Учёт selection bias и non-normality в оценке Sharpe | Гарантированное отсутствие overfitting или будущую прибыль |
| [Solana fees](https://solana.com/docs/core/fees/fee-structure) | Семантика fees и charged failures | Неизменность тарифов и сетевых условий |
| [Solana token extensions](https://solana.com/docs/tokens/extensions) | Transfer restrictions, fees и hooks | Поддержку каждого токена выбранным маршрутом |
| [Raydium implementations](https://github.com/raydium-io) | Разные механизмы пулов и первичные реализации | Корректность нашего ещё не созданного simulator |

## Первичный источник проектных решений

Два review в исходной пользовательской беседе и последующее принятие последней концепции как базы. Существенное содержание сохранено в документах; доступ к беседе не требуется для продолжения. Различие между исходными рекомендациями и текущим выбором сохранено в [changes.md](changes.md). Все численные архитектурные диапазоны и сравнения являются предложениями исследования, а не опубликованными результатами проекта.

## История параметров (проверено 2026-10-02)

- [Solana getEpochSchedule](https://solana.com/docs/rpc/http/getepochschedule) — поля
  captured schedule; конкретные mainnet значения взяты из raw response, не примера docs.
- [Solana getBlocks](https://solana.com/docs/rpc/http/getblocks) — inclusive finalized
  range; полнота возвращённого списка не является независимой source-проверкой.
- [Pinned Raydium update_config.rs](https://github.com/raydium-io/raydium-cp-swap/blob/59fb845a9e5bb569c8b2f3415f13b0c0ebcc6b92/programs/cp-swap/src/instructions/admin/update_config.rs)
  — параметры 0–7. Binary equivalence mainnet всё ещё не доказана.
- [Pinned Token-2022 transfer fee processor](https://github.com/solana-program/token-2022/blob/5bc7ea3b9568b9fdac2d08c921d73d40260249c7/program/src/extension/transfer_fee/processor.rs)
  — old/new schedule и epoch+2; реальные тарифные updates в новой выборке не наблюдались.
