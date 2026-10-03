# D51: bounded live qualification и условия эксплуатации

## План до прогона — 2026-10-04

D50 принят пользователем. Работа в core `main`; UI worktree, D43, reserved holdout,
F01 specification и D48 calendar не менять. D51 не разрешает calendar activation.

1. Один read-only проход доступных account tools: usage, список приложений, metadata
   приложения. Не вызывать market RPC для проверки credentials. Browser discovery один
   раз; при отсутствии браузера — не повторять transport/access tuning.
2. Для единственного live окна нужны account-specific Free/no-paid-overage и exact
   mapping существующего runtime credential к этому аккаунту. Допустимо несекретное
   подтверждение владельца доступа с честно указанным происхождением. MCP USD0,
   masked key suffix и public pricing сами по себе недостаточны. Paid budget0.
3. После substantive review: отдельный pinned receipt `bounded_qualification`,180RPC,
   allocation3600CU, validity покрывает600s, единственный attempt root вне actual F01.
   Runtime — unchanged D50 bundle manifest SHA
   `a06f1d69ff8fb1c0f8647fc5622c3e2d8c1811e697d40eb8be15deb1663da880`.
   Никаких automatic approvals. До proof — RPC0 и никакой reservation.
4. Thin launcher вызывает existing supervisor, сохраняет exclusive durable reservation
   до запуска; crash/ошибка/существующий каталог исключают повтор. Два segment×90RPC,
  210s/segment,600s от reservation, первые ошибки останавливают работу. Никаких retries,
   смены профиля/пула, WSS, execution RPC или продления budget после результата.
5. Live pass:4completed phases,180successful coherent finalized/profile2 snapshots,
  180advanced feature starts, no guard failures, offline raw/feature/target reproduction.
   Неполное окно — fail, даже если часть данных полезна для engineering. Сохранить
   freshness/latency/gaps/statuses/storage, реальные availability неизменны. Clock age и
   freshness квалифицируются по existing frozen guards, без нового подбора порогов.
   Ни bounded окно, ни fixtures не импортируются в actual F01 registry.
6. Read-only host assessment: power/sleep/login/free disk, отсутствие нового launchd job,
   current runtime/credential binding. Не менять power settings и D43. Описать условия
   awake/login/monitoring; metadata не выдавать за измеренный uptime/reboot qualification.
7. Tests сначала offline, затем live только после пункта2. Immutable evidence, Memory
   Bank, логические commits/push. При недостающем доступе D51 остаётся OPEN/BLOCKED,
   а live — `не проведён`; не объявлять очередной этап завершённым вместо снятия blocker.

Calendar activation требует отдельного итогового решения после evidence. T2026-10-07
не сдвигать; missed окна не backfill. ML/research training, holdout release, торговля,
подпись/платежи/upgrades запрещены. Этот план не меняет научные критерии F01.
