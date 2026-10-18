# P1.1 / P1.2 — shell и validated client

2026-10-03. Web scaffold в `apps/control-center/web`. Exact pins из toolchain-pins.md.
Lockfile создан `npm install` в `web/`, не в корне.

Проверки Coordinator: vitest 11 PASS, `tsc --noEmit` PASS, `vite build` PASS.
Браузер `http://127.0.0.1:5173` при adapter `127.0.0.1:8787`: обзор показал 6 окон,
0/114, 135 engineering, 15 runs, RECORDED EVIDENCE, baseline 6a7e079.
Маршрут `/trading` показал `NO_APPROVED_TRADING_CONTRACT`, без кнопки успеха.

Visual review `f22f1d20-204d-404a-bdd0-1852c1801cc4`: reference SHA совпал,
screenshot `p1-overview-1672-full.png` sha256
`660319d6529cd0e40d779a0a0ef6518fe1740a78f93da527330efa71fc9c4b53`, PASS.
Boundary review `54c65b88-c0a4-4706-8c1c-00a768a737c4`: только GET loopback, PASS.

Model binding остаётся UNVERIFIED. Следующая задача — P2.1.
