# CC-00 self-review и evidence handoff

2026-10-03. Core baseline6a7e079. Scope planf28e5fd, scaffold06893f3.
Финальная documentation commit — в git log (не self-referential hash).
Это архитектурная подготовка, не готовый GUI и не новый core этап.

| Вопрос | Проверка / результат | Остаточный предел |
|---|---|---|
| 1. Может ли Grok изменить core? | Scoped AGENTS/Cursor rule/Git guard; diff только в3owned areas | Не OS sandbox: owner может обойти guard. Review обязателен |
| 2. Может ли UI прочитать holdout? | Runtime только minimal bundle; protected rows имеют4planned fields; before-open deny test | Новые artifact roots запрещены; malicious OS owner не изолирован |
| 3. Может ли UI запустить RPC/trading? | Нет core/provider imports/subprocess/actions;2GET only, write verbs405 | Новые dependencies/endpoints требуют review |
| 4. Может ли mock выглядеть REAL? | Provenance + qualification + input kind, DEMO off/separate-cache policy | Frontend labels ещё реализует Grok; component tests обязательны |
| 5. Есть ли второй source of truth? | Детерминированная projection с9exactSHA; --check воспроизводит bytes | Нельзя вручную менять facts или автоматически repin |
| 6. Hardcoded neural architecture? | modelKind/studyId — строки; activeModel=null; F01 — отдельный study | Study-specific view через adapter, не глобальные ifGRU |
| 7. Можно ли добавить prediction/trading? | Versioned providers/capability navigation, separate future control plane | Semantics/auth определяет core, не POST в read API |
| 8. Не мешает ли D50 worktree? | Собственная ветка/working files; main и services не изменены | Git refs общие, запрещены main operations/cleanup |
| 9. Восстановит ли новый чат контекст? | Curated reading, UI Memory Bank, phases/commands/prompt | После каждого phase Grok обновляет state |
| 10. Можно ли выбросить ветку? | Да: нет core изменений, installs, corpus writes, secrets/global deps | Закрыть только UI processes; не clean основной checkout |

## Фактические проверки

- Python3.13.6, Node24.5.0, npm11.5.1; frontend packages не установлены.
-9stdlib tests прошли: deterministic reproduction; source allowlist before open;
  protected field set; purpose/null/ns precision; no raw paths/core imports; traversal/
  symlink/FIFO/size/hash/duplicateJSON/nonfinite; HTTP Host/origin/header/verbs/query/error.
- Scoped Ruff/format прошли. Projection33 910bytes, SHA в published/manifest.json.
- Export --check прошёл. Real loopback HTTP integration served approved data и отказал
  mutations; внешние запросы не выполнялись. Frontend/big GUI/browser visual validation
  ещё отсутствуют. Это предел handoff, не скрытый acceptance всей будущей v1.
- Scope guard прошёл; core source/contracts/Memory Bank unchanged от6a7e079.
- No-private-path/command marker test в DTO. Provider RPC/training/transactions0.
- Denied-source canary не открывает holdout и его metadata: тест меняет pin path в памяти
  и проверяет, что reader вызван только для UI pin manifest.

Core make check не запускался: core не изменён; UI имеет scoped tests и не меняет core
environment. Нельзя выдавать historical D49 tests502 за test count этой ветки.
История исправлений: test variable shadowed http module; lintE731 lambda → def.
Оба исправлены, final checks passed. Статус frontend tests/build — НЕ ПРОВЕДЕНЫ.

## Исправления архитектуры после review

- Direct runtime reads заменены approved projection. Нет arbitrary paths и raw JSON API.
- Избраны safe fields вместо whole evidence: нет commands/private paths/account key data.
- Holdout обрезан до backend DTO, не спрятан CSS.
- Global exposure0 заменён NOT_CERTIFIED; releases считаются отдельно.
- Actual start не приравнен first receipt; historical source pass не live health.
- Пользовательский window1 failure пример не принят за факт; D43 recorded и D48 design
  разделены по namespace/статусу.
- Offline allowlist проверяется до source open, а не просто доверяет редактируемому config.
- Core Memory Bank не засорён frontend details; вся UI работа удаляется независимо.

## Следующий владелец

Grok4.7 Extra High в этой ветке, Phase0→Phase1. Web v1 должна использовать настоящие
recorded artifacts. Новые core interfaces — CC-P001/proposals; основной чат продолжает
D50+ самостоятельно. Этот handoff не даёт activation/training/admission permission.
