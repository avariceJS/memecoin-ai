# Data-first: bounded development acquisition и clipping audit

Дата: 2026-10-07. Приоритет пользователя — реальные широкие данные, не следующий sweep.
Решение: сначала populate development, clipping исследовать после qualification. Production,
champion, AUTO, training recipe и promotion gates не менять.

## Фактический результат после восстановления связи

SSH на `172.20.10.11` восстановлен, прежний host key подтверждён. Выполнен ограниченный
data-only сбор в двух новых research roots, без изменения production. Это **частичный
результат, не готовый широкий benchmark**. [Проверенные receipts](evidence/brain-development-acquisition-2026-10-07.json).

- До первого GET sealed 20 mint: по 4 на май-сентябрь 2026, 40 окон (birth+3d/+10d).
  SHA-ranking, dedup по mint, исключены 162 pool IDs и 179 mint/account IDs. Все pool IDs
  трёх reference datasets (champion, latest AUTO, ablation snapshot) покрыты исключениями.
  Protected outcomes не читались; использованы опубликованные D40 account IDs и metadata
  прежних enrollment plans. Возраст здесь от pool birth, не доказанный возраст самого mint.
- Фактически 9/40 GET, все HTTP200, 81 670 bytes, 5 mint затронуты, 31 окно не запрошено.
  После дедупликации 411 свечей, revisions0. Только 38 полноценных origins на 2 mint,
  3 неперекрывающихся dependency windows, 2 UTC-дня. Весь support в age<1d;
  age1-3d и7-10d имеют нулевой support. Это не 38 независимых наблюдений.
- `campaign01`: 5 успешных GET, затем STOP общего source guard. Первичный receipt
  `source-repaired02/requests/006225`: URLError/TimeoutError, HTTP status отсутствует,
  не429. Source самостоятельно восстановился после своего30s cooldown.
- После проверки разрешена ровно одна continuation незапрошенного suffix, с debit5,
  тем же roster и expiry. `campaign02`: ещё4 успешных GET, затем
  `unexpected_project_consumer`. Причина второго STOP **не установлена точнее**:
  старый guard не записал PID/argv fingerprint. Другой provider writer не доказан;
  ложное срабатывание на краткоживущий read-only helper также возможно.
- Третьей попытки нет. Оба STOP и sealed artifacts сохранены. Последующая локальная
  версия инструмента сохраняет PID и hash неизвестного argv без возможных секретов;
  запрет неизвестного consumer не снят. Этот collector пока не qualified для unattended run.
- На Mac2 все raw seals и9 результатов independently reproduced. Новых fits0,
  predictions0, old-test scoring0. Существующие research run manifests также повторно verified.

Owner read-only observations во время/после: revision33, paused/paused, queued/running0,
champion `9ea4a6bd...3920`, runtime08, source config hash неизменен; source STOP отсутствует,
после сбора failures0. Control Center сообщает `adapter_unavailable` и fallback expiry0:
его `expired` нельзя трактовать как доказательство истечения реального source config.
Фактический source expiry12:23:51UTC; последняя managed exam зарезервирована до этого же
срока. Поэтому её текущие24 seen markets не раскрывались для нового development;
seen-temporal/prospective часть здесь не получена. AUTO не возобновлялся и не исправлялся.

Результат даёт usable cold-transfer prefix, но не основание сравнивать clipping/LR recipes.
Следующее действие до новых GET: read-only qualification списка provider consumers с
диагностикой процесса и отдельный research envelope после закрытия exam/source lease.
Не продлевать production lease и не использовать exam-поток как development задним числом.
Следующий ML опыт остаётся preregistered2x2 LR schedule x clipping после реального support.

Проверки: полный `make check` вне sandbox1018passed/12optional skips; первоначальный
sandbox run1016passed/1failed из-за запрета killpg synthetic deadline-test. После добавления
fingerprint/report/guard tests40focused PASS; `make smoke` PASS. Torch/browser optional
tests наMac2 пропущены; новых ML fits не выполнялось.

## История блокера до нового IP

Mac1 SSH172.20.10.11:22 и192.168.31.66:22 дали timeout; известное имя
MacBook-Pro-Aleksandr.local не разрешилось. Host key checks не отключались, неизвестные
адреса не сканировались. Birth catalogue, актуальная exposure/attempt inventory и общий
provider state остаются наMac1; наMac2 доступны только прежние sealed experiment outputs.
Текущий runtime/status заново НЕ установлен. Последнее наблюдение revision33paused — история,
не подтверждение текущего состояния. Запрошен текущийIP/доступность у пользователя.

