# Возобновляемый prospective corpus

Кампания фиксирует cohort, raw reference captures, source snapshot, общий request budget,
срок действия и временной calibration cutoff до первого запроса. `create-prospective-campaign`
создаёт план без сети; `resume-prospective-campaign PATH` собирает **один** новый bounded
сегмент; `verify-prospective-campaign PATH` воспроизводит завершённые сегменты offline.
Endpoint поступает через MEMECOIN_HELIUS_RPC_URL, в artifact сохраняется только alias.

Сегмент использует existing collect_forward и сохраняет exact requests/responses,
receipt/decode/publication timestamps, append-only attempts и frames. Перезапуск процесса
не переписывает сегменты: недозавершённый каталог опечатывается как interrupted вместе
со всеми сохранившимися bytes и исключается из labels. Весь его planned budget считается
израсходованным. Новый сегмент получает новый causal book, warmup и независимый tail
censoring. Границы не заполняются backfill. OS file lock исключает параллельный resume.

По умолчанию: максимум 4 × 30 rounds, три фиксированных пула означают 360 requests;
5 s cadence, socket timeout 15 s, response cap 1 MB, spacing 1 s, expiry 1 h.
Socket timeout и cooperative segment wall budget не являются жёстким deadline процесса.
HTTP/RPC/validation failure прекращает кампанию; timeout/connection допускает новую
ручную или оркестрированную invocation не ранее 30 s, максимум два failed сегмента
подряд. Внутри сегмента нет retries. Это инженерные ограничения, не trading parameters.

Seal каждого сегмента ссылается на предыдущий seal. Старые captures и manifests остаются
неизменными. Exhausted resume не делает сетевых запросов. После изменения transform
version нужна новая кампания или pinned source для проверки старой.

Сегментные observed-price targets остаются исходными diagnostics. Для последующей общей
разметки используется **единый frozen cutoff кампании**, а не повторяющееся чередование
calibration/evaluation каждого сегмента. Это engineering split, не финальный holdout.
Обучение, рыночная достаточность и execution qualification не следуют из complete=true.
