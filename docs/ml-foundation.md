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
