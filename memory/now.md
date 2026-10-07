# Краткий контекст проекта

## Актуально на 2026-10-07

- SSH172.20.10.11 восстановлен. Data-only20mint roster sealed; фактически9/40GET,
  411bars/38origins/2mints/3disjointwindows: broad benchmark НЕ готов. STOP01source timeout,
  STOP02unknownconsumer без identity; третьего запуска нет. [Отчёт/clipping](../docs/brain-development-acquisition-2026-10-07.md).
  Ownerrevision33paused,champion/runtime08 unchanged; adapter_unavailable, expiry0вUI
  не доказанныйsourceexpiry. Managedexam до12:23:51UTC не раскрывать дляdevelopment.
  Clip100%confirmed, causalharmunknown; новыхfits/oldtestscore0. СледующийMLtrial послеdata.

- Создан [benchmark-v1 contract](../docs/brain-benchmark-v1.md), config/exposure registry и
  metadata preflight с tests. Широких development/calibration/prospective packets пока НЕТ:
  support_ready=false; это не готовый live benchmark. Protected/oldtest не переиспользовать.
- Train-only LR diagnostic8continuations:0.001->0.0001 уменьшил mediancoverage SD на55–58%
  meanseeds и улучшилtraincalibration во всех4pairs. Loss+2.29%current/-1.55%capped;
  futurequality не проверялось, evalpredictions0. Причинная поддержка optimizationinstability,
  не новыйdefaultLR. [Результат](../docs/brain-lr-diagnostic-2026-10-07.md).

- Horizon-head causal ablation завершён:2samplers ×2heads ×seeds7/19,4000steps;
  zero-init48parameter horizon/latent interaction,8fits37.4s,controls weights exact,
  144metricgroups independently exact. Terminal ensemble head test effect -3.93% current,
  +1.08% capped; median calibration не исправлена (82.72% current; capped5m38.62/15m67.89%).
  Structural restriction реальна, но главный остаточный defect head не установлен.
  Research target разделён: actual eligible AUTO origins и отдельная Watch breadth/traffic
  оценка по age/seen/horizon. Admission old/new != market age;16.64%/4000 не новый recipe.
  Следующий приоритет — old+new dev/prospective eval contract до дальнейшего tuning;
  LR/convergence и separate calibration block ещё не проверены. Production/champion/AUTO
  unchanged; owner revision33 paused(reason expired). [Результат](../docs/brain-horizon-ablation-2026-10-07.md).

- Sampling и finite budget causally влияют на loss данного snapshot: combined test -20.76%,
  calibration не квалифицирована;16.64%/4000 не production recipe. Retained rows оставались48000.
  [Sampling](../docs/brain-sampling-ablation-2026-10-07.md),
  [budget](../docs/brain-budget-ablation-2026-10-07.md).
- Historical ~5/~9/~42% не temporal degradation trend: разные sets/baselines; z1425 старый,
  не latest test. [Независимый audit](../docs/ml-review-audit-2026-10-07.md).
- Latest AUTO job5edd выбралGRU, но не promoted; matched historical test хужеchampion41.95%.
  Champion empirical SHA9ea4a6bd...3920 unchanged. Owner latest read-only revision33,
  paused/paused(reason expired),queued/running0; SSH172.20.10.11 с прежним host key.
  Не возобновлять production; release gates требуют новой проверки фактического состояния.
  [Post-training audit](../docs/brain-post-training-audit-2026-10-07.md).
- Watch feature commit0e9a2c1 не установлен: активенruntime08, runtime09эквивалентен08.
  Планируемый release10 должен содержатьWatch и pin official champion; live acceptance
  не проведён. [Контракт/статус](../docs/watch-market.md). История expiries — memory/state.md.

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
- 2026-10-06: реализован отдельный Watch Market single-pool lane в Control Center на
  GeckoTerminal Public API; synthetic tests покрывают resolve→forecast→B07 paper ledger.
  Не выкладывался на Mac1 и не проходил provider/live/browser acceptance: нужен конкретный
  mint/pool пользователя и отдельное безопасное owner-host release window. Подробности:
  [Watch Market](../docs/watch-market.md). AUTO/runtime не менялись.
