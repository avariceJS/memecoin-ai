# B10 — эксплуатационный отказ и восстановление paper

## План до изменения runtime, 2026-10-04

B09 принят. Реальная эксплуатация обнаружила более срочное ограничение, чем новый source:
2026-10-04 13:35:45 UTC paper supervisor завершил worker после 7,61s, записав только
`supervisor_guard`, exit −9. Worker log пуст. Source продолжает acquisition. Причина внутри
guard неизвестна; provider outage, OOM или corruption не установлены. STOP не удалять.
Текущие frozen inventories точны, 300 read-only guard проверок прошли за 2,714s.
Полный replay: 85 ticks, 765 decisions, 5 exits, 0 open/pending; state SHA
`b8623678acdf0b237a69f590952a72950a48b81d670ec81fe6469bcf92420a11`.

B10 — отдельная paper revision поверх неизменного B09 source. Скопировать ledger,
shadow namespaces, обучение и исходную историю soak; проверить exact prefix/cash/positions.
Сохранить старые STOP/incident и lineage. Не менять source runtime, allocations, expiry,
hot cohort, models, policy/costs, F01/D43/holdout. Никаких новых provider probes или fits
раньше прежнего daily gate. Новый paper prefix b10; исходный срок 2026-10-07 12:23:51 UTC.

Добавить безопасную диагностику guard: stage/type/errno и только allowlisted reason,
без произвольного exception text, endpoints или credentials. Ошибки startup тоже имеют
incident. UI независимо от последнего dashboard показывает STOP/возраст paper и source,
ожидаемое время следующего training, а не выдает последний успешный snapshot за health.

Автоматическое восстановление разрешить только paper worker, внешне завершённому SIGKILL
или SIGTERM при category worker_failed (не supervisor kill), максимум два раза за deployment
с переносом уже потраченного бюджета через migration, cooldown60s. Перед продолжением —
полный ledger replay и проверка projection. Никаких retries training, integrity errors,
unknown guard errors или deadlines. Каждая попытка имеет immutable incident/recovery receipt;
неоконченная попытка расходует allowance. На третьем/другом отказе STOP. Это не общий
авторестарт после любой ошибки и не доказательство устранения исходного неизвестного сбоя.

Проверки: fixtures crash до/после ledger commit, exact recovery, corruption fail-closed,
retry exhaustion/restart, training partial artifacts не становятся кандидатами, safe logs,
migration уже migrated root, отсутствие backdated решений. Независимый replay реальных
артефактов и raw/prediction joins; make check/smoke/build и optional ML tests. После cutover
наблюдать несколько реальных циклов без ручного вмешательства; сутки не симулировать.

Следующий source сохраняет отдельную provenance lane: GT first-observed candles нельзя
подменять on-chain balances или неqualified PumpSwap events. Multi-source остаётся следующим
архитектурным участком, а не объявленным B10 fallback. Недельную activation не выдавать:
нужны реальные сутки, scheduled refresh и устранение неизвестных operational failures.
