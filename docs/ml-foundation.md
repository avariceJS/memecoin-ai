# D44: ML/training foundation независимо от D43

2026-10-03. Пользователь разрешил реализацию ML-основы и smoke/integration обучение,
не признавая малый corpus достаточным. D43 frozen runtime/job/окна не менять.
Это снимает прежний запрет на любые training runs только для инженерных опытов;
настоящая market evaluation, edge, RL, paper/live и торговля не начинаются.

## План до экспериментов

Отдельный optional пакет memecoin_ml и отдельное окружение work/ml-venv. Collector
сохраняет stdlib runtime. Verified sealed campaigns → immutable dataset с явными
history availability/missing masks и условными target masks. Истории не пересекают
segment/gap, metadata/IDs/будущие outcomes не становятся features. Censored не zero-fill.
Global chronological train/validation/test intervals с purge по максимальной maturity
всех scenarios решения; все восемь variants решения остаются в одной partition.
Normalization только по уникальным train history frames; test не участвует в fit/selection.

Общий интерфейс: history [batch,time,features+missingness+age], известные до действия
scenario direction/size/delay/horizon, latent encoder → ordered return quantiles и
conditional execution class logits. Нет собственного portfolio policy или net PnL:
существующие labels описывают independent future snapshot reset, unknown costs/inclusion.
Кандидаты empirical, linear, MLP, GRU, causal TCN и Transformer. Width/context/частоты/
квантили — конфигурации опытов, не выбранная производственная архитектура.

Trainer: seeds, masked losses, train-only transforms, finite/gradient guards, AdamW,
clipping, best validation checkpoint, immutable epoch checkpoints, dataset/config/code
binding, RNG/optimizer resume; label-free inference artifact. Evaluation: pinball,
quantile coverage, class log-loss/Brier, counts/masks/decision groups/temporal blocks.
Это не financial backtest. Trade promotion gates остаются отдельными и закрытыми.

Bounded инженерные опыты: synthetic fixture + sealed D43 window0 только smoke; seeds7/19,
≤5 epochs/кандидат, небольшие widths/context, threads1. На M4 измерить CPU/MPS forward,
backward, memory и latency; unsupported ops/отказы явно фиксировать, не подменять CPU
за MPS. Scaling grid ограничить widths16/64 и context8/32; не выводить качество из скорости.
Сравнения метрик synthetic/small real не выбирают «лучший мозг» для рынка.

До настоящего обучения нужны квалифицированная выборка/вселенная, достаточные календарные
режимы и target support, глобальные folds/защищённый holdout, заданный research protocol
и проверка execution model. Foundation не выдаёт себе такое разрешение числом samples.
Результаты пока не получены. Источники: [PyTorch MPS](https://docs.pytorch.org/docs/stable/notes/mps.html),
[reproducibility](https://docs.pytorch.org/docs/stable/notes/randomness.html).

## Реализованный интерфейс и воспроизведение

Optional environment — CPython 3.13, PyTorch2.14.1, NumPy2.5.3; lock отдельно.
`make ml-setup` создаёт `work/ml-venv`, не изменяя `.venv`/frozen D43.
`make ml-check` проверяет ML types/properties; базовый `make check` проверяет
stdlib runtime и data contracts без установки torch. Отдельный CPU job добавлен в CI.

```sh
make ml-setup
make ml-check
work/ml-venv/bin/python tools/ml_smoke.py work/ml-smoke-NEW \
  --sealed-window work/calendar-execution-01/windows/000
work/ml-venv/bin/python tools/ml.py benchmark work/ml-benchmark-NEW --iterations 10
```

Все output directories должны быть новыми. Smoke matrix заранее сохраняет plan:
synthetic120 решений, seeds7/19 ×5 candidates; sealed real window, seed7 ×5;
width16/context8/batch32,3 epochs, AdamW lr0.001, clipping1, validation pinball+CE,
patience3, fixed quantiles0.1/0.5/0.9. Chronological smoke cuts real:50%/75% decision
timestamps, test maturity as-of=max recorded maturity+1ns; synthetic60/90/124s.
Это фиксированный integration recipe, не preregistered market experiment или holdout.
Первый failure останавливает matrix; новые попытки требуют нового output directory.

Низкоуровневый запуск: `tools/ml.py train DATASET NEW_RUN --protocol PROTOCOL.json`
и optional `--resume PREVIOUS_RUN`. Protocol содержит `split` (train_start_ns,
train_end_ns,validation_end_ns,test_end_ns,embargo_ns) и `training` (model:{kind,width,
context},seed,epochs,batch_size,learning_rate,device,patience,mode). Resume только на
границе завершённой эпохи, из sealed run, с идентичными dataset/config/source/torch;
увеличивать можно только final epochs внутри smoke budget. Прерванный незапечатанный
run не считается завершённым checkpoint; best weights не подменяют last optimizer.
CPU exact reproducibility проверяется на одном окружении; cross-platform/MPS bitwise
тождественность не обещана. Повторное чтение test при resume не создаёт fresh holdout.

`Predictor(run).predict(frames, decision_ns, action)` загружает `weights_only=True`
после проверки checksum и source. Action=[input_index,size_fraction,delay_seconds,
holding_seconds]; сценарий известен до решения, не измеренная будущая latency.
History25 каналов=12 standardized features+12 missing flags+log1p(receipt-causal age).
Нормализатор считает unique train frames, missing заменяется0 только вместе с mask.
Invalid real starts исключаются; censored usable decisions сохраняются без loss-labels.
История обрывается на unusable/segment/gap, включая gaps>15s в этом smoke recipe.
Валидация может использовать доступную прошлую историю из train: это не fitting на test.

Линейный кандидат использует последний frame; MLP flattened bounded history; GRU,
causal dilated TCN, causal Transformer — temporal encoders. Общие heads: ordered return
quantiles через softplus offsets и logits трёх conditional execution statuses. Эти
вероятности не являются calibrated fill/admission probabilities собственной сделки.
Empirical baseline: action-conditioned train quantiles, Laplace class counts и явный
pooled fallback при отсутствии return support. Loss=masked mean pinball+masked CE;
никаких zero labels для censorship, полностью censored batches пропускают optimizer.
Test report содержит coverage/Brier/log-loss/pinball, support counts и три temporal
блока; сценарии не считаются независимыми samples, confidence/edge не вычисляются.

Ограничения foundations v1: eager tensors с лимитом256MiB/partition, single-device,
без distributed/streaming loader, без policy/portfolio allocator/MPC/RL и полного
execution simulator. Fixed3 quantiles и3classes — версия инженерного контракта,
не обязательный окончательный target/head design. Настоящий research mode закрыт
явным gate: corpus/protocol qualification ещё не реализована и не одобрена. Перед
большим обучением нужны отдельное решение о universe/targets/folds/holdout, streaming
scale при необходимости, model-selection budget и execution-aware evaluation.
