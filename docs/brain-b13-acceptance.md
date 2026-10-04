# Черновик приёмки B13

Это отчёт по файлам на явный cutoff, не решение о приёмке продукта. Edge, promotion,
подтверждение F01 и калиброванное исполнение не установлены. Наблюдаемые часы — присутствие
по heartbeat, не uptime.

Cutoff отчёта: `2026-10-04 21:41:39.863307 UTC`, `cutoff_ns=1791150099863307000`.
Машинный снимок: `work/brain-b13/operator-report03.json` и
`work/brain-b13/operator-report03.md`, схема `operator-evidence-v1`, `ok=true`.
Чтение было `mode=ro` плюс `backup`. `operator.sqlite` не изменил mtime (`1791150089`).
`operator-report-preview01` и `operator-report-acceptance04` не перезаписывались.
Сеть, launchctl и POST в этом черновике не вызывались.

## Источник после audit-deadline

Старый `work/brain-b09/source01/STOP` сохранён. Его sha256
`b6b5968fce4377324a89b6671b694b2a1ee806d51be6039593e70f500194674b`.
Инцидент `incident-1791145945693553000.json`: категория `source_stop_or_deadline`,
exit −9, elapsed `600.0077585829422` с. Это неизменный исход audit-deadline.

Ремонт записан в `work/brain-b13/source-repaired01/migration.json`:
`kind=source_audit_deadline_repair`, `lineage=technical_repair_genesis`.
`old_attempts=new_attempts=1417`, `old_chain_rewritten=false`,
`qualification_rerun=false`, `expiry_unchanged=true`, `budgets_unchanged=true`.
Бюджет источника: `max_requests=25000`, `max_background_requests=4000`,
`hot_interval_s=120`, `cold_interval_s=1800`. Срок источника
`2026-10-07 12:23:51.922091 UTC` (`expires_ns=1791375831922091000`).
Старый config sha `9fc4e286db9420b6855f63d607aac663d4c719a6174ae470e24f6692d907b20c`.
Новый config sha `d235db8bbcd41a71fe0baa18779680add90dcded45aefbb3c2654013376e7a4e`.
Наследованная квалификация
`8e515510c47c1adb39e826616f2ae28da469c0691b33ba9f9de0b40a89e1202f`.
Логический индекс `9d643ba83d0fb12f1a89e343886d71c19194eed30a3f4953ce96bb454bad9b47`;
добавлен только индекс `versions_pin_open`. Счётчики индекса: audited 1852, captures 1874,
firsts 95204, versions 264529. Raw audit на момент ремонта: bars 95204, captures 1865,
`exact=true`, fingerprint
`cc8f1a2a303aae8d3018aac04093feb63045bd9f9a494b181fb323385c1e0ad7`.
Префикс сервиса `ai.memecoin.brain.b09r1.`. Pin deployment
`afc1c63e3d60655f857d157b52dbe1b0a4fbdbdc1561dec85fe88c1fcd122ac8`.

`health.json` на `at_ns=1791149165762696000` показывает attempts 1472 и failures 0.
Это позже ремонта и раньше cutoff отчёта. Поле `additional_requests=30000` в конфиге
оператора — отдельный конверт оператора, не изменение бюджета источника.

## B12: ложная порча и тот же экзамен

`docs/brain-autonomous-operator.md` фиксирует STOP `2026-10-04 19:41 UTC` как
`index_membership_corruption`: несогласованные SELECT в WAL без общей транзакции.
Этот черновик raw audit не повторял.

Конфиги `work/brain-b12/paper01`, `work/brain-b13/b12-recovered01` и
`work/brain-b13/b12-repaired02` совпадают по экзамену:
активация и `exam_start` `2026-10-04 18:28:50.447238 UTC`,
истечение `2026-10-05 18:28:50.447238 UTC` (`expires_ns=1791224930447238000`),
`training_max_runs=0`, те же три frozen sha
`7fcca7df1c3bb992b00fe355ef538d94668786019152c461f878208b8a6d4bf0`,
`c9c40ce4c52e85707287879fa3423b9e5ad6224a02d16e2e29c67be1e7e1803f`,
`ccab396a2660fd881e92ac5b9b223aba5e11402c089f77aaa120dc73b89c5408`.
Сменились порт и префикс: исходный STOP `8791` / `ai.memecoin.brain.b12.`;
recovered `8793` / `b12r1.`; repaired02 `8795` / `b12r2.`.
Pin repaired02 `a675353715753cb73453e1af17a2707b81e3211d49824efbb607eea3017594e7`.
`work/brain-b13/paper-repair-acceptance01.json` для этой миграции: ledger head
`44f73b92bf4e2b4a9df4765776eb20b49d1275a12c83ade03cae650382703281`, state sha
`281374b73af3a2457ad58b8665ddbfc5dac8e7c038ead42e428623ecd0261d04`, 67 ticks,
realized `0` по опубликованным книгам, `edge_established=false`.

