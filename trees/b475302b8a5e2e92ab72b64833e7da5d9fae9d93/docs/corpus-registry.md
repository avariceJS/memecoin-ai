# D45: immutable registry, qualification и global folds

2026-10-03. Независимый offline поток рядом с активным frozen D43. D43 не менять,
не переустанавливать и не вызывать collector/RPC. D44 training gate не открывать.

## План до реализации/опытов

Stdlib пакет memecoin_registry отдельно от D43 и memecoin_ml (сохраняем source identity
старых checkpoints). Registry связывается с одной immutable calendar plan/cohort/target
semantics. Только завершённые sealed windows с matching outcome, полным file inventory,
без symlinks/path escape и с повторной semantic verification могут попасть в development.
Failed/missed/pending/unsealed сохраняются в inventory и не увеличивают corpus.
Повтор окна — idempotent; изменённый seal/outcome и пересечение decision identity — отказ.
До и после копирования/верификации сверять seal; исходные availability не менять.

Один writer lock, staging → atomic publish; immutable objects и цепочка revisions с
parent hash. Сбой оставляет evidence и не публикует частичную revision; no retry loop.
Следующий sync может безопасно продолжить; прежние versions/outputs не перезаписываются.
Каждая revision содержит полный inventory, development bundle, fold membership и report.
Registry import time отдельно от source online availability; это offline historical
разбиение, не заявление о том, что модель была обучена/доступна в прошлом.

Frozen absolute cutoffs до новых окон. D43 plan: fold0 train window0, validation1,
outer test2; fold1 expanding train0/1, validation2, outer test3. Границы — due timestamps,
embargo30s после train/validation cutoff; maturity purge MAX всех scenario siblings плюс
номинальный конец горизонта. История остаётся внутри source window/segment/pool/gap.
Окна4/5 зарезервированы как будущий holdout до чтения их outcomes; start должен быть
позже создания protocol. Любое окно, пересекающее holdout start по reservation/finish,
целиком quarantine; после конца holdout тоже не добавлять в development автоматически.
D44/window0 явно exposed development. Смещать cutoffs по support/returns нельзя.

Holdout admission проверяет hash inventory/outcome/identity; semantic label verification
отложена до отдельного release stage. Никаких labels/returns/class counts holdout в
обычных reports/exports; release API отсутствует. Это процессный барьер в инструментах,
не криптографическая изоляция от владельца raw-файлов. Holdout не объявляется globally
unseen, если кто-либо читал его через другой инструмент.

Qualification раздельно: integrity, actual UTC hours/days/window/segment coverage,
unique decisions/pools, masks/censorship, complete scenario grid, greedy temporal
nonoverlap (не independent sample count), поддержка каждого action/status в folds,
планируемые и фактические missing windows. Counts не являются доказательством
статистической достаточности. Engineering floors заранее: ≥3 development windows,
≥2 UTC days,≥3 UTC hours; для каждой train/validation/test partition ≥20 decisions,
≥3 nonoverlap groups и ≥3 labels каждого status, в том числе по каждой action.
Эти диагностические floors не включают readiness. Universe/regimes, execution costs/
admission, research specification и formal readiness decision остаются unresolved;
research_training_ready всегда false, даже на богатом synthetic fixture.

Тесты: staged multi-window append/restart/idempotency, corruption/conflicting overlap,
late/seal race, malformed paths, future availability, whole-decision purge/embargo,
fixed memberships после новых окон, holdout non-disclosure/exclusion, false readiness
при всех numeric floors. Реальный sync только существующих sealed окон; offline repeat
и wheel reproduction; никаких training runs этого этапа. Seed null. На фиксации плана результатов ещё не было; фактический результат ниже.

## Реализация и команды

Код `1038265`, frozen absolute protocol — [configs/corpus-registry-v1.json](../configs/corpus-registry-v1.json).
Stdlib пакет `src/memecoin_registry`; PyTorch не требуется. Root текущего registry:
`work/corpus-registry-01`, привязан к `work/calendar-execution-01`. Runtime D43 и
исходники D44 не менялись. Старые ML checkpoints сохранили source identity.

```sh
# Уже выполнено. Не пересоздавать существующий root:
# .venv/bin/python tools/corpus_registry.py init work/corpus-registry-01 \
#   --calendar work/calendar-execution-01

# После появления новых окон — безопасно повторять вручную или из следующей сессии:
.venv/bin/python tools/corpus_registry.py sync work/corpus-registry-01
.venv/bin/python tools/corpus_registry.py audit work/corpus-registry-01

# Новый output; для новых данных явно выбрать новую revision:
.venv/bin/python tools/corpus_registry.py export work/corpus-registry-01 \
  work/d45-fold0-train-NEW --revision 0 --fold fold0 --partition train
```

Sync не запускает collector, не ждёт будущего окна и не делает RPC. Background sync
или новый LaunchAgent не установлены. `init` требует completed reference window0 и
ещё будущий holdout start. После этой даты прежний protocol не пересоздавать задним
числом; новый registry/protocol потребует нового будущего holdout и явной lineage.

