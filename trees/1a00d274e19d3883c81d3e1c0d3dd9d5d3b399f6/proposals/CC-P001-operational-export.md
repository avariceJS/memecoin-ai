# CC-P001 — безопасный operational metadata export

Статус: **PROPOSED, НЕ ПРИНЯТ core**. Автор UI workstream, baseline6a7e079.
Это запрос интерфейса, не разрешение frontend agent менять collector или запускать export.

## Проблема

Committed evidence отвечает «что было известно в момент отчёта». Для текущего calendar
нужна безопасная metadata view. Старый D43 inventory раскрывает target reports; прямое
чтение windows/seals/ledger не допустимо для UI, даже если оно read-only.

## Предлагаемая граница

Core-owned exporter публикует небольшой versioned snapshot **в отдельный public-to-local-UI
каталог**, не внутри raw tree. Writer принадлежит core, frontend не запускает его. UI
backend получает только этот каталог, без доступа к credentials/raw/holdout/weights.
Atomic publish файла+manifest/revision, bounded size, source hashes/recorded time/sequence.
UI reader не восстанавливает semantics из raw и не делает verification с source traversal.

Пример proposed envelope (сейчас отсутствует):

```json
{
  "schema": "operator-metadata/1",
  "nodeId": "opaque-local-node",
  "revision": "opaque-revision",
  "recordedAtNs": "decimal-nanoseconds",
  "sourcePlanId": "opaque-plan",
  "services": [],
  "windows": [],
  "permissions": {"readOnly": true},
  "completeness": "explicitly-scoped"
}
```

Это схема-эскиз, не готовый admissible artifact. Core должен уточнить:
- источник service state: snapshot time vs process check vs recent successful collection;
- actual start/finish/outcome и отдельные corpus/QA/execution states, включая NOT_RUN;
- missed/pending/failed/quarantined и stopped/exhausted, без вывода их из label outcomes;
- before-read partition guard: protected rows только public plan ID/time/access;
- допустимо ли включать protected operational completion вообще; default **нет**;
- exposure scope/count completeness: recorded ledger ≠ все внешние открытия;
- redacted error taxonomy вместо worker log text/URLs/args; никаких secrets/fingerprints;
- source version/staleness budget/max file size/atomicity/integrity, rollback detection;
- если raw verification не выполнена, явно NOT_VERIFIED, не PASS по manifest name.

## Acceptance для будущего core решения

Fixture с protected canary labels: exporter не открывает их файлы даже ради checksum;
metadata output/browser responses не содержат canary. Changed/missing/partial files дают
UNKNOWN или explicit error, не false zero/success. Crash во время publish не смешивает
revisions. Writer не меняет collection cadence/budget/available time и не вызывает network.
UI закрывается без stop signal core. Никакого command/control endpoint в этом proposal.

## Порядок review

Основной core agent определяет feasibility/semantics, IDs/data access и утверждает новый
contract/evidence в своей ветке. UI workstream получает explicit accepted spec/reference
commit, затем отдельным commit добавляет новый reader adapter иtests. До этого текущая
проекция остаётся исторической, System/current services UNAVAILABLE. Не торопить D50.
