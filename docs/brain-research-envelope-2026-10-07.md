# Research acquisition после owner expiry

Дата: 2026-10-07. Production/champion/AUTO не изменяются; ML runs запрещены.

## Preregistration нового envelope

Это отдельный research-only envelope, не третья continuation старого протокола.
Исходные campaign01/02, их STOP, expiry и seals сохраняются. Новый root:
`work/brain-development-20261007/research-envelope01`. До первого GET фиксируются:

- Тот же исходный sealed roster: 20 mint, 40 windows. Только незапрошенный suffix31;
  прежние9 requests переносятся debit. Failed/partial request не повторяется. Проверка
  всех прежних raw receipts воспроизводится до запуска, membership не адаптируется к support.
- Новый wall budget30min от prepare, spacing30s, timeout30s/child35s, response2MiB,
  disk floor4GiB, storage192MiB, paid0; суммарно40 GET по этому roster. Прежние127
  research requests и source/predecessor reservations включаются в общий accounting.
- Отдельный research flock. Ни production locks, ни config/LaunchAgents не изменяются.
  Истёкший source не получает fake heartbeat или новый lease. Его deadline STOP допускается
  только существующей read-only проверкой `_admit_expiry_stop(audit=False)`.
- Перед каждым запросом exact-argv/executable consumer inventory, сохранённый redacted
  receipt. Неизвестный Python/project process блокируется; suffix `ui` больше не является
  разрешением. Во время child owner fence проверяет pinned files, paused revision33,
  отсутствие queued/running jobs, завершённые exam expiries и неизменный source journal.
- Consumer inventory является host-process guard, не egress firewall и не доказательством
  отсутствия стороннего трафика со всего аккаунта/IP. Между snapshots возможна race;
  неподконтрольный внешний consumer не получает автоматический allowlist.
- Любая ошибка integrity/transport/401/403/429 вызывает STOP/no retry;404/empty сохраняются
  как negative support без замены mint. Seal partial outputs, не скрывать denominator.
- Research role только `development_historical_transfer`: без training, inference,
  model scoring и retroactive online availability. Даже полный40GET не означает готовность
  broad AUTO/Watch prospective benchmark; support floors не снижаются.

Zero-GET qualification01 на Mac1:29 snapshots за60s, unknown0,17 loaded launcher rules.
Observed:7paper UI + paused operator, краткоживущие expired B04/source/paper supervisors.
D43 проверен только по launcher/code/schedule metadata; references/windows/outcomes не читались.
Точный consumer старого STOP02 установить нельзя: identity тогда не сохранялась.

Перед GET prepare дважды fail-closed: сначала неверное предположение о нулевом начале
source request sequence (реальный inherited prefix4449), затем неверный путь к owner DB.
Root ещё не был создан, requests0. Исправлены обе предпосылки; admission основан на новом
`guard-qualification02`, который дополнительно исполняет реальный SQL owner fence в каждом
из29 snapshots. Receipt01 сохранён как process-only проверка, не authority для запуска.
В repo после freeze добавлена отдельная защита от serial replay этого roster через новый
root и prepare-time общий lock; текущий frozen collector задним числом не изменялся.

## Результат

**Bounded acquisition завершена, широкий benchmark не готов.**
[Проверенные receipts и pins](evidence/brain-research-envelope-2026-10-07.json).

- Новый envelope:31/31HTTP200,377553bytes,15m27.4s от первого GET до последнего receipt;
  min spacing30.316s.31consumer snapshots,unknown0,transport failures0,STOP не возник.
  Shared accounting6459: source6256 +predecessor36 +previous research127 +roster40.
  Это project accounting, не измеренный account-wide provider meter.
- Весь исходный cohort40/40окон,20mint:2020unique bars,453eligible origins,6supportedmint,
  29неперекрывающихся full-dependency windows. Ранее было411bars/38origins/2mint/3windows.
  Новые GET дали415origins. Все40raw receipts и saved qualification reports независимо
  reproduced наMac2; conflicting revisions0. Ни roster, ни missing denominator не менялись.
- Age<1d:405origins/6mint/27within-stratum disjoint windows/6UTCdays. Age1–3d:48origins/
  1mint/3within-stratum windows/1day. Age7–10d:0. Within-stratum27+3 нельзя выдавать за
  global30: feature dependencies около возрастной границы пересекаются; global29.
  Одинmint даёт322/453=71.08%origins. Поэтому это не broad balanced evaluation basis.
- Post-run qualification03:ещё29snapshots/60s,unknown0,все owner pins/revision/champion
  совпали сqualification02. Mode/phase paused,revision33,jobs0,managedexams expired.
  Champion9ea4a6bd...3920,runtime08/fec78c60...63c3a unchanged. Истёкший source STOP и
  incident сохранены; warningadapter_unavailable не исправлялся в этой задаче.
- Newfits0,predictions0,oldtestscore0,paid0; production/champion/AUTO writes0.
  Ни24/7service, ни длительный prospective campaign не запускались.

Operational вывод: old guard мог отклонять наблюдавшиеся legitimate expired B04/paper
supervisors, поскольку классифицировал плоскую command line по подстрокам. Это подтверждённая
проблема его правил, но точный виновник старого STOP02 **не установлен**. Новый exact-role
guard прошёл finite live run; это не безусловная qualification для unattended/account-wide
acquisition. Следующий bottleneck теперь измерен как support/coverage, а не неизвестный
consumer в этом ограниченном запуске.

Артефакты на двух Mac:research-envelope01,guard-qualification02/03; Mac2 support02.
Envelope SHA`9c8f79b0eb76f30981d3427a038b91b92b785ea8ed03ec4d0fe0465ec7d8a756`,
support SHA`7d4bf2b4cb2e39f0ca5e9b2f991136bc702b137951e9d400baecb9812d48168f`.
Exact executed worker/prepare/guard SHA находятся в evidence JSON; frozen prepare отличается
от repo последующим replay fence, см. выше. Старые campaign01/02 seals verified unchanged.

Проверки:финальный make check1063passed/12optional skips (Torch/browser unavailable);
53focused testsPASS,ruff/format/mypyPASS; make smokePASS. Повторный offline support report
byte-identical исходному support02, manifest SHA совпал.
Новый envelope не требует release10 и не меняет Watch Market deployment status.

## Следующий data шаг, не новый ML sweep

После завершения фиксированного suffix оценить coverage без model predictions. Даже если
transport/guard исправны, исходная случайная birth-cohort может не давать достаточно
непрерывных USD bars. Это отдельный результат, а не основание снижать context/target gates
или объявлять missing candles нулевыми returns. Старый cohort сохраняется целиком.

Следующий prospective campaign должен заранее разделить:

1. Seen-temporal research на frozen operational roster, с новыми event windows после всех
   exposed/managed-exam intervals и полного feature purge. Пока production paused, это
   **proxy roster**, не измеренная текущая AUTO traffic distribution.
2. Watch breadth: новый enrollment по публичным metadata и возрастным strata, с eligibility
   по данным, доступным на момент enrollment. Критерии должны быть записаны до будущих
   outcomes. Все rejected/missing/снятые с наблюдения markets остаются в журнале denominator;
   нельзя просто подобрать другие mint вместо неудачных случаев старого cohort.
3. Отдельные development/calibration/prospective интервалы, фактические received/available
   timestamps и snapshot freeze до scoring. Исторический transfer не заменяет future test.

Новый длительный campaign ещё не preregistered и не запущен: нужны его фактические roster,
общий provider ledger/budget, expiry и support schedule. Multi-day support нельзя получить
за короткий технический smoke. Сейчас не включается unattended service и не меняется AUTO.