`contract/` immutable; `objects/<window-seal-sha>/` содержит проверенный development
bundle; `revisions/000000...` — полные membership/report/dataset snapshots с parent
manifest hash. `attempts/` сохраняет start/result/failure и незавершённый staging.
Files/directories flush через fsync перед atomic rename; `.writer.lock` не позволяет
двум writers публиковать одновременно. Только rename complete revision — commit point.
После crash около commit point клиент может не получить acknowledgement: следующий
sync читает валидные revisions и либо возвращает no_change, либо публикует новую.
Не удалять failure history и не переименовывать staging в revision вручную.

Одинаковое содержимое inventory возвращает no_change; новый as-of сам по себе не
создаёт новую revision. Late import может дополнить membership прежнего периода
новыми IDs только в новой revision; предыдущая frozen revision не меняется. Это
retrospective offline availability audit, не воспроизведение фактически обученной
тогда модели. Target values никогда не используются для выбора cutoffs.

Audit работает без доступа к calendar root, воспроизводя bundle/folds/qualification
из pinned objects. Sync отдельно сверяет текущий source inventory. Holdout raw не
копируется в training objects: остаётся в D43, registry хранит hashes и operational
metadata. Stored hashes — контроль целостности локальных artifacts, не подпись против
владельца файлов, способного намеренно переписать всю цепочку. При изменении registry
code использовать pinned package или отдельную миграцию; нельзя молча пересобрать
старые reports новой логикой. Проверенный package: `work/registry-d45/installed-wheel`.

Fold экспорт содержит только выбранные decision IDs и их causal history, никогда
holdout. Одинаковая схема позволяет использовать D44 transforms/trainer в smoke mode;
он сам не начинает research по появлению нового bundle. Общий bundle development и
membership файлы — самостоятельный интерфейс для будущего fold-aware experiment runner.
В D45 никакого обучения не запускалось, кроме прежних bounded unit/integration tests.

### Holdout и операторский workflow

| Роль | Окна D43 | Период |
|---|---|---|
| Fold0 |train0 /validation1 /outer-test2|2026-10-03 → 2026-10-04|
| Fold1 |train0–1 /validation2 /outer-test3|2026-10-03 → 2026-10-04|
| Reserved final holdout |4–5|2026-10-04 22:53:59.502126 → 2026-10-05 11:23:59.502126 MSK|

Точные half-open границы/embargo в protocol. Expanding folds не независимы друг от
друга; прежний outer test может стать доступной historical validation в более позднем
fold, как заранее записано. Это не разрешение выбирать архитектуру по финальному holdout.
Любое окно, дошедшее до holdout start, quarantine целиком; post-holdout тоже зарезервирован.

**Для routine status использовать D45 sync/audit.** Старый D43 `calendar_inventory.py`
читает подробные target/uncertainty reports всех окон и после появления4/5 может раскрыть
holdout. Его frozen код не изменён, но не использовать его для просмотра этих labels.
Automatic raw/target QA внутри frozen D43 продолжает работу; его artifacts не просматривать
ради model selection. Любое внешнее чтение holdout labels/метрик фиксировать как exposure;
в будущем release требует отдельного review. D45 не способен обнаружить обход через
произвольный файловый просмотр и не выдаёт сертификат globally unseen holdout.

## Фактический результат — 2026-10-03

[Evidence](evidence/corpus-registry-2026-10-03.json). Реальная revision000000:
1 development window,5 pending;180 feature starts,174 usable,6 history-warmup unusable.
1392 variants:988 conditional_roundtrip,22 entry_unfilled,20 exit_unfilled,362 censored.
122 decisions имеют все scenarios observed; greedy temporal nonoverlap5, **не**5
независимых рыночных наблюдений.3 pools,1 UTC day/hour. Фактический union observed spans
279.775962s, calendar elapsed289.962105s; промежутки между будущими окнами не добавляют
observed duration. В обоих folds train174,validation0,test0; это ожидаемый incomplete
registry, не повод сдвигать frozen cutoffs.

Все numeric qualification gates false, research readiness false. Даже когда diagnostic
floors станут true, universe/regimes, own execution costs/admission, research specification
и formal readiness decision остаются отдельными blockers. Нельзя интерпретировать
3 labels/action/class или2 UTC days как статистическую гарантию достаточности.

17 registry tests прошли: synthetic multi-window append, late import, restart и corruption,
holdout exclusion, closed gate при всех numeric floors. Это D43-shaped fixtures с
mocked export boundary, не дополнительные реальные окна.419 base tests (torch module
skipped),433 combined tests в optional ML env,ruff/strict mypy/smoke/build прошли.
Repeat sync=no_change; audit и fold export exact из installed wheel.70 protected D43
files до/после совпали, source identity D44 сохранён. Новых registry/network failures
в real sync не было; failures в fault tests инъецированы намеренно. Remote CI не проверен.

Решение: registry/folds приняты для инкрементального offline подключения новых окон,
research gate остаётся закрытым. Следующий независимый D46 — research specification и
readiness dossier: область обобщения/вселенная, target/execution assumptions, критерии
достаточности и model-selection budget. Параллельно sync новых реальных D43 окон,
не раскрывая reserved holdout и не меняя frozen collector. Полный research training
требует отдельного обоснованного решения, а не просто исполнения расписания.
