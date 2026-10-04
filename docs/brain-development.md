# Brain Development B01: независимый exploratory track

## Решение и стартовый план — 2026-10-04, до сбора и обучения

Прямое указание пользователя открывает historical backfill, расширение universe,
continuous corpus, регулярное supervised training и shadow evaluation. Прежний запрет
market/model-development training заменён для этого track. F01 readiness больше не
является его gate. Frozen F01/D43, reserved holdout и их правила не меняются; результаты
Brain нельзя представлять confirmatory F01 или доказательством edge. F01 calendar
activation остаётся самостоятельным решением, не критическим путём Brain.

Первая вертикаль Brain v1 — распределение будущих returns наблюдаемой цены, baselines
и temporal candidates; не portfolio/trading policy. Reuse D44 PyTorch encoders/losses,
D47 immutable ledger/checkpoint principles. Другие targets/encoders экспериментальны.
Historical OHLCV не переделывать в fake CPMM execution labels. Протокол исполнения
CPMM не расширяется поддержкой market-data bars других пулов.

1. Проверить официальные public API и сделать bounded keyless discovery/history probe.
   Public pool ranking отражает текущую выборку/survivorship, не universe прошлого.
   До12новых Solana pools, F01 fixed3pools исключены из independent collection. Freeze
   admission time и pool/mint metadata; изменения состава — новая universe revision.
2. Historical lane: до3страниц×1000закрытых5min bars/pool, реальные retrieval timestamps
   и response bytes immutable. Bar event time отдельно; availability прошлого неизвестна.
   Chronological purged retrospective evaluation явно hindsight/model-development;
   результаты не online claims. Только доступные к training cutoff raw используются.
3. Prospective lane: опрос каждые5min, first-observed closed bar versions, без overwrite
   исправленных bars. Predictions пишутся до target availability, поздние данные не
   создают старые predictions. Gaps/censoring сохраняются. Shadow — прогнозы и proper
   losses, без simulated orders/paper PnL, подписи/торговли.
4. Начальный supervised protocol: empirical baseline, linear и GRU(width16,context8),
   seeds7/19, horizons5/15min, до10epochs/candidate, train/validation/test60/20/20 по
   глобальному event time с group maturity purge. Все pools делят global cutoffs;
   scaler train-only, выбор по validation, test diagnostic exploratory. Model registry
   immutable; champion автоматически не становится trade policy.
5. Отдельный Brain runtime до72hours с max12000public GET, interval≥3s/request,15s HTTP
   timeout, bounded responses2MiB, disk floor4GiB и Brain storage cap2GiB. Paid budget0,
   никаких credentials/upgrades/new paid sources. HTTP failure history immutable;
   no immediate retry,2consecutive failed acquisition cycles→stop. Не обходить auth/429.
   First source failure closes that source probe; fallback только documented public source.
6. Recurring supervised cycles не чаще6h, только при новых закрытых данных; fit budget
   ≤4neural fits/cycle,≤12cycles/72h, wall900s/cycle, threads1/CPU initial. Shared host
   limits, STOP/expiry/code pins, crash/no duplicate cycles и source/label leakage tests.
7. Реальный backfill→training→label-free inference→prospective shadow path, fixtures и
   failure tests до фонового запуска. Runtime из frozen snapshot, отдельный job/root,
   D43 не переустанавливать. Если public source недоступен, сохранить точный access
   blocker и продолжать независимую часть, не выдавать fixture за реальные данные.

Цель на ближайшие дни — первая содержательная exploratory Brain v1 с расширенным
corpus и будущими shadow observations. Срок не гарантирует качество или edge; число
необходимых режимов/токенов устанавливается evidence, не F01 календарём.
