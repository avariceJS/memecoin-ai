# Начать здесь: Grok 4.7 Extra High

**Не начинать реализацию до чтения пунктов1–4 и проверки Git ниже.**
Работа — независимая визуальная оболочка. Основной chat выполняет D50+; ты этого не делаешь.
Контекст восстанавливается из files, не из предыдущего чата. Язык — русский.

## 1. Правила и актуальный статус UI

| Порядок / файл | Зачем и что понять | Что не менять |
|---|---|---|
| root `AGENTS.md` | Общая causal/integrity/no-money дисциплина | Сам файл, core authorizations не расширять |
| `apps/control-center/AGENTS.md` | Твой точный write/read/network/Git scope | Не ослаблять запреты ради feature |
| `.cursor/rules/control-center.mdc` | Always-applied Cursor entrypoint | Не отключать для доступа к core |
| `apps/control-center/memory/state.md` | Что реализовано, что нет, следующий UI шаг | Историю не заменять догадкой |
| `memory/project.md`, `memory/decisions.md` внутри apps/control-center | Product scope, принятые UI решения | Не смешивать с root Memory Bank |
| UI `memory/changes.md`, `README.md` | История и reproducible commands | Старые failures не удалять |

## 1a. CC-01 — обязательное дополнение Coordinator

Сразу после UI Memory Bank прочитать UI `memory/WORK_STATE.json` и `STATUS.md`, затем
`AUTONOMOUS-WORKFLOW.md`, `AGENT-TEAM.md`, `DESIGN-SYSTEM.md`; самому открыть
`references/dashboard-concept-v1.png`. Это правила task recovery/delegation и visual contract,
не новые core semantics. State фиксирует конфигурацию модели отдельно от actual execution.
Curated core reading ниже нужен Coordinator; bounded subagent получает короткий reading
list из AGENT-TEAM и explicit safe files, не всю историю core.

## 2. Архитектура и безопасные contracts

Читать последовательно `docs/control-center/00-scope.md`, `01-architecture.md`,
`02-data-map.md`, `03-contracts.md`, `05-security-workflow.md`, `04-phases.md`, затем
`proposals/CC-P001-operational-export.md`. Они объясняют текущую границу и будущий request,
но не дают право реализовать core export самому. `06-review.md` фиксирует self-review.

После текстов прочитать `apps/control-center/contracts/types.ts`, `source-pins.json`,
`published/manifest.json`, `published/observatory-v1.json`, `adapter/{safe_io,export_snapshot,
server}.py`, `tests/test_boundary.py`, `scripts/check_scope.py`. Здесь конкретная schema,
живой JSON example и guards; никогда не follow provenance paths внутри JSON.

## 3. Curated core reading, только read-only

Корневые `memory/state.md`, `memory/project.md`, `memory/decisions.md` — обязательный core
контекст; D43–D49 latest entries особенно важны. `memory/README.md` — карта, не призыв
читать весь repo. `memory/data-and-execution.md`, `memory/evaluation.md`, `memory/research.md`
дают causal/target/holdout ограничения; никакие предлагаемые в них experiments не запускать.

| Файл / конкретная часть | Что нужно для UI | Не следует делать |
|---|---|---|
| `memory/architecture.md`: общая конструкция, implemented boundary, D44–49 additions | Engine независим; policy/risk/execution не GUI | Менять backbone/policy/risk |
| `docs/contracts.md`: data/replay/protocol restrictions | Реальные snapshots не synthetic replay | Запускать replay/training |
| `docs/calendar-execution.md`: plan, фактический результат, расписание |180corpus+12audit, stages и missing semantics | Выполнять STOP/launchctl/calendar_inventory команды |
| `docs/corpus-registry.md`: implementation, holdout/operator workflow, result | before-read protection, revision/folds/counts | Вызывать sync/audit/init/export из UI |
| `docs/ml-foundation.md`: интерфейс, actual result, ограничения | Engineering runs и parameters, CPU/MPS shapes | Импортировать Predictor/Torch/weights или тренировать |
| `docs/research-readiness.md`: scope, readiness/admission, selection budget | F01 study,114fit ceiling, counts не permission | Менять spec/thresholds или интерпретировать smoke как edge |
| `docs/f01-runner.md`: admission/exposure, actual result + addendum | Ledger metadata и globally-unseen caveat | Создавать Ledger(), читать events/raw holdout |
| `docs/f01-sampling.md`: contract, admission, dossier, actual result | Future336window plan,0realadmission | Активировать collector/менять T/собирать corpus |
| `docs/f01-collector-qualification.md`: verdict table, source/budget/binding, decision | D49 NOT_READY и historical6RPC | Выполнять D50 или открывать billing/credentials |

Не читать whole `src/` без цели. Если для отображения field нужен код, разрешены read-only
конкретные producer functions: sampling/dossier.qualify, research/framework assessment,
runner/ledger schema, ml/benchmark report. **Не импортировать/запускать их.** Data map даёт
достаточный старт; отсутствие live interface оформляется proposal.

## 4. Реальные безопасные данные

Все9inputs `contracts/source-pins.json` реально существуют **в этом worktree** и содержат
committed historical evidence/design. Это files из docs/evidence иconfigs; exact hashes
проверяются exporter. Их минимальная readable projection уже есть вpublished/.
`work/` отсутствует вisolatedcheckout — не проблема, не создавать ссылку наmainworktree.
Не открывать D43/holdout/raw ради проверки UI. Sources поумолчанию — recorded, не mock.

Отдельно понять три примера:
- D43window0:180snapshots/174usable/135mature;actualStart unknown, firstReceipt отдельно.
- D45:174decisions/1392variants,362censored;не1440variants D43 all-start denominator.
- F01:recorded0/114fits,135engineeringfits отдельно;holdout release0≠globalexposures0.

## 5. Verify before coding

```sh
pwd
git status --short --branch
git worktree list
git log -5 --oneline
python3 apps/control-center/scripts/check_scope.py
python3 apps/control-center/scripts/workflow.py --check
python3 -m unittest discover -s apps/control-center/tests -v
python3 apps/control-center/adapter/export_snapshot.py --check
```

Expected directory `/Users/root1/.codex/worktrees/ec89/memecoin-ai`, branch
`control-center/observatory-handoff`. Не main/detached/чужойworktree. Если иначе — не
переключать main автоматически, сообщить конкретное несоответствие. Ветка может иметь
новые UI commits после handoff; читать latest UI memory, сохранять чужие изменения.

После чтения кратко сообщить: branch, understood scope, actual verification, первые задачи.
Затем autonomous loop из WORK_STATE: Phase0/Phase1 и следующие ready задачи. Большой backend/GUI здесь за тебя не сделан: создатьweb/,
выбранную tooling matrix, useful Observatory. Прогресс записывать вUI Memory Bank.
Готовый стартовый prompt: [GROK-PROMPT.md](GROK-PROMPT.md).
