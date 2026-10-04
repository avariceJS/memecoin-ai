# B08 — семидневный paper Brain и инкрементальная история

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