Новых GET0, новых train runs0, новых dataset rows0. Не обходим отсутствие budget state
запуском второго независимого provider worker. Старый admission worker требует active
control source и fixedbirth+3day contract; отключать его STOP/expiry или подменять его
role для нового benchmark нельзя. Ни campaign, ни broad benchmark ещё не запущены.

## Исходный bounded design

Этот design был записан до восстановления связи. Cold-mint roster/reservation теперь
sealed, его фактическое исполнение описано выше. Seen-temporal часть пока не выполнена.

1. Новый отдельный root `work/brain-development-20261007/campaign01`, research-only.
   Owner остаётся paused/без config writes. До network — inventory всех source consumers,
   remaining budget/cooldowns и atomic campaign reservation на owner research ledger.
   Если нельзя доказать shared-provider envelope без production mutation — no start.
2. Cold-mint development:20ранее не использованных mint,4накаждый birthmonthMay–Sep2026,
   deterministic SHA order с salt `brain-development-v1-20261007`, round-robinmonths.
   Исключения: union train/scaler mint membership всех сравниваемых models, previously
   attempted/отложенные test mints, F01/D43/reserved denylist и pool aliases. Нельзя
   просто вычесть86pool IDs и объявить оставшиеся unseen. Dedup наmintдоранжирования.
3. По2GETнаmint: один доbirth+3days, второй доbirth+10days; limit1000/5minute/USD/base,
   include_empty_intervals=false. Первый даёт age<1d/1–3d, второй целевой age7–10d.
   Raw response сохраняется целиком; dependency warmup до начала age-window не считать
   target cohort. Missing/dead/404 не заменять другими mint после outcomes. Query не
   гарантирует плотных candles/contiguous labels. ЕслиMonth не имеет4admissible metadata
   mints, фиксировать shortfall, не добирать по observedprice/support.
4. Seen temporal development: до8knownmints из frozen operational roster, SHA-ranked,
   новый origin interval строго ПОСЛЕ union ранее exposed train/eval ranges плюс full
   feature purge. Сначала переиспользовать read-only consistent source captures, если
   они действительно новые; до8дополнительныхGET при отсутствии coverage. История из
   старого test не считается новым временным support. Один GET/market не обещает7days.
5. Hard envelope:<=48GET (40cold +<=8seen), spacing>=30s, perrequest<=30s/2MiB,
   campaignwall<=45min, rawbytes<=96MiB, storage<=192MiB, freefloor4GiB, paidbudget0.
   Эти bounds не заменяют более строгий общий rate/monthly budget. Перед каждымGET
   проверять свежий shared-provider guard. Любой401/403/429/schema/integrity error STOP,
   no retries; transportfailure STOP с сохранённым partialartifact.404/empty — negative
   admission record, не повод скрытно заменитьpool. Expiry не продлевать.
6. Seal roster/code/sourcepins/bounds/exclusions/reservations ДОrequests. Publish new
   immutable captures and development-only dataset отдельно, без compile_corpus(role=train)
   и без AUTO refs. Фактические original availability сохраняются, historical online
   availability не изобретается. Candidate inference только после полного data freeze.

Это получение initial broad development basis, не гарантированное выполнение support
floors. Расход дополнительнойquota или новыйcohort возможны только новым preregistered
campaign после coverage review без model scores. Ни sparse exclusions, ни floor lowering
для объявления benchmark готовым не допускаются.

## Два разных development tracks

Уточнение перед continuation 2026-10-07: campaign01 завершён после пяти успешных GET
из-за `URLError/TimeoutError` общего source. Research HTTP failures не было. Source сам
восстановился после своего cooldown, что проверено по journal/receipt. Разрешена одна
отдельно проверенная continuation только незапрошенного suffix: те же 20 mint/windows,
исходный expiry, суммарно <=40 GET с переносом debit5. Старый STOP/seal неизменны;
повтор failed/partial research request запрещён, новый cohort не добавляется.
Это не автоматический retry: решение принято после проверки причины и recovery evidence.

