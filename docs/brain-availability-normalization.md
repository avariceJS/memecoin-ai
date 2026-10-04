# B04 — доступность источника и train-only normalization

## План до новых market GET и fits — 2026-10-04

B03 принят. Его отрицательное activation решение и все STOP остаются неизменными.
B04 разделяет две независимые задачи: корректно учитывать отсутствие данных в
collector-only режиме и проверить чувствительность прежних моделей к хвостам features.
D43/F01/holdout, CPMM, raw, targets, model000000 и champion не меняются.

Offline audit использует sealed B03 snapshot01 и raw manifests: происхождение самых
больших |z|, OHLCV constraints, cross-capture revisions, duplicate conflicts, gaps,
receipt/event times. Никакие extreme bars не удаляются на основании полученного loss.
Официальный GeckoTerminal swagger: beta, cache1min, около10calls/min, параметр
include_empty_intervals включает интервалы без trade data. Из отсутствия bars нельзя
вывести доказанное отсутствие торгов. Данные provider не становятся coherent execution.

Normalization experiment: неизменный B03 dataset и два full-train chronological folds,
эмпирический baseline, linear/GRU, seeds7/19, width16/context8,10epochs/CPU1. Два arms:
identity (контроль) и winsorization каждого непрерывного канала по train-only q01/q99,
затем прежний train mean/std scaler. Binary channels past_positive_fraction/zero_volume
не winsorize; None/masks/age/targets не менять. Degenerate quantile span: оставить канал
как есть и явно записать. Нет подбора percentile/seed/feature subset после результатов.
16 neural fits, общий hard600s; per-fit200s, no retry after failed fit. Control checkpoints
сравнить с B03 для проверки эквивалентности. Selection по validation; только если
среднее relative improvement≥1% и ни один fold validation не хуже >1%, arm заслуживает
дальнейшего будущего shadow experiment. Сейчас никакого promotion независимо от loss.
Evaluation диагностический, уже exposed B03; paired1h bootstrap и horizons/seeds отдельно.
Transform сохранять и проверять при inference, fit только по unique train frames.

Collector-only candidate — новый root/runtime/решение, не исправление B03 результата.
Прежние 12 pools в denominator, original expiry2026-10-07T00:30:23.405597Z,
12000 shared GET ceiling (135 уже debited), paid0, sequential≥12s/GET,240s/cycle,
5min due-only, no catchup, schema whole-response quarantine, q≥4 global STOP,
auth/429 immediate STOP,2 consecutive transport failures STOP, hash/clock fatal.
Импортировать quarantine state и пробные бюджеты/next_due, не обнулять их при restart.
Training полностью отключён. Старый paired empirical/linear/GRU shadow допускается
только по прежним actual availability и ≤300s freshness, без использования B04 fits.

Новый operational criterion проверяет пригодность к записи observations/missingness,
а не достаточность fresh forecast universe: два bounded due cycles, все12dispositions
учтены, HTTP/semantic/integrity errors классифицированы, valid captures сохраняются,
не менее одного valid capture, global stop не сработал. Fresh counts публикуются, но
не являются activation gate этого collector-only режима. Это новая ограниченная цель,
не снижение B03≥9 для допуска training. При6 последовательных cycles без продвижения
nominal close хотя бы одного valid pool — STOP (причина unknown, не «рынок неактивен»).
Crash/failed cycle не сбрасывает stagnation/failure debt. Нет скрытых exclusion.

Новых market GET в qualification≤24 (два cycles), после ошибки нет третьей попытки
candidate. До GET — fixtures/recorded audit и точный source/config/import binding.
После двух успешных cycles — отдельное review/activation artifact с exact cycle seals,
runtime/plist/expiry/budget pins, затем разрешён install отдельного LaunchAgent в рамках
автономного Brain Development. Если критерии не пройдут — STOP, без нового candidate
в этом этапе. Дополнительные diagnostic market endpoints в B04 не требуются.

Validation: train-only leakage/missingness/transform replay, record lineage/duplicates,
scheduler/stagnation/failure/probe inheritance, fixtures, bounded live, installed wheel
inference/scoring reproduction. Memory Bank и Git обновить; raw/weights вне Git.
