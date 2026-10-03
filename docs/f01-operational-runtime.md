# D50: operational supervisor frozen F01 source

## План до реализации — 2026-10-03

D49 принят пользователем; продолжается core roadmap. UI/control-center worktree не
менять. Код в core main, новый package memecoin_operational сохраняет fingerprints
D43–D49. Existing D48 proposal/calendar/source root и D49 candidate не переписывать.

1. Одна bounded read-only проверка account usage/billing доступа. No-charge/credential
   mapping нельзя вывести из USD0. Без account-specific evidence — реальные RPC0;
   условный budget одного отдельного live qualification180RPC/3600CU, без retries.
2. Реальный subprocess supervisor:210s segment /600s whole window; process group kill,
   parent-death fencing, restart no retry, STOP,disk floor,monotonic+wall clock guards.
   Disk floor2GiB + reserve512MiB/window; bounds принадлежат operational envelope,
   не меняют frozen scientific semantics или60480RPC ceiling.
3. Immutable staged runtime bundle: frozen packages/interpreter/references/proposal,
   exact artifact/source/credential pins, runtime guards before child and each RPC;
   separate budget review and activation receipt. Никакого install/load/activation.
4. Pure/fault fixtures и полный180fakeRPC subprocess integration→D48 admission→offline
   reproduction. Ускоренный synthetic clock явно маркирован; real transport0,training0.
   Проверить timeout/parent crash/kill/restart/tamper/low disk/clock/partial publication.
5. Staged launchd plist и due-only operational entrypoint (default gate closed), без
   регистрации сервиса. Read-only host metadata не равно доказанному awake uptime.
6. Полные checks/wheel reproduction, concrete verdict, Memory Bank, commits/push main.

First error closes window; no catchup/backfill/calendar shifts.8failed stop. Raw/реальные
availability не меняются, protected holdout не открывается. Нет торговли, upgrades,
платежей, QuickNode, изменения D43, F01 training или освобождения holdout. Если access
не появился — завершить всю независимую инженерную часть и явно перечислить blockers.