Cold-mint historical transfer может иметь event dates раньшеtraincutoff, но mint/inputs
не были в train/scaler. Это отдельный transfer diagnostic наpast events; модель могла
учиться на более поздних regimes другихmarkets. Поэтому это НЕ chronological future test
и НЕ замена temporalAUTOtrack. Предыдущий benchmark-v1 lifecycle уточняется этой явной
разницей: будущие development/calibration/prospective windows остаются отдельными.
Сам old+new benchmark пока support_ready=false; completed historical hydration не снимет
future-readiness gate. Полноценную prospective basis нельзя получить за один короткий GET.

## Что установлено о clipping

Из sealed LRrun `3d81f4eb...7415a`: все8forks имеют clip_fraction=1.0 за1000updates.
CurrentLR0.001 meanpreclipnorm55.28–95.93, lowLR0.0001 —33.93–37.35, threshold1.
Это global norm на уже IQR-normalized loss, а не изменение individualweights и не norm
фактического AdamW parameter step. Savedreceipts не содержат per-step normdistribution,
direction или update/weight ratios. Median clipfactor/percentiles из mean/max восстановить
нельзя; значение1/mean(norm) не равно mean(1/norm).

Из кода training.py: L=pinball/s, g=grad(pinball), s=trainIQR>0. При активномglobalclip:
`clip(g/s,C) = (g/s)*C/(||g||/s + epsilon) ~= C*g/||g||`.
Следовательно, постоянныйloss scale практически сокращается на насыщенных updates;
это НЕ доказательство clipping harmless: norm minibatches всё ещё влияет на их relative
contribution в Adam moments. Globalclip сохраняет направление текущегоgradient, но меняет
его batch-dependent magnitude. Поэтому это не то же самое, что просто уменьшитьLR.

AdamW использует первые/вторые moments. Constant rescaling всей историиgradient при
согласованных moments почти сокращается в m/sqrt(v), но time-varyingnormscaling в общем
случае не сокращается. При сменеthreshold изexistingcheckpoint неизменные moments несут
старую шкалу: initialtransient нельзя автоматически назвать улучшением clipping regime.
Это вывод из формул, не результат новогоGRUexperiment. Проверено на synthetic2Dstream,
не считать его evidence улучшения реальныхforecasts.
Primary references: [PyTorch global clipping](https://docs.pytorch.org/docs/2.14/generated/torch.nn.utils.clip_grad_norm_.html),
[AdamW algorithm](https://docs.pytorch.org/docs/2.14/generated/torch.optim.AdamW.html).

## Самый информативный optimization experiment после data readiness

Выбираю2×2: LRconstant/late-decay ×clipcurrent/rare-safetyclip, seeds7/19, прежнийGRU/head,
один фиксированный sampler objective, одинаковые init/minibatches/budget. Это8fits,
не sweepbackbones. Rare-safetythreshold задаётся доdevscores по отдельному train-only
gradient calibration probe; его изменение не подбирать поdeclared100%частоте илиtest.
Точныйthreshold/decaypoint/budget ещё не preregistered, запуск НЕ проведён.

Для чистойинтерпретации — matchedfromscratch arms с одинаковымиzeroAdamstates, а не
необъяснённая сменаclipscale встарыхmoments. Можно reuseexact контроль после проверки
эквивалентности. Логировать unclipped/postclip norms, clipfactor quantiles, clippingbylayer,
parameterstep/weight ratio, cosine consecutive updates и volatility/sparse-cohort batchshare.
Primary — fixedterminal и стабильностьпоcheckpointgrid на новомdevelopment; perseed/horizon/
mint-age calibration. Старыйtest неscore, невыбирать beststep заднимчислом.
Это отделитLR-effect, clipping-effect и ихinteraction; послеdevselection одна recipe
идётвотдельныйcalibration/futureexam. Ожидаемо короткийcompute по прошлымизмерениям,
но стоимость8новыхfits сtelemetry здесь не измерена. Backbone/production пока прежние.

## Артефакты

`tools/brain_clipping_audit.py` повторно проверяет seal исходногоLRrun и извлекает receipts,
плюс выполняет явноsyntheticalgebra check безTorch/providercalls/training.
[Machine-readable audit](evidence/brain-clipping-audit-2026-10-07.json).
Программа не меняетsourceartifacts. Collector/report:
`tools/brain_development_collect.py`; artifacts на обоих Mac в
`work/brain-development-20261007/campaign01` и `campaign02`, offline support наMac2 в
`work/brain-development-20261007/support01`. Seals/code pins приведены в evidence JSON.
Raw captures не добавлены вGit. Support dataset имеет research-only role и не подключён
к training/AUTO; retrospective receipts не переопределены как online availability.
