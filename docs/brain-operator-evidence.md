# Журнал автономного оператора

Read-only срез для следующего Codex через несколько дней. Это не edge, не promotion и не
подтверждение F01. Протокол контура — [автономный оператор](brain-autonomous-operator.md).
Виртуальная бухгалтерия — [paper loop](brain-paper-loop.md). Граница выпуска модели —
[evaluation](../memory/evaluation.md). Исторический Brain остаётся exploratory:
[development](brain-development.md).

```bash
.venv/bin/python -B tools/brain_operator_report.py \
  --root work/brain-b13/operator01 \
  --output /tmp/operator-report.json \
  --markdown-output /tmp/operator-report.md \
  --now-ns 1700000010000000000
```

`--output` и `--markdown-output` создаются только как новые файлы. Повторный путь, symlink и
запись в `datasets/`, `candidates/`, `exams/`, `paper/`, `ledger/` или `operator.sqlite`
отклоняются. Отчёт не меняет журнал, датасеты и сделки.

## Что считается

API: `build_report(root, now_ns=None)`. При одном и том же root и cutoff результат
детерминирован. В хешах лежат `config.json`, pinned runtime `manifest.json`, канонический
снимок таблиц control SQLite и sha прочитанных каталогов. Журнал читается живым
соединением `mode=ro`: `backup()` копирует согласованные страницы в память. Копировать
байты `operator.sqlite` отдельно от `-wal`/`-shm` нельзя: между копиями writer успевает
их разъехать. `immutable=1` к живому WAL не применяется. Служебные `-wal`/`-shm` может
создать сам SQLite; это координация чтения, не изменение evidence.

Наблюдаемые часы считаются только по таблице `heartbeats`: соседние отметки одного сервиса
с интервалом не длиннее 90 с складываются, более длинный разрыв остаётся дырой. В эту сумму
входят и unhealthy-отметки: это присутствие записей, не uptime (`uptime_claim=false`).
Отдельно публикуются `observer_ns` (только service `operator`) и `healthy_service_ns`
(source/paper/exam, оба конца интервала healthy). Нет таблицы или нет строк на cutoff —
статус `unknown`, часы `null`. Elapsed от первой отметки до cutoff публикуется отдельно.

Будущий forecast loss берётся из persisted shadow экзамена. Каталоги:
`paper/<exam>/candidate-shadows/<model_sha>/shadow/predictions` и `outcomes`, плюс прежний
`paper/<exam>/shadow/predictions` и `outcomes`. Один и тот же
`(exam, model_sha, sha256 файла)` считается один раз. Имя каталога `candidate-shadows`
должно совпасть с `model_sha` внутри файла. Файл без `model_sha` не приписывается модели.
`predicted_ns` не раньше activation и не позже cutoff. `prediction_sha` outcome равен байтам
prediction. Горизонты публикуются раздельно. Минуты — `horizon * bar_s / 60`: `bar_s` из
конфига экзамена либо из строки `BAR = ...` в pinned `runtime/memecoin_brain/source.py`.
При `BAR = 300` горизонт 1 — 5 минут, горизонт 3 — 15 минут. Loss горизонта — сумма
записанного `candidate_metrics.*.pinball` только по scored outcomes этой модели. Модели
не складываются в один pinball. Pending горизонт не получает нулевой loss. Если shadow-каталога
нет, `forecasts.status=unknown` и счётчики `null`. Пустой существующий каталог — измеренный
ноль. Независимая группа — `(exam_id, model_sha, pool, event_close_s)`. Книги paper остаются
раздельными. Калибровка не вычисляется: это описание файлов, без доверительного интервала.

Исторический test карточки модели подписывается `reused_descriptive` и `independent=false`.
Он не входит в будущий loss. Выбранное семейство — записанный validation choice, не новый
победитель. `promotions` всегда пустой список.

Paper PnL — опубликованные `realized` и `equity`. `mark_stale` и возраст dashboard старше
120 с видны в `paper.stale`. Прошлые fills не пересчитываются. `paper.actions` считает
решения WAIT/ENTER/HOLD/EXIT. `enter_next_observed_close` попадает в `stages.intent` и не
становится fill. `paper.fills` — только записанные счётчики ledger `entries`/`exits`.
`pending` книги остаётся отдельно.

`history_series(root, limit=500)` отдаёт фактические точки source/corpus/training/paper.
События `events` остаются в серии. Точки sampler добавляются с `origin=telemetry`, не
вместо них.

## Sampler

Родитель вызывает `telemetry.record(root, view, state, now_ns=None)` примерно каждые 15 с.
Модуль сам сервисы не запускает. Нужен уже инициализированный `operator.sqlite` схемы
control v1; отсутствующую базу он не создаёт. Своя транзакция `BEGIN IMMEDIATE`, timeout 5 с.
Таблицы, если схема control принята:

```sql
CREATE TABLE IF NOT EXISTS heartbeats (
    at_ns INTEGER NOT NULL,
    service TEXT NOT NULL CHECK (service IN ('operator', 'source', 'paper', 'exam')),
    exam_id TEXT NOT NULL DEFAULT '',
    healthy INTEGER NOT NULL CHECK (healthy IN (0, 1)),
    PRIMARY KEY (at_ns, service, exam_id)
);
CREATE TABLE IF NOT EXISTS telemetry (
    at_ns INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL
);
```

Не больше одной точки на 60 с по последнему heartbeat `operator`. Время только фактическое:
будущий stamp и stamp раньше последней точки отклоняются, старые строки не обновляются.
`operator.healthy` всегда 1. `source.healthy` берётся из bool `view.source`.
`paper.healthy` — из `view.paper_exams[].operations.paper`. В payload только узкий снимок:
source heartbeat/healthy/stopped/attempts/cooldown/failures/budget/expiry, coverage
bars/pools/matured/event_days/receipt_days, mode/phase, и по экзамену diagnostics,
ledger head, книги cash/equity/realized/unrealized/fees и счётчики positions/trades/forecast.
Прошлые fills не выводятся. Ошибка возвращается родителю и не подменяется пустой точкой.

Не читаются веса, raw captures, F01, D43 и holdout. Пути артефактов вне root, symlink и
несовпавший manifest в отчёт не раскрываются и никуда не исполняются.

Черновик приёмки B13 — `docs/brain-b13-acceptance.md`. Машинный снимок текущего корня
`work/brain-b13/operator04` — `work/brain-b13/operator-report03.json`, cutoff
`1791150099863307000`. Это не приёмка продукта. Более ранние
`operator-report-preview01` и `operator-report-acceptance04` оставлены как прежние срезы.