- Последний runtime commit: `d3024b6` (sleep/wake recovery), context entrypoint: `88526bf`. В
  сессии Mac 2 2026-10-06 SSH к `root1@192.168.31.66` прошёл с проверенным host fingerprint;
  Read-only `/api/status`: `operator-status-v1`, `auto/waiting`, `domain_stop=false`;
  TCP listener, LaunchAgent `ai.memecoin.brain.operator` и runtime process совпали
  (PID 64937, pinned `runtime08`). Owner prepare/bind commit `669d4d5` прошли: 980 passed/12
  skipped; staged `runtime09` имеет тот же runtime pin. Activation заблокирована: одна managed
  exam действует до `2026-10-06 21:14:49 UTC`, operator `auto/waiting`; нужен expiry и штатная
  Pause через UI.

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
  Python3.13/dev `.venv` установлены; системный python3 остаётся3.9. SSH Mac2→Mac1 проверен;
  synthetic report принят на Mac1 и возвращён на Mac2 с совпадающими manifest/member hash.
  Mac1 сохраняет live source/AUTO/registry/paper и короткий training; read-only operator health
  проверен, production release на Mac1 не переключали.
- Cursor CLI2026.10.01-e373342 установлен, требует `~/.local/bin/agent login`. Каталог/Grok/
  Kimi/GLM/DeepSeek не подтверждены; Cursor MCP list пуст. Внешний сильный MCP-Qwen отложен.
  Отдельный Codex worker исправил зависимость UI-тестов от Mac1 work/; не считать его Grok.
- Local Qwen — отдельный бесплатный preprocessing ресурс, НЕ замена сильного reviewer.
  `qwen3.5:35b-a3b-q8_0` проверена: Q8_0/36.0B, disk38.7GB, context8192,
  ≈47.1tok/s. Первый review обрезан/ошибочен и не принят; swap5.3→14.2GiB, free88→7%.
  Модель затем выгружена. Unattended Q8 не квалифицирована, heavy training не запускался.
- Добавлены sealed artifact CLI и runtime prepare/bind/activate/rollback CLI. Artifact transfer
  прошёл synthetic two-host round trip; release bind/rollback проверены только на synthetic
  root. UI update action отсутствует. Rollback сохраняет новые events; активация требует
  паузы, no jobs/no unexpired managed exams.
- `local_evidence_queue.py`: deterministic grouping curated packets с полными исходниками/
  STOP и необязательной LLM annotation. Shared compute lease, context4096, output256,
  keep_alive0; нынешняя Q8 отложена по RAM gate. Нет 24h service или реального log exporter.
  Market features не admitted, эксперимент не проведён; billed-token savings не измерены.
- Разрешённые отдельные provider budgets можно учитывать после проверки account-wide/IP
  ограничений и plan. AlchemyTerms2.2(x) запрещает spread usage across accounts; второйMac/ключ
  не автоматически новая quota. Диапазоны раздельные, budgets/reservations сохраняются.
  [Архитектура/переход/метрики](../docs/two-mac-workflow.md), `tools/worker_preflight.py`.
- [Актуальные команды/ограничения](../docs/two-mac-operations.md). Следующий шаг: отдельная
  bounded owner-host release rehearsal с prepare/bind/paused activate/health/rollback;
  release qualification остаётся на owner; новых historical campaigns пока нет.
- Exact code `ab8c1a7`:978tests passed/12optional skipped,ruff/mypy/smoke PASS;
  [acceptance/делегирование](../docs/evidence/two-mac-worker-2026-10-06.json).

## Где читать первоисточник

- Оператор и восстановление: `docs/brain-operator-manual.md`, `docs/brain-autonomous-operator.md`.
- Текущие training данные/результаты: `docs/brain-training-expansion-results.md` и связанный JSON в `docs/evidence/`.
- Научная оценка: `memory/evaluation.md`; source/data semantics: `memory/data-and-execution.md`.
- Решения: верхние записи `memory/decisions.md`; исторический поиск — `rg` или context helper.
- B09/B10/B12, frozen exams и expiries: открыть конкретные документы из `memory/README.md` только при затрагивании этих объектов.
