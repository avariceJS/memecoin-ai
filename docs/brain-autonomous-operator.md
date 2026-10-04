# B13 — автономный paper Brain и операторский Control Center

## Решение до новых fits / activation, 2026-10-04

Прямое поручение пользователя: вывести Codex из ежедневного operational loop на следующие
пять дней. Разрешены независимый bounded product rollout, UI-команды Collect/Snapshot/Train/
Paper/Auto/Pause/Stop/Resume, новые immutable candidates и отдельные future exams. Старые
B09/B10/B12 сроки, STOP, incidents, frozen weights и ledger не переписываются. F01/D43/
reserved holdout не читаются. Paid budget0, real execution/signing отсутствуют.

Проверка начала сессии: Git a4aaf66, clean. B09 source и B10 paper healthy; B12 получил
новый STOP worker_failed/exit1 в2026-10-04 19:41UTC: index_membership_corruption.
Несогласованные SELECT в WAL без общей transaction — проверяемая гипотеза; recovery только
после regression/raw audit/exact ledger replay в новой revision с прежним exam expiry.

## Исследовательский контракт новой автономии

Новый recipe B13 заранее фиксирован: прежние12features,8frames,price log-return q10/50/90,
5/15min, empirical + linear/GRU width16, seeds7/19,1000updates×64decisions, AdamW как B12.
Новых heads/RL/PnL-targets нет. Checkpoint и development model выбираются только по
validation pool-macro pinball, прежнее правило1%/простоты empirical→linear→GRU.
Никакого automatic champion promotion; empirical champion остаётся сравнительным контролем.

Snapshot — consistent SQLite backup и actual receipt cutoff, first-observed bars со
ссылками на raw. Global календарные границы: test_end — закрытая5min граница на snapshot;
validation_end=test_end−3h; train_end=test_end−6h. Whole decision maturity строго до конца
своего fold. Это новый rolling development/refit protocol: прочитанные исторические B12
outcomes могут стать прошлым для нового обучения, но НЕ независимым confirmatory evidence.
Каждый новый future exam начинается только после готового sealed model с фактическими
новыми eventclose/inputreceipt. Exam24h; новый daily refit не меняет старые веса.

Чтобы recent markets попадали в train, cuts привязаны к текущему календарю, а не60% всей
старой истории. Coverage каждого fold и unseen pools публикуются до fit. Train sample cap
48000, validation/test по6000: deterministic hierarchical pool/day round-robin, порядок
внутри bucket поSHA(seed7,id), без чтения outcome values. Sampler pool/day balanced как
B12; это заранее принятый diversity-oriented recipe, НЕ выбор по B12 test winner.
Полный архив не удаляется/не обрезается; manifest показывает исходное и retained coverage.
Max500000 исходных decisions в materialization; превышение — явный capacity blocker.

Auto: collect → snapshot → train → новый candidate → отдельный frozen future exam,
не чаще24h от reservation и не менее100новых mature samples, максимум6training reservations
за rollout≤7days. Partial/failed jobs не publish candidate; неизвестная ошибка fail-closed.
Paper PnL оценивает отдельную fixed15min policy, не используется для обновления weights.
WAIT/ENTER/HOLD/EXIT в UI отражают действительный baseline lifecycle; learned exit/MPC
в этом этапе не объявляются реализованными.

## Операционный контракт

Новый localhost Control Center8792 и durable operator journal. Legacy8790/8791 доступны.
Commands idempotent, jobs bounded, operator/recovery events сохраняются. Pause прекращает
новые operator tasks; Stop сохраняет incomplete attempts; Stop all явно включает legacy
services. Resume не снимает domain integrity STOP и не продлевает expiry.

B09 source используется до его исходного2026-10-07 12:23:51UTC. Затем отдельный successor
с consistent index/raw reuse, inherited cursors/quarantine/provider failures и новым
ограниченным разрешением текущего пользователя, без параллельных provider clients.
До successor фаза Collect не меняет frozen B09 cadence. Новые дополнительные limits:
≤30000publicGET,≤6000background, тот же hot6/120s,spacing12s,raw8GiB/free4GiB,paid0.
Предшествующие qualification/error records — унаследованный evidence, не новая измеренная
qualification. Single-provider risk и исторический survivor bias явно остаются.

Promotion требует отдельного исследовательского review сопоставимых matured future exams;
кнопка с фиктивным quality approval не создаётся. Сон/выключенный Mac не uptime; auto resume
после входа пользователя в macOS продолжает только будущие observations и сохраняет gaps.

## Делегирование

Проверен/установлен Cursor CLI2026.10.01-e373342; после штатного login `agent models`
подтвердил `grok-4.7-xhigh`. Два инженерных worker: control/UI/jobs/tests и WAL/acquisition
plumbing/tests. Dataset/model-selection/prospective decisions и итоговый review — Codex.
Результаты implementation/acceptance будут добавлены после проверок; сейчас не проведены.

## Инциденты во время интеграции (исходные записи сохранены)

2026-10-04 19:41UTC: B12 paper STOP `worker_failed`, traceback
`index_membership_corruption`. Реальный raw/index audit94,116bars exact; regression
воспроизводит ложное нарушение при concurrent commit между SELECT. Новый WAL read snapshot
сохраняет все checksum/membership guards. B12-recovered01 на8793 сохраняет63tick prefix,
state_sha`bc36ca4f2f6289e740c1b77cfbae098c67252e186d0705f2d39569cb5f6d6ed3`,
2pending,0positions,0exits, modelrefs/start/expiry без изменений. Старый STOP не удалён.

2026-10-04 20:32UTC: B09 audit supervisor достиг600s, category`source_stop_or_deadline`,
exit-9, incident1791145945693553000. Это НЕ исходный expiry и не допускает обычного Resume.
Исходный STOP sha`b6b5968fce4377324a89b6671b694b2a1ee806d51be6039593e70f500194674b`.
Read-only backup и независимый full raw audit:95,204bars/1,865validcaptures exact,
fingerprint`cc8f1a2a303aae8d3018aac04093feb63045bd9f9a494b181fb323385c1e0ad7`.
На backup EXPLAIN показал512scan по264,529versions; индекс`versions(pin,open_s)` заменяет
scan на поиск по pin. Тот же audit_batch512 после индекса занял2.235s; full raw audit8.051s.
Evidence: `work/brain-b13/audit-performance01/result.json`. Не увеличивать deadline.
Допускается отдельная техническая source repair revision с exact state/counters/cursors,
прежними max_requests/expiry/quarantine и новой runtime binding. Это не новая квота и не
удаление STOP. Paper source migration требует exact ledger replay и прежних clocks/models.
Активация repair на момент этой записи ещё не проведена.

### Штатное завершение future exam

Operator не запускает новый150s paper child в последние180s до immutable expiry.
Это явный drain с возможным right-censoring последних outcomes, не продление срока
и не искусственный STOP. Unknown STOP остаётся fail-closed. Старые frozen B10/B12
runtimes и их expiry не переписываются.
