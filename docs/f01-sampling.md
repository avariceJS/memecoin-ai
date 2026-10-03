# D48: будущий sampling/source binding и sealed corpus adapter F01

## План до реализации и опытов — 2026-10-03

Источник правил: frozen [F01-v1](../configs/research-f01-v1.json),
[D46](research-readiness.md), [D47](f01-runner.md). D43 и унаследованный holdout
не открывать/не менять. Настоящее обучение и новый сбор не запускаются.

1. Отдельный immutable proposal: future UTC anchor после inherited holdout,84days,
   четыре5min окна в сутки по6h strata (UTC00:30/06:30/12:30/18:30),336 окон.
   Existing finalized Alchemy HTTP/profile2, fixed3pools,2segments×30rounds×5s,
   ≤180RPC/окно,≤60480RPC всего; без WSS/fallback/нового execution sampling.
   Это ceiling предлагаемого плана, не разрешённый расход/тариф/оплата.
2. Source semantics, frozen converter/runtime hashes, exact schedule и отдельный
   activation boundary. First error closes window; no retry/backfill/catch-up,
   lateness≤900s,window wall≤600s,stop после8 failed окон. Missing сохраняется.
   Нельзя сдвигать календарь или заменять pools по observed outcomes.
3. Новый пакет сохраняет hashes D43–D47. Sealed development admission: actual source
   semantics/reproduction, times/identities/grid/counters, complete window seal,
   duplicate/conflict detection, append-only revisions и offline verification.
   Holdout по window index/cutoff отклонять до любого чтения его source path.
4. Dossier связывает immutable revision/runner plan, actual support, full scheduled
   denominator, failures/missed/pending, usable fraction, frozen folds и missing
   reviews. Numerics не выдают admission; forecast/economic gates прежние.
5. Fixtures на multi-window/restart/tamper/temporal/holdout/no-false-readiness,
   engineering read-only integration только D45 development. Не переименовывать
   старые timestamps в реальные prospective observations. Budget/scaling feasibility
   оценить без training; generic threshold не гарантирует power/rare-class support.
6. Offline/wheel reproduction, проверки, Memory Bank, логические commits/push.

Fail-closed при integrity/clock/source/unsupported input: safe category, сохранённый
attempt/log, без retry loop. Старые artifacts не переписывать. Новый network/runtime
activation требует отдельно зафиксированного решения по concrete budget/credentials/
feasibility; D48 не устанавливает collector и не выдаёт research admission.