## B10: неудачная копия и повтор той же книги

`work/brain-b13/b10-repaired01` остановлен. `paper-health.json`: `worker_failed`, exit 1,
`at_ns=1791147828648602000`. Лог paper-worker указывает `artifact_inventory_mismatch`.
Единственный лишний отсутствующий путь относительно исходного дерева и repaired02 —
`training/000000/model/ledger/.lock`. Файла `sealed.lock` в дереве нет.
Train-инцидент repaired01 — отдельный `stop_requested`, exit −9.

`work/brain-b13/b10-repaired02` — повторная копия той же книги. `migration.json`:
prefix events 458, prefix state sha
`b96d60f4abc44485ae4e698ad5f9c768148fe07620a41d8e413b1a6441b8f67f`,
`automatic_activation=false`, `no_cash_reset=true`, `no_synthetic_exit=true`.
`b10-repair-acceptance02.json`: ledger head
`2c5c459659cebc47d3f227aa4225eab09cbca2d716852a82b2cd97ab2555e868`,
ticks 458, books 6, `all_training_seals_verified=true`,
`edge_established=false`, `calibrated_execution=false`, `no_real_execution=true`.
Порт `8796`, префикс `ai.memecoin.brain.b10r2.`, pin
`4b6f99b200ed4b62742f742eb62d12a0accb8e59a319c6042988de80f2185eac`.
Срок тот же, что у источника: `1791375831922091000`.
Последний прочитанный `paper-health.json`: `completed`, exit 0,
`at_ns=1791149101149162000`.

## Обучение operator04

Корень `work/brain-b13/operator04`, порт `8792`, `paid_budget_usd=0`,
`real_execution_enabled=false`. Срок продукта
`2026-10-11 20:31:13.058527 UTC` (`expires_ns=1791750673058527000`).
На cutoff журнал в режиме `paper`, фаза `paper`, revision 7.
Активный экзамен `job_b7edd93ea86173f9`, выбранный кандидат `job_fe34d70ab6cea0bc`.
Опубликованные задания: collect `job_fd4fa808065ceeb2`, snapshot `job_4ed49dcc84dfb59d`,
train `job_fe34d70ab6cea0bc`, paper `job_b7edd93ea86173f9`. Все четыре `completed` /
`published`. Следующий refit закрыт интервалом: `gate_interval` не пройден.
Квота `runs_used=1` из `runs_max=6`.

Датасет `job_4ed49dcc84dfb59d`, recipe `B13-pool-day-v1`, recipe sha
`60d3206389785cef30211ba610064dd71742f4e17d82e694af272dc1b8bc3f66`.
`dataset_sha=356a202be87a81c3753fe9141948be3a3f5d982c6190c821e7d7efbe86fd2191`,
manifest sha `cb6646156ed7f82fe8ffb911f6b0163f875afad960f08c9e43b1f33a5449a0b6`.
`historical_test_independent=false`, `unseen_test_pools=[]`, `automatic_promotion=false`,
seeds 7 и 19, 1000 updates. Retained: train 92 дня / 24 пула / 48000 решений;
validation 1 день / 14 пулов / 451 решение; test 1 день / 14 пулов / 352 решения.
Raw audit архива на snapshot: bars 95273, captures 1885, `exact=true`, fingerprint
`763fbe52b4e8c5a5039838df682d667ca84ad6757acf4b8078290e4cae78a491`.

Кандидат `job_fe34d70ab6cea0bc`, `promoted=false`, `new_fits=4`, выбран `gru`.
Model sha `c895590ac4726af6fcc8e46b870b6f08c8fdd7caf1fe50dbca018b16a8e77a76`,
manifest sha `04f8ec23dac55f2b288fdeb9403bf252c4bbd45dc7b9c023d79752a0c595d9ea`.
Метрика выбора — validation `pool_macro_pinball`: empirical `0.0195115306713587`,
GRU `0.018499755708238176`, linear `0.02314365725305648`. На reused descriptive test
та же метрика: empirical `0.06945625829193938`, GRU `0.0698125353649828`,
linear `0.07521797695027428`. GRU хуже empirical. `edge_established=false`,
`confirmatory=false`. Промоций в отчёте `[]`. Empirical champion остаётся контролем,
sha `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`.

## Будущий экзамен и виртуальные книги

