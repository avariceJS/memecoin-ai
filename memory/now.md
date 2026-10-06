# Краткий контекст проекта

Это входная сводка, а не замена первичным документам. Проверяйте Git и фактический
runtime перед изменениями: данные о процессах и сроках быстро устаревают.

## Текущее направление

- Brain — exploratory supervised collect → sealed snapshot → bounded training → frozen
  candidate → future paper. Paper PnL отдельно от forecast scoring; онлайн-обучения после
  сделок нет. Автоматический champion promotion запрещён.
- На 2026-10-06 corpus 24 → 86 train pools был включён в AUTO через pinned immutable
  revision. 8 matched fits: расширенный GRU не улучшил общий historical test на 8.92%.
  Это отрицательный результат, не основание менять test, recipe или champion.
- AUTO остаётся bounded: 24h и ≥100 новых matured outcomes, ≤6 reservations за 7 дней;
  текущий operational state всегда считывать с машины, не выводить из этой заметки.
- Последний commit при записи сводки: `d3024b6` (sleep/wake recovery). Позже сверить
  `git status`, HEAD, LaunchAgents, source health, expiry и incidents.

## Нерушимые границы

- F01/D43 и reserved holdout не раскрывать и не менять. Историческое событие, доступность,
  train eligibility и source provenance не переопределять без отдельного решения.
- Не заявлять edge по loss/PnL; не обучаться на future test; не backdate; не переписывать
  STOP/incidents, sealed datasets, models или paper ledger. Не снимать quarantine без admission.
- Только virtual paper/read-only source. Нет реальных денег, signing, отправки транзакций,
  платных подключений или неизвестных credentials.
- AUTO может выпускать immutable candidate и запускать future exam; champion promotion
  требует отдельного review/подтверждения.

## Как загружать контекст без перечитывания истории

1. Прочитать `AGENTS.md` и эту страницу.
2. Запустить `python3 tools/agent_context.py --task <operator|data|ml|architecture|review|docs>`.
   Пакет включает только выбранные актуальные документы, git status и последние commits.
3. Для конкретного вопроса добавить `--query 'ключевые слова'`: будут приложены найденные
   строки с коротким окружением. Открывать полный отчёт/evidence только если вывод ведёт к нему.
4. `memory/state.md`, `decisions.md`, `changes.md` и reports — архивная история с текущими
   фактами вверху, не документы для повторного полного чтения. Проверять ссылки на первичный
   evidence и его hashes перед любым изменением критических семантик.

## Роли моделей и границы двух узлов

- Astra/Codex: только решения с высокой ценой ошибки — temporal/causal semantics, eligibility,
  leakage/holdout, preregistration, evaluation/model selection, promotion, risk/execution,
  архитектурные trade-offs и финальная интеграция.
- Grok/Cursor на втором Mac: при наличии отдельного checkout — bounded implementation, тесты,
  UI и docs с закреплёнными файлами. Kimi/GLM/DeepSeek: независимый critic, проверка тестового
  плана и research synthesis с первичными ссылками. Локальная Qwen: предварительная сортировка
  больших логов/diffs. В этой сессии ни одна из этих моделей не запускалась и не видит repo.
  Любой отчёт должен содержать exact paths/evidence, tests и открытые вопросы; модели не
  authority для критических семантик выше.
- Локальная Qwen на втором Mac: потенциально дешёвая предварительная сортировка больших логов,
  diff и документов с возвратом источников/строк. На 2026-10-06 endpoint и доступность второго
  Mac из этого checkout не проверены; моделью не запускалась, данных ей не передавали.
- Этот checkout находится на основном Mac 24GB; здесь доступен Codex, а остальные модели — на
  втором Mac 48GB без копии проекта. Они не имеют прямого доступа к Git/файлам/Brain. Работа
  второй модели возможна только после явной передачи ограниченного пакета/артефактов и возврата
  результата; канал пока не проверен.
- Основной Mac остаётся owner live source, canonical local store, AUTO и Control Center. Второй
  Mac — изолированный offline research/training/LLM worker после проверенной передачи sealed
  shards и hash-verified bundles. Не шарить mutable SQLite/не создавать второго writer. Сейчас
  копии checkout и измеренного канала передачи нет; training workload не запущен.

## Где читать первоисточник

- Оператор и восстановление: `docs/brain-operator-manual.md`, `docs/brain-autonomous-operator.md`.
- Текущие training данные/результаты: `docs/brain-training-expansion-results.md` и связанный JSON в `docs/evidence/`.
- Научная оценка: `memory/evaluation.md`; source/data semantics: `memory/data-and-execution.md`.
- Решения: верхние записи `memory/decisions.md`; исторический поиск — `rg` или context helper.
- B09/B10/B12, frozen exams и expiries: открыть конкретные документы из `memory/README.md` только при затрагивании этих объектов.
