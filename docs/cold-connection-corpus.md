# D42: cold connection → prospective окна → execution uncertainty

2026-10-03. [План](../configs/cold-connection-v1.json) фиксируется до новых запросов.
D41 closed artifact не возобновляется. WSS off, без QuickNode, D33, оплаты, сделок и ML.

Четыре новых процесса/соединения, порядок Alchemy/public/public/Alchemy. Между первым
и вторым раундом минимум60 s. До двух strict finalized/profile2 checkpoints одного
фиксированного pool на соединение: первый cold, второй проверяет фактический reuse.
Первая ошибка завершает trial, максимум3 failed trials на всю диагностику. Timeout15s
не увеличивается; parent завершает зависший child через35s. Нет повторов RPC внутри
trial, адреса по DNS выбирает прежний stdlib алгоритм; его отдельные TCP attempts
регистрируются, не объявляются RPC retries. Максимум8 RPC, новых transport вариантов нет.

Тонкие wrappers фиксируют start/end DNS, TCP connect, TLS wrap/handshake и safe exception
class/errno, не сохраняют URL/key/exception message. Они вызывают прежние функции
без изменения аргументов; TLS verification остаётся. Raw captures и phase journal
сохраняются до обработки. Это observer-only instrumentation отдельного инструмента,
а не изменение production transport. DNS может блокировать дольше socket timeout;
внешний child deadline ограничивает диагностический процесс. TCP/TLS failures не
доказывают upstream причину, billing/rate limits или неверные credentials.

Кандидат для corpus должен завершить последний trial двумя strict snapshots. Выбор
по числу полных trials, tie→Alchemy; иначе corpus сеть не запускается и выполняется
offline uncertainty на прежних данных. Выбор bounded operational mode не означает SLA.
Никаких новых циклов tuning после этой диагностики.

Два prospective окна на выбранном source, каждый со своим immutable plan/cutoff:
2×30 rounds×5s, максимум180 requests/окно,360 всего. Старт второго не ранее600s после
первого, вне зависимости от доходности/успешности первого. Ошибка закрывает только
текущее окно; нет resume failed segment/fallback. Новая invocation/TLS на сегмент.
Каждый gap честно отсутствует в data; окна не склеиваются. Десять минут разнесения не
объявляются разными market regimes. Actual calendar bounds обязательно в evidence.

Existing targets/quality/nonoverlap QA плюс offline uncertainty: conditional outcomes
до неизвестных network costs, break-even additional roundtrip cost в input-asset bps,
сетка0/5/10/25/50/100bps — только assumptions, не реальные тарифы. Сравнить paired delay0/5s
на тех же decisions/direction/size/partition; censored/unfilled не превращаются в нули.
Не смешивать atomic units разных assets, не суммировать overlapping hypothetical PnL.
Unknown admission/inclusion/FX/rent/persistent own impact сохраняются; net_pnl=null.
Проверить artifacts/source/offline/wheel; seed null. Обучение не начинается.

