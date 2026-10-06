# Краткий контекст проекта

Это входная сводка, а не замена первичным документам. Проверяйте Git и фактический
runtime перед изменениями: данные о процессах и сроках быстро устаревают.

## Текущее направление

- Brain — exploratory supervised collect → sealed snapshot → bounded training → frozen
  candidate → future paper. Paper PnL отдельно от forecast scoring; онлайн-обучения после
  сделок нет. Автоматический champion promotion запрещён.
- На 2026-10-06 corpus 24 → 86 train pools был включён в AUTO через pinned immutable
  revision. 8 matched fits: расширенный GRU хуже control на общем historical test на 8.92%.
  Это отрицательный результат, не основание менять test, recipe или champion.
- AUTO остаётся bounded: 24h и ≥100 новых matured outcomes, ≤6 reservations за 7 дней;
  текущий operational state всегда считывать с машины, не выводить из этой заметки.
- Последний runtime commit: `d3024b6` (sleep/wake recovery), context entrypoint: `88526bf`. Сверить
  `git status`, HEAD, LaunchAgents, source health, expiry и incidents на Mac 1. В сессии
  Mac 2 от 2026-10-06 production state не считывался: SSH host пока неизвестен.

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
- Development теперь на Mac 2: `/Users/sl-aleksandr/Developer/memecoin-worker`, measured
  M4 Pro/48GiB, bootstrap checksums совпали, base fast-forward `eee7ce1`→`a594474`.
  Python3.13/dev `.venv` установлены; системный python3 остаётся3.9. Mac1 сохраняет live
  source/AUTO/registry/paper и короткий training; нет SSH host, связи или production cutover.
- Cursor CLI2026.10.01-e373342 установлен, требует `~/.local/bin/agent login`. Каталог/Grok/
  Kimi/GLM/DeepSeek не подтверждены; Cursor MCP list пуст. Внешний сильный MCP-Qwen отложен.
  Отдельный Codex worker исправил зависимость UI-тестов от Mac1 work/; не считать его Grok.
- Local Qwen — отдельный бесплатный preprocessing ресурс, НЕ замена сильного reviewer.
  `qwen3.5:35b-a3b-q8_0` проверена: Q8_0/36.0B, disk38.7GB, context8192,
  ≈47.1tok/s. Первый review обрезан/ошибочен и не принят; swap5.3→14.2GiB, free88→7%.
  Модель затем выгружена. Unattended Q8 не квалифицирована, heavy training не запускался.
- Добавлены sealed artifact CLI и runtime prepare/bind/activate/rollback CLI; пока local
  synthetic acceptance, не two-host qualification. UI update action отсутствует. Rollback
  сохраняет новые events; активация требует паузы, no jobs/no unexpired managed exams.
- `local_evidence_queue.py`: deterministic grouping curated packets с полными исходниками/
  STOP и необязательной LLM annotation. Shared compute lease, context4096, output256,
  keep_alive0; нынешняя Q8 отложена по RAM gate. Нет 24h service или реального log exporter.
  Market features не admitted, эксперимент не проведён; billed-token savings не измерены.
- Разрешённые отдельные provider budgets можно учитывать после проверки account-wide/IP
  ограничений и plan. AlchemyTerms2.2(x) запрещает spread usage across accounts; второйMac/ключ
  не автоматически новая quota. Диапазоны раздельные, budgets/reservations сохраняются.
  [Архитектура/переход/метрики](../docs/two-mac-workflow.md), `tools/worker_preflight.py`.
- [Актуальные команды/ограничения](../docs/two-mac-operations.md). Следующий шаг: SSH
  `user@host` Mac1 + host-key verification, synthetic two-host probe, readonly health,
  квалификация release на owner. Без подключения продолжать scoped engineering наMac2.
- Exact code `ab8c1a7`:978tests passed/12optional skipped,ruff/mypy/smoke PASS;
  [acceptance/делегирование](../docs/evidence/two-mac-worker-2026-10-06.json).

## Где читать первоисточник

- Оператор и восстановление: `docs/brain-operator-manual.md`, `docs/brain-autonomous-operator.md`.
- Текущие training данные/результаты: `docs/brain-training-expansion-results.md` и связанный JSON в `docs/evidence/`.
- Научная оценка: `memory/evaluation.md`; source/data semantics: `memory/data-and-execution.md`.
- Решения: верхние записи `memory/decisions.md`; исторический поиск — `rg` или context helper.
- B09/B10/B12, frozen exams и expiries: открыть конкретные документы из `memory/README.md` только при затрагивании этих объектов.
