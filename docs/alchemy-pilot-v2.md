# D40-v2: matched cadence и роль WSS

План заморожен до сети: [v2](../configs/alchemy-pilot-v2.json). V1 и его raw/source
сохранены; прежний pinned wheel воспроизводит v1. Три прежних пула, finalized/profile2.
Два процесса по 120 секунд: steady без fault, затем symmetric stress. Никакого ML,
подписи, торговли, оплаты/upgrade, QuickNode или D33 historical audit.

Четыре Alchemy варианта, отдельные persistent HTTP соединения:

- `http5`: фиксированная абсолютная сетка 5 s.
- `http10`: фиксированная абсолютная сетка 10 s.
- `wss10`: та же 10 s сетка, WSS как дополнительный raw/диагностический канал.
- `adaptive`: eligibility на общей 5 s сетке, changed-content trigger, heartbeat 10 s.

`http10`/`wss10` — строго matched-cadence пара. Adaptive сравнивается как другая
sampling policy с двумя fixed-cadence controls, не как чистый эффект транспорта.
Общая 5 s сетка features, одна и та же temporal math, вращение порядка запросов.
Deadline вычисляется от старта, а не от завершения RPC. Overrun >4 s прекращает
сегмент без catch-up burst. Raw identical-content notifications сохраняются, но
новый slot при том же value не вызывает новый HTTP. Late/same-slot ambiguity остаётся
консервативным hint; HTTP anchors не вытесняют уже полученный более новый hint.

В stress все четыре arm получают gap на tick12, закрывают HTTP, пропускают read;
WSS также disconnect/resubscribe. На tick13 у всех новый recovery checkpoint,
дальше прежняя сетка. Глобальный gap не закрывает прошлую историю, warmup обязательный.
Public D39 collector остаётся отдельным неизменённым contextual control.

Предварительный критерий для adaptive: минимум20% меньше HTTP, чем http5, ухудшение
usable fraction не более5 п.п., не меньше economic changes, чем http10; дополнительно
нужно ≥20% меньше полного payload относительно http10 ИЛИ ≥20% больше наблюдённых
economic changes при payload не выше http5. При отсутствии этого evidence WSS остаётся
опциональной диагностикой, выключенной по умолчанию. Steady и stress не смешиваются.
Counts changes не объявляются полным trade flow; мало/ноль активности не доказывает
равенства источников. Все budgets/stop rules закреплены в JSON.

Следующий этап выбирается по результату, с отдельным решением в Memory Bank. Длинный
corpus, coverage и execution uncertainty остаются gates перед обучением.

## Отказ первоначального запуска и отдельный bounded follow-up

Первый `work/alchemy-pilot-v2-01/segment-00` остановился на WSS EOF после четырёх
acknowledgements, до Alchemy HTTP snapshots. Close code отсутствует; upstream причина
не установлена. RFC6455 transport побайтово совпадает с успешным v1. Public контроль
дал два HTTP200, затем намеренно остановлен; его third attempt SourceError — локальная
отмена, не третий сетевой запрос и не public provider failure. Failed/raw bundle запечатан.

Новый самостоятельный [план v2b](../configs/alchemy-pilot-v2b.json) сохраняет критерии,
когорту, частоты и бюджеты сравнения; меняется admission: один subscribe in flight,
следующий только после ack. Это проверяемое уменьшение startup burst и более точная
локализация частичной регистрации, не установленное исправление EOF. Максимум одна
дополнительная кампания steady+stress; любой неожиданный отказ завершает новые WSS
запросы этой сессии. Первоначальная failed campaign не возобновляется и не переписывается.

## Итог D40-v2, 2026-10-03

[Evidence](evidence/alchemy-pilot-v2-2026-10-03.json), код `57927ec`.
V2b steady и symmetric-stress завершены; новые процессы, 421 strict Alchemy snapshots,
144 public controls. Начальная неудачная попытка добавила 2 public snapshots и 4 ack,
но ни одного Alchemy snapshot. EOF остаётся unexplained; успешный serial вариант
не доказывает причинного исправления provider failure. Других WSS повторов не было.

