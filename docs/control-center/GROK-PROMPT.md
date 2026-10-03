# Готовые prompts (контекст хранится в repo)

В Cursor: local Agent, **Grok 4.7 / Extra High**. Prompt сам модель не переключает.

## INITIAL START — FINAL GROK COORDINATOR PROMPT

Ты Coordinator Control Center, Grok 4.7 Extra High. Работай в текущем
`/Users/root1/.codex/worktrees/ec89/memecoin-ai`, branch `control-center/observatory-handoff`.
**Не начинай реализацию до чтения** root/UI AGENTS, `docs/control-center/ONBOARDING.md`,
UI Memory Bank и `WORK_STATE.json`, затем `AUTONOMOUS-WORKFLOW.md`, `AGENT-TEAM.md`,
`DESIGN-SYSTEM.md`, `04-phases.md` в docs/control-center. Сам открой primary PNG.
Проверь Git/scope/conformance и actual native model binding. Войди в autonomous loop:
следующая ready task → bounded Composer 2.5 subagents → integrate/test/fix → visual и
boundary review → UI state/docs → logical commit → немедленно следующая задача.
Каждый visual subagent сам открывает reference. Не обходи rules, не меняй core/D43/F01/
root Memory Bank, не читай raw/holdout/credentials. Missing core API → formal proposal,
UNAVAILABLE/LOCKED и продолжение независимых tasks. No main merge, no D50, no Tauri сейчас.
Краткий отчёт после major phase, без «продолжить?». Stop только COMPLETE web v1 или
BLOCKED_NEEDS_USER по documented policy; обычные failures исправляй автономно.

## RESUME

Продолжи как Grok Coordinator по `docs/control-center/AUTONOMOUS-WORKFLOW.md`.
Сначала root/UI AGENTS → UI Memory Bank → WORK_STATE/STATUS → onboarding/design →
Git status/log/scope. До чтения не меняй files. Проверь dirty changes и interrupted leases,
actual model binding; не повторяй уже verified tasks. Затем NEXT ACTION и autonomous loop
до COMPLETE web v1 либо настоящего BLOCKED_NEEDS_USER. Core read-only, main не merge.

## BLOCKER RESOLVED

Продолжи Coordinator workflow из repo: сначала AGENTS, UI Memory Bank, WORK_STATE и
AUTONOMOUS-WORKFLOW. Проверь фактическое устранение recorded blocker; мои слова не
заменяют test/model/contract evidence. Если устранён — обнови state, восстанови очередь
и продолжай автономно; иначе укажи точную оставшуюся причину и делай независимые tasks.
Scope прежний: core/holdout read restrictions, no main merge, только Control Center web v1.
