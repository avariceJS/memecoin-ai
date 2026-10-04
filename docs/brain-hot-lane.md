# B09 — восстановление paper через приоритетный hot lane

## Frozen plan до новых запросов, 2026-10-04

B08 принят с отрицательной activation. Его STOP, negative qualification и два provider429
сохраняются. Цель B09 — вернуть честный paper loop и оставить ограниченный72h rollout,
если новое расписание выдержит несколько полных циклов. Недельный/месячный uptime не
объявлять без календарного evidence. F01/D43/holdout и реальные execution не затрагивать.

Universe прежние24, без discovery. Hot cohort — первые шесть active по убыванию liquidity
из immutable B08 admission (tie по адресу); не по returns/PnL. Cohort фиксируется до GET.
Остальные18 сохраняются в denominator и background collection, quarantine наследуется.
Это liquidity-biased operational subset, не репрезентативный широкий рынок.

Hot due90s, global spacing≥12s (≤5/min), одна сеть/один budget coordinator. Background
не чаще1/min и только при запасе≥35s до следующего hot due; чередовать cold prospective
и history. Cold due1800s, history прежние≤30pages/pool/90days/1000rows, cursor/pagecounts
наследовать изB08; raw reuse без redownload. Hot requests max100candles. Невозможность
собрать данные не превращать в synthetic candles или прошлую доступность.

Provider-wide cooldown/rate-events наследовать; третий429/24h→STOP,6 consecutive transport
failures→STOP, auth/integrity→STOP. Pool-local schema/quarantine не останавливает здоровых.
Один bounded worker≤8GET/240s, HTTP timeout20s. Qualification≤120GET/25min, без activation
дальнейший runtime не может тратить network. Опубликованная activation должна связывать
config/runtime/qualification hashes. Старые roots/jobs не переписывать.

Qualification:1200s наблюдения, первые180s warmup; sampling каждые≈10s по actual cutoff,
без заполнения пропусков. Gate: минимум90 recorded samples послеwarmup, span≥900s,
≥1fresh hot pool не менее80% samples, ≥4fresh не менее60%; каждыйhot pool дал≥3разных
новых closed candles посленачала, hot inter-request gaps≤180s, HTTPsuccess≥95%, безSTOP,
raw/indexexact. Fresh означаетactive и0≤now−close≤300s, как вpaper. Publish полный
sample series, denominator6/24, gaps, failures и оба расписания. Это новая operational
проверка cadence, не ретроспективная правка B08 gate и не model-selection trial.

Background обязан реально получить отдельные requests; history starvation видна отдельно,
не скрывается ростом live captures. При отрицательном gate не продолжать сетевую qualification.
При положительном gate exact prefix/cash/positions migration B07→B09, сохранить champion/
candidate versions и policy; source/paper/train/UI отдельные frozen jobs. Перед cutover
повторить current freshness и binding checks. На rollout72h максимум25000 новыхGET,
background≤4000, raw8GiB/freefloor4GiB, paid0. Qualification входит в общийbudget.

Daily training прежний D44/D47,≥24h+100new mature samples,4fits≤600s, максимум4runs включая
перенесённыйB07run; прежниеfeatures/targets/selection. Не переключать champion автоматически.
Новые outcomes толькопослеmaturity, model behavior/forecastloss раздельно. Live visibility:
hot/cold roles, provider cooldown, actualsource heartbeat и freshness, история paper trades,
дневныеmodelmetrics. Исследование/rawPumpSwap не блокирует source worker.

Проверки: fixtures hot priority/no starvation/cooldown/reservation/restart/bindings, полный
raw/index и paper replay, snapshot→raw/forecast joins, installedwheel и UI. Автоматический
soak status на72h должен измерять реальные часы, gaps/coverage/worker failures; первые
20минут не выдавать за сутки. Первые сутки требуют последующего review артефактов.

### Уточнение до первого GET: слот для background

Проверка worst-case расписания обнаружила, что6×12s при hot90s оставляют только18s,
меньше35s guard для background: history могла голодать бесконечно. До acquisition
hot interval изменён на120s (3hotGET/min). Это оставляет48s после earliest next start,
достаточно одного bounded background request. Global5/min, guard35s и maxhotgap180s
не меняются; все дальнейшие thresholds прежние. Источник не опрашивался до этой поправки.
