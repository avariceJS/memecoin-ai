# Exact UI toolchain pins — Phase 0

Снято 2026-10-03 с public npm registry (`npm view`, dist-tag `latest`, не `next`).
Локально: Node `v24.5.0`, npm `11.5.1`, Python `3.13.6`. Vite engines
`^20.19.0 || >=22.12.0` этот Node удовлетворяет. `web/` и lockfile ещё не созданы.
Установка — только `apps/control-center/web`, `npm ci`, версии в `package.json` без
диапазонов. Не ставить пакеты в корень репозитория и не менять core lockfile.

Решение CC-009. React Router остаётся 7, не current latest 8.4.0. TanStack Table,
когда понадобится, остаётся 8, не current latest 9.2.4. TypeScript — dist-tag latest
`7.0.2`, не `next` `7.1.0-dev.20261003.1`.

## Phase 1 — ставить вместе

| Пакет | Версия | Зачем оставлен этот major |
|---|---|---|
| react | 19.3.0 | CC-003 React 19 |
| react-dom | 19.3.0 | пара к React |
| @types/react | 19.3.0 | типы той же линии |
| @types/react-dom | 19.3.0 | типы той же линии |
| react-router | 7.18.4 | последний 7.x; peer `react >=18`. Не 8.4.0 |
| @tanstack/react-query | 5.104.1 | CC-003 Query 5; peer `react ^18 \|\| ^19` |
| zod | 4.6.5 | CC-003 Zod 4, граница DTO |
| typescript | 7.0.2 | registry latest, strict |
| vite | 8.3.2 | registry latest; Node 24 подходит |
| @vitejs/plugin-react | 6.1.1 | peer `vite ^8` |
| vitest | 5.0.3 | peer включает Vite 8 |
| jsdom | 30.1.1 | среда Vitest |
| @testing-library/dom | 10.4.2 | peer Testing Library |
| @testing-library/react | 16.3.3 | peer React 19 |
| @testing-library/jest-dom | 7.0.1 | matchers |

В этом наборе нет web3, RPC SDK, wallet, ethers/viem и Python core. Boundary review
2026-10-03 это подтвердил до установки.

## Позже, тем же правилом exact pin

| Пакет | Версия | Когда |
|---|---|---|
| @tanstack/react-table | 8.21.3 | Phase 2/3, если native table недостаточен. Не 9.2.4 |
| recharts | 3.10.1 | графики отчётов, не свечи |
| @tanstack/react-virtual | 3.14.13 | только после измеренного bottleneck |
| @playwright/test | 1.63.0 | CC02.0 browser harness, только devDependency |
| @types/node | 24.19.1 | типы Node 24 для `tsc` harness, не runtime |
| @axe-core/playwright | 4.13.0 | ещё не ставить; a11y остаётся на CC02.8 |

Optional peer Vite (sass, less, terser, babel compiler) не ставить.

## CC02.0 — установлено 2026-10-04

`@playwright/test@1.63.0` и `@types/node@24.19.1` стоят exact devDependencies.
`npm run build` оставил прежний JS 514.84 kB / gzip 153.15 kB и CSS 35.99 kB:
Playwright в production bundle не входит. Lucide, Inter и Radix ещё не ставились.

## CC02.1 — установлено 2026-10-04

`lucide-react@1.51.0`, `@fontsource-variable/inter@5.3.0`, `@radix-ui/react-tooltip@1.2.16`.
После оболочки production JS 575.21 kB / gzip 175.09 kB, CSS 40.53 kB / gzip 7.38 kB,
плюс локальные WOFF2 Inter. Dialog ещё не ставился.
