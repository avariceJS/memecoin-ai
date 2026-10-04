# B03 — quarantine отдельных пулов и возобновление Brain corpus

## План до новых запросов — 2026-10-04

B02 принят пользователем. B03 меняет обработку известного data-quality failure,
не scientific правила F01, universe, исходные bars/features/targets или модели.
Старые B01/B02 STOP, failed runs и raw неизменны. Новое самостоятельное решение
об activation возможно только после fixtures, recorded replay и проверки bindings.

Исходные bounds: expiry 2026-10-07T00:30:23.405597Z, 12000 GET суммарно с B01/B02,
103 уже израсходованы; paid0, тот же public keyless GeckoTerminal. 12 исходных пулов
всегда в denominator. Импорт raw/model/shadow byte-for-byte, исходные receipt times.
Смена протокола failure classification явная: прежние конфликты OHLCV переводятся
в pool quarantine, а не обнуляются как будто успешные; это разрешённый B03 repair.

Правила pool quality:
- Только известные strict bar errors: duplicate timestamp, invalid OHLCV, alignment.
  Весь ответ исключается. Никакого выбора одного conflicting duplicate/слияния OHLCV.
- Quarantine durable до следующего GET. Проблемный HUQ… seeded из B02 raw evidence.
  Остальные пулы продолжают; в каждом cycle для всех12 есть observed/skipped/failure status.
- Не более одного recovery probe в час, максимум3 на pool до исходного expiry.
  Для release нужны2 consecutive свежих valid probes, разделённых не менее1часа.
  Probe bytes сохраняются; пока pool quarantined, новые shadow predictions по нему
  не выдаются. Уже выпущенные forecasts могут получать mature outcomes по valid raw.
- При4 quarantined pools (из12) global quality stop; не продолжать выборку из меньшинства.
  Fresh здесь nominal close age≤600s; shadow по-прежнему использует свой≤300s gate.
- HTTP/auth/rate/network, hash/clock/runtime corruption не являются pool errors.
  Auth/429 немедленно закрывают источник; transport закрывает cycle,2 consecutive
  global failures→STOP. Integrity/runtime failure→STOP. No immediate retry/catchup.
  Успешный cycle без новых запросов не сбрасывает global failure debt.

Qualification: свойства на fixtures, полный mixed healthy/quarantined cycle, restart,
recovery spacing/count/release, provider failure, crash reservation, denominator,
future input/target, recorded conflicting response и exact replay. Staged runtime
с code/config/universe/import/quality-seed bindings; hard240s collection/600s training,
CPU1 для fit, существующие6h/max12training cycles, leases/STOP/disk/clock safeguards.

После qualification — один новый approved root work/brain-b03/prospective01. До
установки job выполнить bounded live cycle, исторический проход максимум24GET/600s
по недобранным prefixes шести пулов до B02 anchor/floors, затем второй due cycle.
History request before=cursor−1; общий pacing≥12s, единый root/lock/request counter.
Historical pass не повторяется после failure/crash; schema failure quarantines pool,
provider failure закрывает pass и runtime. Ни одного дополнительного источника.
Два live cycles должны сохранить полный denominator и дать≥9 fresh valid pools;
иначе service не устанавливать. Никакого третьего live qualification retry.

Сохранить coverage/revision/paired mature shadow report и wheel reproduction. Если
история существенно расширит pool diversity, повторить B02 learning curves на новом
sealed dataset: те же2folds×3sizes×4fits=24, hard600s, без изменения champion и без
выбора lucky seed/horizon. Robust normalization — отдельный следующий experiment,
не смешивать с качеством/объёмом данных в B03. Календарные часы/дни накапливает bounded
job; не объявлять их полученными заранее. Trading/paper/live/RL запрещены.
