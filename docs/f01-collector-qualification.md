# D49: квалификация bounded collector для frozen D48

## План до реализации и сетевых проверок — 2026-10-03

Проверяется существующий `work/sampling-d48/proposal`; его календарь, F01, D43,
source/feature/target semantics и reserved holdout не меняются. Source root proposal
не создавать. Никакого launchd install/load, длительного collector или training.

1. Read-only Alchemy MCP: app metadata и usage; не считать их доказательством runtime
   credentials, Free tier или zero-dollar hard limit. Официальные CU/throughput/pricing
   сверить отдельно. Не платить, не подписывать, не менять account/plan.
2. Один HTTP qualification attempt: ≤6 finalized getMultipleAccounts (2×3 fixed pools),
   existing persistent transport/profile2, timeout15s/response≤1MB, inter-round5s,
   ≤100s parent deadline, nominal≤120CU. Это отдельная разрешённая bounded диагностика,
   не расход proposal60480 и не F01 corpus. Первый transport/protocol/freshness failure
   останавливает attempt без retries/fallback. Raw/actual receipt/availability сохранить.
   Полное180RPC live окно не запускать без подтверждённого monetary режима.
3. Новый изолированный collector qualification package: durable reservation/restart,
   due-only/no catch-up/8 failures/600s wall/180RPC и exact staged activation binding.
   Проверять adversarial fixtures, без редактирования frozen runtime пакетов.
4. Runtime feasibility: полный180-call synthetic raw integration и offline replay,
   реальные filesystem/RAM/runtime metadata, response/storage и CU ceiling по месяцам.
   Snapshot Clock/freshness не доказательство полноты исторических данных или uptime84days.
5. Staged activation artifact связывает proposal/spec/runtime/interpreter/references,
   output root, credential fingerprint, budget evidence; все permissions false.
   Отсутствующий тариф/hard cap/account identity/service readiness — явные blockers.
6. Проверки, evidence, Memory Bank, commits/push. Отдельное итоговое решение после D49
   необходимо для установки/запуска. Readiness F01 и holdout release остаются закрыты.

Сбои сохраняются с безопасной категорией; сетевой budget не возобновляется после сбоя.
Критерий ready: подтверждены все source/quota/zero-money/storage/runtime/scheduler/binding
условия, не только успешный RPC. Установленный сервис и устойчивость календарных окон
не выводятся из synthetic тестов. Неподтверждённое условие означает NOT READY.