Официальная семантика: [HTTPSConnection](https://docs.python.org/3.13/library/http.client.html#http.client.HTTPSConnection),
[socket.create_connection](https://docs.python.org/3.13/library/socket.html#socket.create_connection).
Cold здесь означает новый процесс/TCP/TLS connection; OS DNS cache не очищается.
Измерения относятся к локальным вызовам, не к server-side времени или execution latency.
Наблюдатель не доказывает причину прежнего отказа и не оптимизирует TCP/TLS параметры.
Оба prospective окна завершены по frozen расписанию; итог ниже.

## Итог 2026-10-03

[Evidence](evidence/cold-connection-corpus-2026-10-03.json).
План `07fc1ae`, diagnostic `add2ef1`, corpus `1a1caec`, uncertainty `df02019`, report fix `1463fd9`.

Диагностика:4/4 cold connections,8/8 strict snapshots;4/4 warm requests переиспользовали
соединение. DNS4,8–74,7ms, TCP0,26–0,38ms, TLS148,2–178,5ms в этих локальных замерах.
D41 timeout не воспроизведён; его upstream причина остаётся неизвестной. По frozen tie
правилу выбран Alchemy. Нет доказательства превосходства над public или production SLA.
Ни новых timeout, ни дополнительных diagnostic rounds не было.

| Окно | Accepted | Usable | Mature decisions | Engineering evaluation | Nonoverlap |
|---|---:|---:|---:|---:|---:|
| 0 | 180 | 174 | 132 | 66 | 6 |
| 1 | 180 | 174 | 136 | 69 | 6 |

Суммарный observed segment span583.122s; создания campaign plans
разнесены на599.994084s, фактические старты первых сегментов — на600.037526s. Разрыв отсутствует в данных, не backfilled.
Окно0: `2026-10-03T00:11:27.855055+00:00` → `2026-10-03T00:16:24.680728+00:00` (UTC).
Окно1: `2026-10-03T00:21:27.892581+00:00` → `2026-10-03T00:26:24.694469+00:00` (UTC).

Четыре самостоятельных процесса/сегмента:4 cold requests и356 reused requests,
network failures0, все360 HTTP snapshots accepted. Восстановление старых failed окон
не выполнялось. Во всех сегментах прежние warmup/masks/tail censoring. Два локальных
cutoff — engineering QA, не единый global ML split. Минуты одной сессии не доказывают
разнообразия regimes/market universe; неперекрывающиеся окна не независимы автоматически.

Calendar criterion по created_ns **false**: actual599,994084s вместо минимум600s, shortfall5,916ms.
Причина — runner start вместо actual first campaign creation как anchor. Первоначальный
audit остановился ValidationError; safe failure log сохранён. Исправлен только будущий
scheduler, данные/план/receipt times не переписаны, сетевого повтора нет. Offline report
воспроизводит и явно сохраняет нарушение; корректность snapshots не объявляет план выполненным.

## Execution uncertainty и решение

В допустимых calibration/evaluation partitions — 2018 conditional roundtrip rows,
из них 0 положительных до unknown network costs. Paired delay0/5s: 962 scenario pairs,
sign flips0. Это hypothetical outcomes заданных сценариев, не сделки стратегии.
Суммы по extra-cost assumptions0/5/10/25/50/100bps: positive conditional rows =
`[0, 0, 0, 0, 0, 0]`. Эти thresholds не являются измеренными тарифами.
Окно0/calibration: delay delta min/median/max = -13.054/0.000/137.860bps.
Окно0/engineering_evaluation: delay delta min/median/max = -29.891/0.000/29.894bps.
Окно1/calibration: delay delta min/median/max = -35.565/0.000/35.570bps.
Окно1/engineering_evaluation: delay delta min/median/max = -27.694/0.000/319.920bps.

Censored/unfilled остаются отдельными статусами (полные counts в evidence), не zero-fill.
Negative conditional outcomes этой короткой когорты не отрицают edge всего рынка.
Фактические inclusion/admission, native/priority costs, FX/rent и persistent own impact
не калиброваны. Net PnL неизвестен; ML/RL/paper/live не запускались.

386 tests, lint/format/mypy, smoke/build. Raw→targets/quality/support/uncertainty
воспроизведены dev и installed wheel; совпадает полный calendar report. Старые D40-v2
и D41 также воспроизведены,40 файлов D41 сохранили hashes. Артефакты и source snapshots
в ignored work; manifests/hashes и версии инструментов в evidence.

**Решение:** завершить transport tuning. Продолжать bounded Alchemy HTTP persistent
TLS, новый процесс на сегмент, WSS off, конечный budget и stop rules без hidden retries.
Следующий этап — пререгистрированные окна в разные часы/дни с теми же guards и
read-only execution-cost/timing evidence. Чужие successful transactions не считать
калибровкой admission собственной стратегии. Смена cohort/параметров по PnL не разрешена
этим audit; полноценный research specification и ML readiness ещё не достигнуты.

```sh
.venv/bin/python tools/calendar_report.py work/calendar-corpus-01
PYTHONPATH=work/d42-wheel .venv/bin/python tools/calendar_report.py work/calendar-corpus-01
.venv/bin/python tools/execution_uncertainty.py verify work/calendar-corpus-01/window-0 work/calendar-corpus-01/window-0-targets work/calendar-uncertainty-00
# Аналогично window-1 и work/calendar-uncertainty-01.
```