| Steady 120 s | HTTP5 | HTTP10 | WSS10 | Adaptive |
|---|---:|---:|---:|---:|
| HTTP requests | 72 | 36 | 36 | 49 |
| Usable / 72 common rows | 69 | 66 | 66 | 66 |
| Observed economic changes | 13 | 9 | 10 | 14 |
| Standalone received payload, bytes | 262152 | 131076 | 288942 | 336855 |

Scheduler corrected: http10/wss10 имеют одинаковые planned/actual ticks; все четыре
варианта имеют один и тот же feature as-of grid. Content coalescing сохранил raw, но
подавил 94 unchanged hints в steady и 104 в stress. Late messages не заменяют HTTP
anchors; неизвестный write по другому dependency не скрывается unrelated hint.
Каждое решение adaptive «читать/пропустить» пересчитано из того же arrival-order журнала.

Adaptive экономит 31,94% HTTP против HTTP5 при снижении usable на4,17 п.п., но payload
на28,49% выше HTTP5 и в2,57 раза выше HTTP10. Primary criterion **false**. Больше observed
changes не доказывает лучшую полноту: даже matched10s пара отличается9/10 из-за порядка
serial reads и разных banks. Exact same-bank equality и statistical superiority не заявлены.
Общий WSS канал физически shared, но standalone budget каждого WSS arm консервативно
включает весь его payload, без искусственного деления overhead между экспериментами.

В symmetric stress: HTTP counts69/36/36/47; usable63/60/60/61 из72 common rows.
Gap введён всем arm на tick12; tick13 — новые recovery reads, затем исходная сетка.
Это отдельный stress, а не часть primary steady score. 1274 WSS messages в v2b,
ни одна notification не стала pool observation. Полнота intermediate history false.

372 tests, lint/format/mypy, smoke/build, точное dev/wheel воспроизведение обоих segments
и public controls. Failed v2 воспроизведён своим pinned wheel. Верификатор дополнительно
проверяет raw receipt times и matching cadence. Bounds/receipts/config/CPMM/targets не ослаблены.
CPU/RSS общепроцессные, WSS source-to-receipt latency неизвестна; фактический bill не измерен.

## Архитектурное решение и переход к D41

WSS **опционален, выключен по умолчанию**, не обязательная зависимость prospective
collection или торговой модели. Его raw/diagnostic adapter сохраняется для конкретных
гипотез change discovery, но очередной цикл transport tuning не является приоритетом.
Full transactional stream потребовал бы отдельной семантики; эта работа не начата.

Для следующих bounded prospective segments выбран **Alchemy HTTP, persistent TLS,
5 s**, с неизменённым existing collector/profile2. Это явное решение по bounded
источнику, не production SLA и не фиксация частоты будущей торговой системы. Public
D39 сохраняется как reference/control; hidden fallback нет. Alchemy HTTP прошёл
строгие checkpoints v1/v2, а HTTP5 дал больше доступных snapshots на общей сетке.

Следующий логичный этап D41: [план](../configs/qualified-http-corpus-v1.json),
2×30 rounds×5 s, максимум180 HTTP, новая invocation/TLS на сегмент. Первая ошибка
закрывает кампанию без resume/retry. Только новая prospective availability, два
immutable segments; затем existing execution-conditioned targets, quality и full-grid
nonoverlap support. Seed null; execution model остаётся conditional snapshot reset,
unknown costs/inclusion/own impact; net_pnl null. Обучение/RL/paper/live не разрешены.

Воспроизведение v2b:

```sh
.venv/bin/python tools/alchemy_pilot.py verify work/alchemy-pilot-v2b-01 --segment 0
.venv/bin/python tools/alchemy_pilot.py verify work/alchemy-pilot-v2b-01 --segment 1
.venv/bin/python tools/alchemy_v2_report.py work/alchemy-pilot-v2b-01
# Начальная неудача требует собственного source-pinned пакета:
PYTHONPATH=work/alchemy-v2-initial-wheel .venv/bin/python tools/alchemy_pilot.py verify work/alchemy-pilot-v2-01 --segment 0
```


D41 фактически начат: первый cold connect timeout закрыл кампанию без retries и без
новых snapshots; offline QA полностью censored. Это отдельный availability gate,
не изменение WSS decision и не повод объявлять source production-ready.
[Результат D41](qualified-http-corpus.md),
[evidence](evidence/qualified-http-corpus-2026-10-03.json).
