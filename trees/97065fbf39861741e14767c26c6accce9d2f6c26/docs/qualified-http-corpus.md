# D41: bounded Alchemy HTTP corpus

2026-10-03. Кампания начата и закрыта на первом connect timeout; новый corpus не получен.
[Evidence](evidence/qualified-http-corpus-2026-10-03.json),
[замороженный план](../configs/qualified-http-corpus-v1.json), код сбора `17ebb8d`.
Это результат следующего этапа после [D40-v2](alchemy-pilot-v2.md), а не продолжение
WSS эксперимента или доказательство недоступности Alchemy вообще.

## План и фактическое выполнение

По результатам matched-cadence сравнения выбран прежний collector с Alchemy HTTP,
persistent verified TLS, finalized, profile2, фиксированная прежняя когорта из трёх
пулов. WSS выключен. Два prospective сегмента по30 rounds с интервалом5 s, максимум180
запросов, новые процессы/соединения на границе. Один failure закрывает всю кампанию,
автоматических retries и скрытого fallback нет. План и provenance созданы до сети.

Первый getMultipleAccounts остановился через15,0718 s: `category=timeout`,
`failed_phase=connect`, `http_status=null`, `reused_connection=false`. Вызов
HTTPSConnection.connect объединяет DNS/TCP/TLS; точная подфаза и upstream причина
не установлены. Нет evidence ошибки credentials, quota, RPC или account coherence:
HTTP ответ не получен. Новый доступ или ключ от пользователя сейчас не требуется.

Запросов1, successful snapshots0, response bytes0. Второй сегмент не запускался;
кампания закрыта marker `closure.json`. Original reservation, attempt, времена,
source snapshot и failed seal сохранены без правок в `work/qualified-http-corpus-01`.
Усилен только CLI guard для будущих запусков: даже crash между seal и closure не
разрешает retry failed/interrupted сегмента по более мягкой общей campaign policy.
Пять synthetic boundary tests проверяют этот запрет без сети.

## Offline результат и границы

Existing execution targets/quality/support сформированы и точно воспроизведены dev
и installed wheel. Три feature rows masked,24 scenario rows censored по
`invalid_start_observation`, mature/evaluation decisions0, nonoverlap windows0.
Наличие трёх разных pool identifiers в отчёте не означает рыночного coverage.
Reference captures служат только выбору когорты, не новым online observations.

Engineering cutoff закреплён до сети в plan; наблюдений по обе стороны нет. Seed null.
Execution model прежняя: directions2 × size100/1000ppm × delay0/5s, holding20s,
tolerance15s, slippage100bps; conditional snapshot reset без persistent own impact,
unknown inclusion/network costs, net_pnl null. Никакие targets не стали исполнимыми
или зрелыми от одного факта воспроизведения. ML/RL/paper/live не запускались.

377 tests, lint/format/mypy, smoke/build прошли. Проверки integrity/availability,
CPMM/features/targets, protocol scope и старые artifacts не менялись. Raw остаётся
в ignored work; в evidence — hashes40 файлов, планы, safe attempt и полные QA reports.

```sh
.venv/bin/python tools/qualified_http_corpus.py verify work/qualified-http-corpus-01
work/alchemy-v2b-wheel/bin/python tools/qualified_http_corpus.py verify work/qualified-http-corpus-01
```

## Решение и следующий этап

WSS остаётся optional/off: D41 не добавил evidence его пользы. Выбор Alchemy HTTP для
bounded источника сохраняется как проверенная гипотеза, но готовность длительного
сбора не подтверждена. Strict correctness успешных snapshots из D40 и доступность
нового cold connection — разные свойства; production migration/SLA не объявлены.

Следующий этап — отделить DNS/TCP/TLS в диагностике cold connection, сохранив verified
TLS, общий deadline, исходные ошибки и нулевые скрытые retries. Новый bounded network
план допустим после новых диагностических оснований/другого календарного окна; эту
закрытую кампанию не возобновлять и не увеличивать timeout ради получения success.
Затем новые prospective окна и execution uncertainty QA. Полный corpus остаётся
недостаточным для обучения, расширение transport экспериментов не является самоцелью.
