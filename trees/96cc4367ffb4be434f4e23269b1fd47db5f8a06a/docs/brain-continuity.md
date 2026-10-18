# B08 — инкрементальный corpus; недельная активация отклонена

## Результат, 2026-10-04

**Переход на новый source не выполнен.** Инженерный слой готов и проверен, но frozen
operational gate не пройден: на финальном cutoff свежи 5 рынков при минимуме 6.
Не продлевали qualification сверх 32 GET, не меняли порог и не обнуляли историю ошибок.

[Итоговый evidence](evidence/brain-continuity-2026-10-04.json),
[UI staging screenshot](evidence/brain-continuity-paused-ui-2026-10-04.png).
Локальный sealed result: `work/brain-b08/evidence-final`, manifest
`4a7f350c9c6c856c42fd2ea05a80ee96d919128739fa8ef86e2fc05dc9560548`.
Raw/index, отрицательные qualification и failure receipts остаются вне Git.

**Текущее состояние продукта:** [Control Center](http://127.0.0.1:8790), B07 paper и
training jobs сохранены. B04 source штатно остановился после HTTP429 во время B08 discovery;
новые входы paused. Это источник outage, а не плановая остановка ради обучения. Ни один
старый STOP не снят, source/model/runtime bindings B07 не менялись. Его 43 ledger ticks,
393 решения и три закрытые сделки сохранены; открытых и pending на snapshot нет.
Не следует описывать этот статус как работающий непрерывный торговый цикл.

## Что готово для следующего запуска

- Immutable raw store с transactional SQLite index. Первое доступное значение свечи
  сохраняется, revisions отдельно; import/recovery не меняет реальные availability times.
  Row/membership/capture checks и bounded rotating raw audit проверяют derived данные.
- Отдельный frozen source supervisor: durable reservation до IO, global pacing/cooldown,
  ошибки отдельных pools изолированы; bounded history работает после due live.
  Сбой процесса не обнуляет quota, срок, cooldown или quarantine.
- Paper adapter с точным переносом prefix ledger, cash, positions и model namespaces.
  Проверены fixtures с открытой позицией, restart и единственным exit. **Реальный cutover
  не проводился**, старый ledger не выдаётся за новые B08 trades.
- Подготовлены полные source denominator/statuses и per-model decision/entry/exit counters
  в UI. Mobile overflow исправлен в новом коде; staging проверен на реальном B07 dashboard,
  JS errors/overflow отсутствуют на 1440 и 390 px. Frozen B07 UI не переустановлен.
- Daily trainer остаётся прежним D44/D47: eligibility ≥24h и ≥100 новых mature samples,
  четыре fits, ≤600s. В подготовленной B08 config максимум восемь runs и общий chronological
  suffix capacity около 28k bars. B07 продолжает жить по собственному прежнему лимиту три.
  Новых fits в B08 нет, champion empirical не заменён.

## Данные и наблюдённые ограничения

| Показатель | Факт |
|---|---:|
| Universe / observed pools | 12 → 24 / 24 |
| Quality active / inherited quarantine | 21 / 3 |
| First-observed bars | 15 515 → 18 600 |
| Indexed captures / version rows | 464 / 58 843 |
| Pool×candle с изменённым OHLCV, включая B04 | 232 |
| Самый длинный span одного pool | 14,68 дня |
| Новые discovery GET | 4: три success, один 429 |
| GET после cooldown | 32: 29 live success, 2 history success, 1 history URLError |
| Новые 429 после cooldown | 0 |
| Fresh на двух qualification cutoff | 0, затем 5; нужно ≥6 |
| Paid / RPC / новые fits | 0 / 0 / 0 |

Все 12 новых pools дали decoded bars, но observed не означает fresh. 90 дней были верхней
целью pagination, не полученным покрытием. Текущая discovery cohort не представляет весь
исторический рынок; survivorship, liquidity selection и неодинаковая длина истории остаются.
58 843 version rows включают одинаковые повторные ответы и не равны числу revisions.
Дальнейшая history pagination после отрицательного verdict не запускалась.

Публичная документация указывает keyless free API и 30 calls/min:
[GeckoTerminal introduction](https://apiguide.geckoterminal.com/),
[официальный FAQ](https://apiguide.geckoterminal.com/faq). Наш фактический 429 ниже этого
темпа показывает, что документация не гарантирует доступность каждого burst. Причина
срабатывания неизвестна; это не установленная ошибка credentials или доказательство
платного ограничения. B04 получил отдельный 429 через 6s после discovery; его STOP и body
сохранены. У первоначального discovery failure body не был сохранён; это ограничение
первого receipt. Новый код сохраняет HTTP error bodies.

После полного 15-min cooldown использован темп 5/min с наследованием обоих rate events.
Первый operational cutoff оказался после истечения свежести прежней 5-min свечи.
Исторический запрос получил `URLError`, HTTP status отсутствует; DNS/TLS/connect причину
receipt не устанавливает. Штатный transport cooldown выдержан. Оставшиеся восемь GET
завершили тот же лимит32; финальные 5 fresh не удовлетворили gate. История обеих проверок
сохранена. На source установлен `qualification_freshness_gate_failed`, новых GET после него0.

Это отрицательный результат текущего сочетания ширины universe, cadence и GT freshness.
Он не доказывает невозможность более узкого устойчивого GT feed и не оправдывает немедленный
переход на неквалифицированные raw события. Запас бесплатной quota здесь не главный лимит:
больше объёма запросов не делает полученную свечу более свежей.

## Воспроизводимость и эксплуатация

Full raw/index comparison exact на 18 600 bars. Один локальный замер: raw scan 1,488s,
index 0,191s; это локальный benchmark, не SLA. Независимый as-of replay B07 через новый index:
516 raw market joins, 131 published forecast joins, два model pins, 43 ticks. Три закрытия
воспроизведены также из установленного wheel. Старый GRU net −$2,345041 при assumed costs;
это накопленный B07 paper result, не новый B08 результат или evidence edge.

Проверки: 616 base tests PASS / 6 optional skips; 642 combined ML PASS; 28 focused tests;
`make check`, `make smoke`, `make build`. Deployment pin исходного source bundle:
`8dad09e5985d71c8348d517ca1440c2061ad14639805c486f12f5c466e1ca1f5`.
Подготовленная expiry 2026-10-11T11:32:39.985204Z и потолки 75k GET/8GiB не означают
разрешённую активацию. B08 LaunchAgents не загружены. Временный UI на8791 остановлен.

Для offline воспроизведения, без provider IO:

```sh
.venv/bin/python tools/brain_b08_reproduce.py work/brain-b08/replay-final \
  --source-index work/brain-b08/source01
```

Не повторять prepare/qualification поверх существующих roots, не удалять STOP. Staged
cutover tool откажет на отрицательном qualification. Raw B04 нужны для rebuild нового
index и старого paper replay. Локальные артефакты не заменяются и не коммитятся.

Weekly/monthly unattended пока не достигнут: нужны устойчивый live feed, фактический soak,
проверка ресурсоёмкости растущего source journal/paper ledger и операционный watchdog.
Их полный replay сейчас зависит от размера истории. Один incremental index эту задачу
не закрывает. Сон/выключение Mac по-прежнему означает паузу наблюдений.

## Следующий оптимальный шаг — B09

Сначала восстановить paper feed отдельным bounded планом: небольшой active set с более
частым обновлением, live-priority отдельно от cold/history pagination, измерение freshness
на нескольких полных циклах, сохранение полного denominator и унаследованных rate events.
Admission новых рынков и большой historical backfill не должны съедать live cadence.
Не менять задним числом B08 gate и не выбирать model winner на этом эпизоде.

Параллельно продолжить versioned PumpSwap semantics и независимый price cross-check,
чтобы уменьшать single-provider зависимость. Полная raw qualification не должна задерживать
возврат честно маркированного price-proxy paper. После восстановления — day-scale soak,
новые mature outcomes, daily candidates и paired behavioral comparison. Модельная сложность
сейчас не ограничивает прогресс продукта.

---

Ниже сохранены исходный план и amendments, сделанные до соответствующих запросов.

# Исходный B08 plan — семидневный paper Brain и инкрементальная история

## План до новых запросов и activation, 2026-10-04

B07 принят. Работающий paper loop — продукт, который не останавливается ради research.
Приоритет B08: убрать близкий expiry и полный повторный raw scan, расширить universe
до24 рынков и накапливать доступную историю до90 дней. Это capability/rollout на неделю,
не доказательство недельного uptime. Месячный режим требует фактической эксплуатации.

Отдельный source root/runtime, семь дней от staging, максимум75000 бесплатных public GET,
не чаще одного старта за4s (15/min), response≤2MiB, raw storage≤8GiB, free floor4GiB.
Официальный public limit30/min: https://apiguide.geckoterminal.com/faq . Пока B04 работает,
его≤5/min burst вместе с новым≤15/min остаётся ниже этого предела. Paid budget0.
Нет Alchemy/Helius/QuickNode расходов или trading transactions в этом этапе. Raw PumpSwap
qualification продолжится отдельно; неизвестные события не становятся training data.

До4 discovery pages; сохранить прежние12 pools, новые добавлять до24, без protected
F01 pools/base mints и duplicate base. Admission time реальный, selection/survivorship bias
не скрывать. Immutable raw → transactional SQLite derived index; first observed candle
не перезаписывается, revisions отдельно. Receipt time не меняется при recovery/import.
Повторный запрос уже скачанной history не нужен. Новый index воспроизводится из raw;
row checksums/capture pins и отдельный полный offline audit ловят corruption.

Live due5min, полные24 denominator/statuses; scheduling oldest due first, max8requests за
один worker≤240s. History round-robin, только при отсутствии due live, max30pages/pool,
floor=max(pool creation, now−90days), limit1000. Live limit100. Пустая/короткая страница
завершает bounded pagination, не доказывает полноту рынка; отсутствие progress завершает
lane этогоpool. Existing quality quarantine наследуется, новые schema errors изолируютpool;
не останавливать весьрынок по фиксированному числу плохихпулов. Для quarantine не более
двух recovery probes/day с двумя последовательными valid fresh для release.

Reservation доnetwork; crash расходуетattempt, следующий due не повторяет незавершённую
history page. Transient transport/5xx → exponential cooldown, max6 consecutive failures
доglobal STOP; 429 → Retry-After/min15min cooldown, третья429 за24h→STOP;401/403→STOP.
Schema/empty/stale отдельногоpool не provider outage. Integrity/disk/budget/clock → STOP.
История не блокируетlive; обучение иpaper изолированы отsource failures. STOP не стирать.

Paper migration: старые services продолжают работать доqualification. При cutover
кратко fence старыхwriters, копировать ledger/shadow/training без измененияbytes; pin
prefix head/state/cash/positions, новаяconfig/runtimebinding и явный migration record.
Новые ticks несутconfigSHA; копияпрошлогоledger не новаяторговаяистория. Старыеroot/runtime
сохраняются. Не менять исходнуюpaper policy/costs или cash, не закрыватьпозицииискусственно.
Champion bundle прежний; candidate namespaces/model versions сохраняются. ТотжеUIпорт8790.

Daily training прежнийD44/D47: ≥24h/100newmature, max8runs включаяперенесённыйB07run,
≤600s/run/4fits; ниавтоpromotion, ниF01. Fullstore сохраняетвсюисторию; training берет
последние≤7days, приcapacity — общий chronological suffix≤~28000bars (всеpools наboundary),
безотбораудачныхпулов/returns. Forecast context/targets/CPMM не переписывать.

Доactivation: fixtures index/revisions/as-of/corruption/rebuild, scheduler budget/cooldown/
local quarantine, migration exact cash/openpositions/restart; recorded fullscan/index exact
иbenchmark; bounded реальныеdiscovery/live/history. Послеqualification: frozen deployment,
запускsource/paper/train/UI, фактическиеfresh predictions, replay иsealed evidence. B07
не останавливать доуспехаqualification. Приmigrationfailure сохранить старыйконтур.

Control Center: прежние портфели/сделки, новые рынки сquality, source horizon/история/
quota/cache; отдельноеповедениекандидатов (решения, входы, realized/MTM) иpaired futureloss.
Первые новыеfits иполныесутки не симулировать радиотчёта. Следующийшаг определяется
фактическимиcoverage/failures/поведениеммоделей, не backbone complexity.

## Наблюдённое ограничение и поправка до activation

Первые три discovery pages успешны; четвёртая получила HTTP429 без Retry-After.
Через6s тотжеответ получил B04, который штатно поставил STOP. Это не доказательство
ошибки credentials или отсутствияfreeплана. Точная причина срабатывания ниже опубликованных
30/min неизвестна. Сохраняются оба receipt и старый STOP; повторных discovery GET нет.
Universe24 собран из трёх успешных страниц. B07 продолжает UI/accounting, но входыpaused.

Новыйsource наследует оба429 и15mincooldown, 2из3rateevents/24h. Доcooldown новыхGET0.
Дляrecovery выбран5/min (12s spacing), а не обещанныйпровайдероммаксимум. Третий429/24h
закроетновыйsource; этотбюджетнеобнуляетсяприrestart. Проверкапослеcooldown≤32GET,
каждыйподdurable reservation; приSTOPнемедленноостановиться. Сначалаqualifiedlive12+новые
рынки; историяиспользуетоставшеесявремя. Paidподключениянеразрешены иневыполняются.

Тест IO также выявилduplicateключ `pools` вhealth report; исправлено наobserved_pools
доlivequalification. Paper ledger/политика имодели не менялись. Capture bodies/index и
переносстарыхсделок проверяются independently; DB — derivedcache, raw остаётсяистиной.
Часовойbounded audit до512raw captures вращаетсяпоleast-recently-verified, дополнительно
проверяетversion/first membership. Полнаяraw/indexсверкаобязательнапередactivation.

Activation gate перед post-cooldown GET: все24pool dispositions доступны (включая
наследованнуюquarantine); ≥6fresh pools и≥4новыхpool сvalid decodedbars наqualification
cutoff, raw/indexexact, migrationfixtureexact. Этоoperational minimumдляdemo, неMLreadiness.

### Первая проверка и завершение того же бюджета

На 24 attempts: 23 HTTP success, один URLError без HTTP status; не новый 429.
Полная raw/index сверка exact: 18 595 bars, 24 observed pools. На первом cutoff
последние свечи вышли за gate свежести 300 s: activation отклонена, результат
`qualification.json` сохранён. Четвёртый worker не послал GET во время cooldown.
После штатного cooldown используются только оставшиеся восемь GET исходного
лимита 32, с теми же gate/скоростью/бюджетами; второй результат сохраняется отдельно
в `qualification-v2.json`. Это повторная проверка operational recovery, не новый trial
качества модели. При отрицательном результате дальнейших qualification GET нет.