Экзамен `job_b7edd93ea86173f9` активирован `2026-10-04 21:13:36.834270 UTC`
(`activation_ns=1791148416834270000`) и истекает `2026-10-05 21:13:36.834270 UTC`.
`activation.json`: `mode=frozen_future_exam`, `promotion=false`, `no_backfill=true`.
Exam manifest sha `7b23c8b16296ab908a5d3aaed31456238d03f45b2e4839fc6dab858e57e18d93`.
`real_execution_enabled=false`.

Протокол `docs/brain-autonomous-operator.md` задаёт отдельную fixed 15-minute price proxy;
paper PnL не обновляет веса. Поле `holding_minutes` в конфиге этого экзамена отсутствует.
Dashboard помечает режим как virtual price proxy и явно не калиброванное исполнение.
Шесть книг независимы: champion и кандидат, у каждого empirical, GRU и linear.
На cutoff у каждой книги cash `1000`, equity `1000`, realized `0`, позиции 0, pending 0,
закрытых сделок 0, `mark_stale=false`. Сумма equity `6000` в отчёте — сумма шести книг,
не один счёт. Dashboard, попавший в срез, `generated_ns=1791150091007739000`.
Более поздний dashboard после cutoff в этот отчёт не входит.

Действия на cutoff, источник `model_behavior`: WAIT 138, ENTER 0, HOLD 0, EXIT 0.
Стадия всех 138 записей — `decision`. Intent `enter_next_observed_close` в этом срезе нет.
Fills: entries 0, exits 0. Это CASH/WAIT, не сделки.

`forecasts.status=measured`. Каталог `candidate-shadows/<model_sha>/shadow` прочитан;
дублей с legacy `exam/shadow` нет (`duplicates_folded=0`). `BAR = 300` взят из pinned
runtime, поэтому горизонт 1 — 5 минут, горизонт 3 — 15 минут. `loss_combined=false`:
суммы pinball остаются у своей модели. Это описательные суммы persisted
`candidate_metrics.pinball`, не оценка edge и не доверительный интервал.

| Горизонт | Predictions | Outcomes | Pending | Scored |
| --- | ---: | ---: | ---: | ---: |
| 1 / 5 мин | 46 | 38 | 8 | 38 |
| 3 / 15 мин | 46 | 22 | 24 | 22 |

Всего predictions 92, outcomes 60, pending 32, scored 60, censored 0, invalid 0.
Независимых групп `(exam, model_sha, pool, event_close)` 46: fully scored 22,
с pending 24. Исключений по activation, cutoff и `prediction_sha` нет.

Champion `9ea4a6bd7977fb66fe4a18eba54ed7eaed0afd65810ec99c75aec3e1443e3920`,
5 мин, n=19: empirical `0.0268713190944132695`, GRU `0.0901903339312489180`,
linear `0.339117442940243518`. 15 мин, n=11: empirical `0.0331215655358103265`,
GRU `0.0192873549131759447`, linear `0.0521983955338170689`.

Кандидат `c895590ac4726af6fcc8e46b870b6f08c8fdd7caf1fe50dbca018b16a8e77a76`,
5 мин, n=19: empirical `0.0618526616411181266`, GRU `0.0510027806877612469`,
linear `0.1918116751216929719`. 15 мин, n=11: empirical `0.0655543423841276875`,
GRU `0.0261583494217872569`, linear `0.0207283790059316643`.

Pending горизонты не заполнены нулевым loss. 24 группы ещё ждут 15-минутный outcome.

Наблюдение оператора на cutoff: elapsed `0.539704154` ч, присутствие
`0.536825054` ч, столько же observer и healthy service. Разрывов длиннее 90 с в
записанных heartbeat нет. `uptime_claim=false`. Это окно от первой отметки, не неделя
продукта и не 100% uptime. Telemetry rows 32. Рост покрытия: bars 95261 → 95379,
пулы 24 → 24.

## Синтетические проверки интерфейса

`work/brain-b13/delegation/browser-fixture-acceptance.json` помечен
`data_kind=synthetic`, `mutation_scope=synthetic_fixture_only`. Эти фикстуры и
скриншоты `browser-fixture-*` проверяют UI на синтетике. Они не являются будущим
экзаменом, книгой `job_b7edd93ea86173f9` и не доказывают сделки.

## Что этот черновик не закрывает

Развёртывание LaunchAgent, полный `make check` / e2e и коммиты остаются за родителем.
Список прежних пробелов: `work/brain-b13/delegation/evidence-final-review.md`.
Пункт про слепой `exam/shadow` закрыт срезом `operator-report03`: отчёт читает
`candidate-shadows` и legacy shadow. Остальные пункты того списка этот черновик не закрывает.
