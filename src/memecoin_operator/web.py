# ruff: noqa: E501
"""Loopback operator control center. Writes require a process-local CSRF token."""

from __future__ import annotations

import html
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from memecoin_operator import watch_market
from memecoin_operator.control import (
    BODY_LIMIT,
    command,
    events,
    initialize,
    private_log,
    status,
    tick,
)
from memecoin_operator.preview import validate_preview

PAGE = r"""<!doctype html>
<html lang="ru">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Операторский центр</title>
<meta name="operator-token" content="__TOKEN__">
<style>
:root {
  color-scheme: dark;
  --bg: #0a1018;
  --card: #121b2e;
  --card-2: #182433;
  --line: #314158;
  --text: #e7eef8;
  --muted: #93a3bf;
  --faint: #b9c7dc;
  --accent: #d7c4a3;
  --teal: #8fd0c8;
  --warn: #e6c07b;
  --bad: #f0a8a8;
  --good: #9dceb0;
  --focus: #f4f7fb;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--text); }
body { font: 13px/1.4 ui-sans-serif, system-ui, sans-serif; min-height: 100vh; }
button, select, input { font: inherit; color: inherit; max-width: 100%; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.skip { position: absolute; left: 8px; top: -40px; background: var(--card); color: var(--text); padding: 6px 10px; z-index: 9; }
.skip:focus { top: 8px; }
.shell { max-width: 1440px; margin: 0 auto; padding: 14px 16px 28px; }
.layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 14px; align-items: start; }
.main, .identity { min-width: 0; }
.side { position: sticky; top: 8px; min-width: 0; border: 1px solid var(--line); background: var(--card); border-radius: 12px; padding: 10px; }
.side-label { color: var(--faint); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; }
.identity h1 { margin: 4px 0 0; font-size: 26px; letter-spacing: -.4px; font-weight: 600; }
.eyebrow { color: var(--accent); letter-spacing: .16em; font: 11px/1.2 ui-monospace, monospace; text-transform: uppercase; }
.sub, .muted { color: var(--muted); }
.top { display: flex; justify-content: space-between; gap: 12px; align-items: flex-end; margin-bottom: 10px; }
.links { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; justify-content: flex-end; min-width: 0; }
.links a, .pill { border: 1px solid var(--line); border-radius: 999px; padding: 4px 9px; color: var(--text); text-decoration: none; background: var(--card); }
.mode-ru { color: var(--accent); font-size: 12px; }
.live { color: var(--teal); }
.warn { color: var(--warn); }
.bad { color: var(--bad); }
.good { color: var(--good); }
.banner, .context, .synth { margin: 0 0 10px; padding: 8px 10px; background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--accent); color: var(--muted); }
.banner strong, .context strong { color: var(--text); font-weight: 600; }
.synth { border-left-color: var(--warn); color: var(--warn); font-weight: 650; letter-spacing: .03em; }
.actions { position: sticky; top: 0; z-index: 4; display: flex; flex-wrap: wrap; gap: 6px; align-items: end; padding: 8px; margin: 0 0 10px; background: rgba(10, 16, 24, .94); border: 1px solid var(--line); border-radius: 12px; }
.field { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1 1 140px; }
.field span { color: var(--faint); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
select, .actions button, dialog button { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; min-height: 34px; }
.actions button, dialog button { cursor: pointer; }
.actions button:disabled { cursor: not-allowed; opacity: .55; }
.actions button.danger { border-color: #6d3b3b; color: var(--bad); }
.actions button.gated { box-shadow: inset 0 0 0 1px var(--warn); }
.actions button.needs { box-shadow: inset 0 0 0 1px var(--accent); }
.gate-line { flex: 1 0 100%; margin: 0; min-height: 1.2em; color: var(--warn); }
.tabs { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.tabs button { text-align: left; background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 7px 8px; cursor: pointer; color: var(--muted); }
.tabs button[aria-selected="true"] { background: var(--card-2); color: var(--text); border-color: var(--accent); }
.grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, .75fr); gap: 8px; }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 8px; }
.card { background-color: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; min-width: 0; overflow-x: auto; }
.card h2 { margin: 0 0 6px; font-size: 14px; font-weight: 600; }
.card .hint { margin: -2px 0 6px; color: var(--muted); font-size: 12px; }
.value { font: 20px/1.15 ui-monospace, monospace; letter-spacing: -.3px; overflow-wrap: anywhere; }
.delta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.k { color: var(--faint); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
.stepper { display: flex; gap: 6px; flex-wrap: wrap; }
.step { flex: 1 1 90px; min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 6px; color: var(--faint); }
.step.on { color: var(--text); border-color: var(--accent); background: #1a2838; }
.step small { display: block; color: var(--muted); font-size: 11px; }
table { border-collapse: collapse; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--line); vertical-align: top; white-space: nowrap; }
th { color: var(--faint); font-size: 11px; font-weight: 500; letter-spacing: .04em; text-transform: uppercase; }
.panel { overflow-x: auto; max-width: 100%; }
.panel table { width: max-content; min-width: 100%; }
code, .mono { font: 12px/1.35 ui-monospace, monospace; color: var(--accent); white-space: nowrap; }
.empty { color: var(--muted); padding: 6px 0; }
svg.chart { width: 100%; height: 168px; display: block; overflow: hidden; }
svg.band { width: 100%; height: 132px; display: block; overflow: hidden; }
.check { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 8px; padding: 3px 0; border-bottom: 1px solid rgba(49, 65, 88, .7); align-items: start; }
.check b { overflow-wrap: anywhere; text-align: right; }
.tag { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; font-size: 11px; }
.tag.hot { color: var(--teal); border-color: #2f6f68; }
.tag.cold { color: var(--faint); }
.tag.quarantine { color: var(--bad); border-color: #6d3b3b; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.filters label { display: flex; flex-direction: column; gap: 3px; color: var(--faint); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; flex: 1 1 140px; }
.watch-form { display: flex; align-items: end; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.watch-form input[type="text"], .watch-form select { background: var(--card); border: 1px solid var(--line); border-radius: 8px; min-height: 34px; padding: 6px 8px; }
.watch-mode { display: flex; gap: 4px; min-height: 34px; align-items: center; border: 1px solid var(--line); border-radius: 8px; padding: 3px 7px; }
.watch-mode label { display: inline-flex; align-items: center; gap: 4px; color: var(--faint); }
.watch-mode input { accent-color: var(--teal); }
.watch-buttons { display: flex; flex-wrap: wrap; gap: 6px; }
.watch-buttons button { background: var(--card); border: 1px solid var(--line); border-radius: 8px; min-height: 34px; padding: 6px 9px; cursor: pointer; }
.watch-buttons button:disabled { opacity: .55; cursor: not-allowed; }
.watch-buttons .danger { border-color: #6d3b3b; color: var(--bad); }
.watch-market-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(250px, .8fr); gap: 8px; }
.watch-value { font: 20px/1.2 ui-monospace, monospace; overflow-wrap: anywhere; }
.watch-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.watch-status { min-height: 1.4em; color: var(--muted); margin: 4px 0 8px; }
.watch-reasons { color: var(--faint); overflow-wrap: anywhere; }
.watch-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.watch-action { font: 18px/1.2 ui-monospace, monospace; font-weight: 700; }
dialog { border: 1px solid var(--line); background: #101820; color: var(--text); border-radius: 14px; padding: 0; width: min(480px, calc(100vw - 24px)); }
dialog::backdrop { background: rgba(4, 8, 16, .72); }
.dialog-body { padding: 14px; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; padding: 0 14px 14px; }
.hidden { display: none; }
footer { margin-top: 14px; color: var(--faint); font-size: 12px; }
#side-next { margin-top: 8px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
@media (max-width: 1100px) {
  .grid, .stats { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  h1, .identity h1 { font-size: 22px; }
}
@media (max-width: 860px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  .side { position: static; }
  .tabs { flex-direction: row; flex-wrap: wrap; }
}
@media (max-width: 700px) {
  .grid, .watch-market-grid { grid-template-columns: minmax(0, 1fr); }
  .stats { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .top, .links { display: flex; flex-wrap: wrap; }
  .shell { padding: 10px; }
  .value { font-size: 18px; }
  .check { grid-template-columns: minmax(0, 1fr); }
  .check b { text-align: left; }
}
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
</style>
<body data-dev-readonly="__DEV_READONLY__">
__DEV_PREVIEW__
<a class="skip" href="#panel-overview">К обзору</a>
<div class="shell">
<div class="layout">
<nav class="side" aria-label="Разделы">
  <div class="side-label">Кабина</div>
  <div class="tabs" role="tablist" aria-label="Разделы">
    <button type="button" role="tab" id="tab-overview" aria-selected="true" aria-controls="panel-overview">Обзор</button>
    <button type="button" role="tab" id="tab-data" aria-selected="false" aria-controls="panel-data">Данные</button>
    <button type="button" role="tab" id="tab-training" aria-selected="false" aria-controls="panel-training">Обучение</button>
    <button type="button" role="tab" id="tab-models" aria-selected="false" aria-controls="panel-models">Модели</button>
    <button type="button" role="tab" id="tab-paper" aria-selected="false" aria-controls="panel-paper">Paper</button>
    <button type="button" role="tab" id="tab-watch" aria-selected="false" aria-controls="panel-watch">Watch Market</button>
    <button type="button" role="tab" id="tab-system" aria-selected="false" aria-controls="panel-system">Система</button>
  </div>
  <div id="side-next"></div>
</nav>
<main class="main">
<header class="top">
  <div class="identity">
    <div class="eyebrow">Memecoin AI · Operator</div>
    <h1>Операторский центр</h1>
    <div class="sub">Исследовательский контур · виртуальные деньги · без promotion</div>
  </div>
  <div class="links">
    <span class="pill" id="mode">режим —</span>
    <strong class="mode-ru" id="mode-ru"></strong>
    <span id="legacy-links"></span>
  </div>
</header>
<div id="synth" class="synth hidden" role="note"></div>
<div class="banner"><strong>Честная модель.</strong> GeckoTerminal, бары 5 минут. Цена — proxy: slippage 50 bps и комиссия 25 bps на сторону. Выход фиксированный через 15 минут, не MPC и не выученный exit. Champion empirical не заменяется. Кнопки promotion нет. Paper PnL не доказывает edge.</div>
<div id="toast" class="muted" role="status">Загрузка снимка…</div>
<div class="actions" aria-label="Команды">
  <label class="field"><span>Профиль сбора</span><select id="profile"><option value="balanced">ровный · balanced</option><option value="history">история · history</option></select></label>
  <button type="button" id="act-collect">Сбор</button>
  <button type="button" id="act-snapshot">Снимок</button>
  <label class="field"><span>Снимок для обучения</span><select id="snap"></select></label>
  <button type="button" id="act-train">Обучить</button>
  <label class="field"><span>Кандидат для paper</span><select id="cand"></select></label>
  <button type="button" id="act-paper">Paper exam</button>
  <button type="button" id="act-auto">Авто</button>
  <button type="button" id="act-pause">Пауза</button>
  <button type="button" id="act-resume">Продолжить</button>
  <button type="button" class="danger" id="act-stop">Стоп</button>
  <button type="button" class="danger" id="act-stop-all">Стоп всё</button>
  <p id="gate-line" class="gate-line" aria-live="polite"></p>
  <p id="stop-line" class="gate-line"></p>
</div>
<section id="panel-overview" role="tabpanel">
  <div class="stats">
    <div class="card stat"><div class="k">Фаза</div><div class="value" id="phase">—</div><div class="delta" id="phase-ru"></div></div>
    <div class="card stat"><div class="k">Matured</div><div class="value" id="matured">—</div><div class="delta" id="matured-delta"></div></div>
    <div class="card stat"><div class="k">Попытки / 7д</div><div class="value" id="runs">—</div><div class="delta" id="runs-delta"></div></div>
    <div class="card stat"><div class="k">Источник</div><div class="value" id="heartbeat">—</div><div class="delta" id="heartbeat-delta"></div></div>
  </div>
  <div class="grid">
    <article class="card"><h2>Рост корпуса</h2><div id="chart"></div></article>
    <article class="card"><h2>Следующий шаг</h2><div id="next"></div><div id="checks"></div></article>
  </div>
  <div class="card" style="margin-top:8px"><h2>Фазы</h2><div class="stepper" id="steps"></div></div>
</section>
<section id="panel-data" class="hidden" role="tabpanel">
  <article class="card"><h2>Источник и бюджет</h2><div id="source"></div><p id="borrow" class="hint"></p></article>
  <article class="card" style="margin-top:8px"><h2>Снимки</h2><p class="hint">Fold, recipe и provenance — только если проекция их прислала.</p><div class="panel" id="snapshots"></div></article>
  <article class="card" style="margin-top:8px"><h2>Рынки</h2><p class="hint">Hot / cold / quarantine, свежесть, событийные сутки и диапазоны по пулам. Пустая ячейка — пробел проекции, не ноль.</p><div class="panel" id="markets"></div></article>
</section>
<section id="panel-training" class="hidden" role="tabpanel">
  <div class="grid">
    <article class="card"><h2>Текущий fit</h2><div id="fit"></div></article>
    <article class="card"><h2>Квоты</h2><div id="quota"></div></article>
  </div>
  <article class="card" style="margin-top:8px"><h2>Задачи</h2><div class="panel" id="jobs"></div></article>
</section>
<section id="panel-models" class="hidden" role="tabpanel">
  <article class="card"><h2>Чемпион</h2><div id="champion"></div></article>
  <article class="card" style="margin-top:8px"><h2>Кандидаты и validation</h2><p class="hint">Реестр неизменяемых версий. Validation выбирает кандидата. Test здесь descriptive. Promotion нет.</p><div class="panel" id="models"></div></article>
</section>
<section id="panel-paper" class="hidden" role="tabpanel">
  <article class="card"><h2>Книги</h2><p class="hint">Каждая виртуальная книга независима и стартует со своих $1000. Сумма книг не является одним портфелем и здесь не рисуется.</p><label class="field"><span>Книга</span><select id="book"></select></label><div class="stats" id="book-stats" style="margin-top:8px">
    <div class="card stat"><div class="k">Кэш</div><div class="value" id="cash">—</div><div class="delta" id="cash-delta"></div></div>
    <div class="card stat"><div class="k">Equity</div><div class="value" id="equity">—</div><div class="delta" id="equity-delta"></div></div>
    <div class="card stat"><div class="k">Realized</div><div class="value" id="realized">—</div><div class="delta" id="realized-delta"></div></div>
    <div class="card stat"><div class="k">Комиссии</div><div class="value" id="fees">—</div><div class="delta" id="fees-delta"></div></div>
  </div><div class="panel" id="books"></div></article>
  <article class="card" style="margin-top:8px"><h2>Решения WAIT / ENTER / HOLD / EXIT</h2><p class="hint">Удержание — уже существующая или ожидающая экспозиция, не filled-позиция. Намерение входа ещё не сделка. Выход фиксированный 15 минут, не выученный и не MPC. Полоса ниже — квантили log return этого решения, не цена.</p><div id="band"></div><div class="panel" id="decisions"></div></article>
  <article class="card" style="margin-top:8px"><h2>Позиции и сделки</h2><div class="panel" id="ledger"></div></article>
  <article class="card" style="margin-top:8px"><h2>Прогнозы</h2><div class="panel" id="forecasts"></div></article>
</section>
<section id="panel-watch" class="hidden" role="tabpanel">
  <p class="hint">GeckoTerminal public snapshot может запаздывать. Brain использует закрытые 5-минутные бары; выбранный pool может быть вне обучающей выборки. Paper fill — ценовой proxy, не подтверждённое исполнение.</p>
  <div class="watch-form">
    <div class="field"><span>Адрес из Axiom</span><div class="watch-mode" role="group" aria-label="Тип адреса">
      <label><input type="radio" name="watch-kind" value="mint" checked> mint</label>
      <label><input type="radio" name="watch-kind" value="pool"> pool</label>
    </div></div>
    <label class="field"><span>Solana mint или pool</span><input id="watch-address" type="text" spellcheck="false" autocomplete="off" placeholder="Base58 address"></label>
    <div class="watch-buttons"><button type="button" id="watch-resolve">Найти рынок</button></div>
    <label class="field"><span>Pool</span><select id="watch-pool" disabled><option value="">Сначала найдите адрес</option></select></label>
    <label class="field"><span>Токен пары</span><select id="watch-mint" disabled><option value="">Выберите pool</option></select></label>
    <div class="watch-buttons"><button type="button" id="watch-start" disabled>Начать наблюдение</button><button type="button" class="danger" id="watch-stop" disabled>Остановить</button></div>
  </div>
  <div id="watch-status" class="watch-status" aria-live="polite">Watch Market остановлен.</div>
  <div class="watch-market-grid">
    <article class="card"><h2>Рынок</h2><div id="watch-chart"></div><div id="watch-market-meta" class="watch-meta">Нет выбранного рынка.</div></article>
    <article class="card"><h2>Brain · прогнозы</h2><div id="watch-forecasts" class="panel"></div><div id="watch-model-meta" class="watch-meta"></div></article>
  </div>
  <div class="grid" style="margin-top:8px">
    <article class="card"><h2>Решение</h2><div class="watch-actions"><div id="watch-action" class="watch-action">WAIT</div><div id="watch-reason" class="watch-reasons">Ожидание прогноза.</div></div></article>
    <article class="card"><h2>Виртуальный портфель</h2><div id="watch-portfolio"></div></article>
  </div>
  <article class="card" style="margin-top:8px"><h2>Сделки и сигналы</h2><div class="panel" id="watch-ledger"></div></article>
</section>
<section id="panel-system" class="hidden" role="tabpanel">
  <p class="muted" id="port-note">Живые консоли только из legacy[]. Порты 8790 и 8791 сами ссылками не становятся.</p>
  <div class="grid">
    <article class="card"><h2>Хост</h2><p class="hint">cache_age_s — возраст того же замера, не новый осмотр. null остаётся неизвестным.</p><div id="host"></div></article>
    <article class="card"><h2>Остановка</h2><div id="stop-effect"></div></article>
  </div>
  <article class="card" style="margin-top:8px"><h2>Восстановление источника</h2><div id="recovery"></div></article>
  <div class="grid" style="margin-top:8px">
    <article class="card"><h2>Срок и блокеры</h2><div id="blockers"></div></article>
    <article class="card"><h2>Сервисы</h2><div class="panel" id="services"></div></article>
  </div>
  <div class="grid" style="margin-top:8px">
    <article class="card"><h2>Команды</h2><div class="panel" id="commands"></div></article>
    <article class="card"><h2>Инциденты</h2><div class="panel" id="incidents"></div></article>
  </div>
  <article class="card" style="margin-top:8px"><h2>Журнал</h2>
    <div class="filters">
      <label>Фильтр журнала<input id="q" type="search" spellcheck="false" autocomplete="off"></label>
      <label>Вид<select id="kind"><option value="">все</option><option value="audit">audit</option><option value="history">history</option><option value="incident">incident</option><option value="revision">revision</option><option value="job">job</option></select></label>
    </div>
    <div id="log-note" class="hint"></div>
    <div class="panel" id="log"></div>
  </article>
</section>
<footer id="foot">Порт страницы подставит скрипт. Штатный контур 8792. Живые ссылки только из legacy[]. Стоп не считается выполненным до подтверждения адаптера. Продолжить не снимает доменный STOP.</footer>
</main>
</div>
</div>
<dialog id="dialog" aria-modal="true" aria-labelledby="dialog-title">
  <div class="dialog-body">
    <h2 id="dialog-title">Подтверждение</h2>
    <p id="dialog-text"></p>
  </div>
  <div class="dialog-actions">
    <button type="button" id="dialog-cancel">Отмена</button>
    <button type="button" id="dialog-ok" class="danger">Подтвердить</button>
  </div>
</dialog>
<script>
const esc = s => String(s ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const REASONS = {
  accepted: "Принято", duplicate: "Повтор без второго эффекта", expired: "Срок конфигурации истёк",
  quota: "Лимит обучений в 7-дневном окне", interval: "Интервал между попытками ещё не прошёл",
  insufficient_growth: "Мало новых matured с прошлого снимка", paused: "Пауза: новые задачи не стартуют",
  stopped: "Остановлено", busy: "Уже есть тяжёлая задача", domain_stop: "Доменный STOP этой кнопкой не снимается",
  ineligible: "Адаптер считает данные непригодными", same_snapshot: "Этот снимок уже шёл в обучение",
  missing_snapshot: "Снимок не найден", missing_candidate: "Кандидат не найден",
  partial_result: "Неполный результат не стал кандидатом", invalid_result: "Результат отклонён",
  deadline: "Превышен срок задачи", interrupted: "Прервано и не будет молча повторено",
  not_confirmed: "Нужно подтверждение", adapter_error: "Ошибка адаптера", adapter_unavailable: "Адаптер недоступен",
  unknown_growth: "Нет числа matured", collect_first: "Сначала сбор", mode_idle: "Автоцикл выключен",
  already_paused: "Уже на паузе", already_stopped: "Уже остановлено", already_auto: "Авто уже включено",
  already_running: "Уже выполняется", resumed: "Режим восстановлен", stop_all: "Запрос полной остановки",
  published: "Опубликовано", lease_lost: "Процесс потерял аренду родителя", inadequate_split: "Адаптер отклонил разбиение",
  config_unreadable: "config.json не прочитан", uninitialized: "База ещё не создана",
  exam_capacity: "Лимит открытых экзаменов", conflict: "Тот же request_id с другим телом",
  bad_token: "Неверный токен", bad_origin: "Чужой Origin", bad_host: "Чужой Host",
  auto: "Автоцикл включён", waiting: "Ожидание условий",
  median_below_cost_buffer: "медиана ниже буфера издержек",
  enter_next_observed_close: "вход на следующем наблюдаемом close",
  already_exposed: "уже есть экспозиция или pending",
  fixed_15m_exit: "фиксированный выход 15 минут", fixed_time_15m: "фиксированный выход 15 минут",
  observed_future_fill: "fill по уже наблюдаемому close"
};
const NEXT = {none: "нет шага", collect: "сбор", snapshot: "снимок", train: "обучение", paper: "paper exam", waiting: "ожидание", paused: "пауза", stopped: "стоп", idle: "простой"};
const MODE = {idle: "простой", collect: "сбор", paper: "paper", auto: "автоцикл", paused: "пауза", stopped: "стоп"};
const CHECK = {expiry: "срок", operator_mode: "режим оператора", domain_stop: "доменный STOP", busy: "занятость", eligible: "пригодность", growth: "прирост", interval: "интервал", quota: "квота", initial_collect: "первый сбор"};
const STAGE = {intent: "intent · намерение", fill: "fill · исполнение", existing_or_pending_exposure: "existing_or_pending_exposure · уже в рынке или pending", decision: "decision · без fill"};
const labelReason = code => REASONS[code] || (code ? String(code) : "—");
const say = code => esc(labelReason(code));
const $ = id => document.getElementById(id);
const DEV_READ_ONLY = document.body.dataset.devReadonly === "true";
const miss = text => '<span class="muted">' + esc(text) + "</span>";
let last = null;
let commandNote = "";
let pending = null;
let eventRows = [];
let eventsTruncated = false;
let eventsError = "";
let bookKey = "";
let watchResolution = null;
let watchView = null;
function formatReason(code) {
  const human = labelReason(code);
  if (!code) return human;
  return human.indexOf(String(code)) < 0 ? human + " · " + code : human;
}
function statusLine(d) {
  const base = "Снимок rev " + d.revision + " · " + labelReason(d.phase);
  return commandNote ? base + " · " + commandNote : base;
}
function absorbTerminal(d) {
  const jobs = (d.jobs || []).slice().sort((a, b) => Number(b.finished_ns || b.created_ns || 0) - Number(a.finished_ns || a.created_ns || 0));
  const job = jobs[0];
  if (!job || (job.status !== "failed" && job.status !== "interrupted") || !job.reason) return;
  const acceptance = !commandNote || /^(Принято|Опубликовано)(?:\s|·|$)/.test(commandNote);
  if (acceptance) commandNote = formatReason(job.reason);
}
function noteText(text) {
  commandNote = text;
  $("toast").textContent = last ? statusLine(last) : text;
  $("toast").className = "warn";
}
function note(code) { noteText(labelReason(code)); }
function table(headers, rows) {
  if (!rows.length) return '<div class="empty">Пока пусто. Ноль не подставляется вместо отсутствия данных.</div>';
  return "<table><thead><tr>" + headers.map(h => "<th>" + esc(h) + "</th>").join("") + "</tr></thead><tbody>" +
    rows.map(r => "<tr>" + r.map(c => "<td>" + c + "</td>").join("") + "</tr>").join("") + "</tbody></table>";
}
function kv(pairs) {
  return pairs.map(pair => '<div class="check"><span>' + esc(pair[0]) + "</span><b>" + pair[1] + "</b></div>").join("");
}
function finite(value) {
  if (typeof value === "boolean" || value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function asNs(value) {
  const n = finite(value);
  if (n === null) return null;
  if (n > 1e15) return n;
  if (n > 1e11) return n * 1e6;
  if (n > 1e8) return n * 1e9;
  return null;
}
function fmtTime(ns) {
  const n = asNs(ns);
  if (n === null) return null;
  const date = new Date(n / 1e6);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("ru-RU", {hour12: false});
}
function fmtAge(ns, now) {
  const left = asNs(ns);
  const right = asNs(now);
  if (left === null || right === null) return null;
  const delta = Math.round((right - left) / 1e9);
  const abs = Math.abs(delta);
  const unit = abs < 90 ? abs + " с" : abs < 5400 ? Math.round(abs / 60) + " мин" : abs < 172800 ? (abs / 3600).toFixed(1) + " ч" : (abs / 86400).toFixed(1) + " сут";
  return delta >= 0 ? unit + " назад" : "через " + unit;
}
function whenText(ns, now) {
  const clock = fmtTime(ns);
  const age = fmtAge(ns, now);
  if (!clock && !age) return miss("время не пришло");
  return esc([clock, age].filter(Boolean).join(" · "));
}
function secondsText(value) {
  const n = finite(value);
  if (n === null) return "—";
  if (n === 0) return "0";
  if (n % 86400 === 0) return (n / 86400) + " сут";
  if (n % 3600 === 0) return (n / 3600) + " ч";
  return n + " с";
}
function deadlineText(ns, now) {
  if (ns === null || ns === undefined || ns === "") return "нет метки";
  const left = asNs(ns);
  const right = asNs(now);
  if (left === null) return "время не читается";
  if (right !== null && right >= left) return "уже можно";
  return fmtTime(left) || "время не читается";
}
function shortHash(value) {
  if (!value) return "—";
  const text = String(value);
  return text.length > 16 ? text.slice(0, 12) + "…" : text;
}
function redact(value) {
  if (typeof value === "string") {
    if (value.indexOf("operator.sqlite") >= 0 || /\/(Users|home|private|var|tmp)\//.test(value)) return "[путь скрыт]";
    return value;
  }
  if (Array.isArray(value)) return value.slice(0, 12).map(redact);
  if (value && typeof value === "object") {
    const out = {};
    Object.keys(value).slice(0, 16).forEach(key => { out[key] = redact(value[key]); });
    return out;
  }
  return value;
}
function evidence(value) {
  try {
    const text = JSON.stringify(redact(value));
    return "<code>" + esc(text.length > 280 ? text.slice(0, 280) + "…" : text) + "</code>";
  } catch (err) {
    return miss("evidence не сериализуется");
  }
}
function chart(points) {
  const usable = (points || []).filter(p => p && p.ns != null && (p.samples != null || p.bars != null));
  if (usable.length < 2) {
    const one = usable[0];
    return '<div class="empty">' + (one ? "Одно реальное наблюдение: matured " + esc(one.samples) + ", bars " + esc(one.bars) + ". Линия из одной точки не рисуется." : "Нет ряда. Точки пишутся из опросов источника, не из заглушки.") + "</div>";
  }
  const w = 720, h = 168, padL = 52, padR = 12, padT = 16, padB = 32;
  const xs = usable.map(p => Number(p.ns));
  const ys = usable.map(p => Number(p.samples == null ? p.bars : p.samples));
  const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  const minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
  const flat = minY === maxY;
  const y0 = flat ? minY - 1 : minY;
  const y1 = flat ? maxY + 1 : maxY;
  const spanX = maxX - minX;
  const sx = i => padL + (spanX ? (xs[i] - minX) / spanX : 0.5) * (w - padL - padR);
  const sy = i => padT + (1 - (ys[i] - y0) / (y1 - y0 || 1)) * (h - padT - padB);
  const line = usable.map((_, i) => (i ? "L" : "M") + sx(i).toFixed(1) + " " + sy(i).toFixed(1)).join(" ");
  const dots = usable.map((_, i) => '<circle cx="' + sx(i).toFixed(1) + '" cy="' + sy(i).toFixed(1) + '" r="3.2" fill="#8fd0c8"></circle>').join("");
  const yTop = (h - padB);
  const text = (x, y, value, anchor) => '<text x="' + x + '" y="' + y + '" text-anchor="' + anchor + '" fill="#d7e0ef" font-size="11" font-family="ui-monospace, monospace">' + esc(value) + "</text>";
  const start = fmtTime(xs[0]) || "t0";
  const end = fmtTime(xs[xs.length - 1]) || "t1";
  const svg = '<svg class="chart" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="Рост matured">' +
    '<line x1="' + padL + '" y1="' + padT + '" x2="' + padL + '" y2="' + yTop + '" stroke="#314158"></line>' +
    '<line x1="' + padL + '" y1="' + yTop + '" x2="' + (w - padR) + '" y2="' + yTop + '" stroke="#314158"></line>' +
    text(padL - 6, padT + 4, String(maxY), "end") +
    text(padL - 6, yTop, String(minY), "end") +
    text(padL, h - 8, start, "start") +
    text(w - padR, h - 8, end, "end") +
    text(padL + 6, 12, "samples", "start") +
    '<path d="' + line + '" fill="none" stroke="#8fd0c8" stroke-width="2"></path>' + dots + "</svg>";
  const caption = "matured " + ys[0] + " → " + ys[ys.length - 1] + " · точек " + usable.length +
    (flat ? " · уровень без изменения" : "") + (spanX ? "" : " · одинаковая метка времени") +
    " · линия соединяет опросы и не означает непрерывный рост";
  return svg + '<div class="muted">' + esc(caption) + "</div>";
}
function fillSelect(id, items, valueKey, label) {
  const el = $(id);
  const prev = el.value;
  el.innerHTML = items.length ? items.map(item => '<option value="' + esc(item[valueKey]) + '">' + esc(label(item)) + "</option>").join("") : '<option value="">нет</option>';
  if (prev) el.value = prev;
}
function listFrom(source, coverage) {
  if (Array.isArray(source.per_pool)) return source.per_pool;
  if (Array.isArray(coverage.per_pool)) return coverage.per_pool;
  if (Array.isArray(source.pools)) return source.pools;
  return null;
}
function laneOf(row, hot) {
  const raw = row.lane || row.disposition || row.status || row.class || "";
  if (raw === "quarantine" || row.quarantine === true || row.quarantined === true) return "quarantine";
  if (raw === "hot" || row.hot === true) return "hot";
  if (raw === "cold") return "cold";
  if (Array.isArray(hot) && row.pool) return hot.indexOf(row.pool) >= 0 ? "hot" : "cold";
  return "";
}
function rangeText(row) {
  const bits = [];
  const open = fmtTime(row.first_s != null ? row.first_s : row.first_ns);
  const close = fmtTime(row.last_s != null ? row.last_s : row.last_ns);
  if (open && close) bits.push(open + " — " + close);
  if (Array.isArray(row.ranges)) bits.push("отрезков " + row.ranges.length);
  if (Array.isArray(row.gaps)) bits.push("разрывов " + row.gaps.length);
  return bits.join(" · ");
}
function quarantineReason(source, row) {
  const direct = row.quarantine_reason || row.reason;
  if (typeof direct === "string" && direct) return direct;
  const list = source && source.quarantine;
  if (!Array.isArray(list) || !row.pool) return "";
  const found = list.find(item => item && (item.pool === row.pool || item.id === row.pool));
  return found && typeof found.reason === "string" ? found.reason : "";
}
function renderMarkets(d) {
  const source = d.source || {};
  const coverage = d.coverage || {};
  const rows = listFrom(source, coverage);
  if (rows === null) {
    $("markets").innerHTML = '<div class="empty">В статусе нет per_pool. Таблица рынков не выдумана.</div>';
    return;
  }
  if (!rows.length) {
    $("markets").innerHTML = '<div class="empty">per_pool пришёл пустым. Это не оценка нуля рынков вне списка.</div>';
    return;
  }
  const hot = Array.isArray(source.hot_pools) ? source.hot_pools : null;
  $("markets").innerHTML = table(["пул", "класс", "бары", "сутки", "диапазон", "свежесть"], rows.map(row => {
    const lane = laneOf(row, hot);
    let klass = lane ? '<span class="tag ' + lane + '">' + esc(lane) + "</span>" : miss("класс не в проекции");
    if (lane === "quarantine") {
      const why = quarantineReason(source, row);
      klass += why ? " " + esc(why) : "";
    }
    const days = row.event_days != null ? row.event_days : row.days;
    const fresh = row.freshness_s != null ? esc(row.freshness_s) + " с" : miss("нет по пулу");
    return [esc(row.pool || "—"), klass, row.bars == null ? miss("нет") : esc(row.bars), days == null ? miss("нет") : esc(days), esc(rangeText(row) || "диапазон не в проекции"), fresh];
  }));
}
function foldCell(retained, fold) {
  if (!retained || typeof retained !== "object") return miss("fold support не в проекции");
  const item = retained[fold];
  if (!item || typeof item !== "object") return miss("нет " + fold);
  const bits = [];
  ["decisions", "pools", "days"].forEach(key => { if (item[key] != null) bits.push(key + " " + item[key]); });
  return bits.length ? esc(bits.join(" · ")) : miss(fold + " пуст");
}
function recipeText(recipe) {
  if (!recipe || typeof recipe !== "object") return miss("recipe не в проекции");
  const bits = [];
  if (recipe.version) bits.push(String(recipe.version));
  if (recipe.steps != null) bits.push("steps " + recipe.steps);
  if (recipe.sha256) bits.push(shortHash(recipe.sha256));
  return bits.length ? esc(bits.join(" · ")) : miss("recipe пуст");
}
function testFlag(row) {
  if (row.historical_test_independent === false) return "reused/descriptive";
  if (row.historical_test_independent === true) return "заявка independent не подтверждается UI";
  const hist = row.historical_test || row.test;
  if (hist && typeof hist === "object" && hist.label) return String(hist.label) + (hist.independent === false ? " · descriptive" : "");
  return "";
}
function renderSnaps(d) {
  const now = d.now_ns;
  const rows = d.snapshots || [];
  $("snapshots").innerHTML = table(["id", "время", "matured", "hash", "train", "val", "test", "recipe", "флаг"], rows.map(s => {
    const retained = s.retained || s.support || null;
    const flag = testFlag(s);
    return [
      "<code>" + esc(s.dataset_id) + "</code>",
      whenText(s.created_ns, now),
      s.matured_samples == null ? miss("нет") : esc(s.matured_samples),
      "<code>" + esc(shortHash(s.snapshot_hash || s.dataset_sha)) + "</code>",
      foldCell(retained, "train"),
      foldCell(retained, "validation"),
      foldCell(retained, "test"),
      recipeText(s.recipe),
      flag ? esc(flag) : miss("флаг test не в проекции")
    ];
  }));
}
function plainValue(value) {
  if (value == null || value === "") return "";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) return value.map(plainValue).filter(Boolean).join(", ");
  return Object.keys(value).slice(0, 8).map(key => {
    const item = value[key];
    if (item && typeof item === "object" && item.pool_macro_pinball != null) return key + " " + item.pool_macro_pinball;
    return key + " " + plainValue(item);
  }).join(" · ");
}
function metricBlob(value) {
  if (value == null) return miss("нет");
  const text = plainValue(value);
  return text ? esc(text) : miss("пусто");
}
function candidateStatus(c) {
  if (!c.status) return miss("статус не в проекции");
  if (c.status === "failed") return "failed · обучение неуспешно";
  if (c.status === "incomplete") return "incomplete · обучение не завершено";
  return esc(c.status);
}
function supportText(c) {
  const support = c.support || c.retained;
  if (!support || typeof support !== "object" || !Object.keys(support).length) return miss("support не в проекции");
  const bits = ["train", "validation", "test"].map(fold => {
    const item = support[fold];
    if (!item || typeof item !== "object") return "";
    const parts = [];
    if (item.decisions != null) parts.push("decisions " + item.decisions);
    if (item.pools != null) parts.push("pools " + item.pools);
    return parts.length ? fold + " " + parts.join(" ") : "";
  }).filter(Boolean);
  return bits.length ? esc(bits.join(" · ")) : miss("support пуст");
}
function candidateReady(c) {
  if (!c || c.status === "failed" || c.status === "incomplete") return false;
  if (!c.model_sha) return false;
  const support = c.support;
  if (!support || typeof support !== "object" || Array.isArray(support)) return false;
  return Object.keys(support).some(key => {
    const item = support[key];
    return item && typeof item === "object" && (item.decisions != null || item.pools != null || item.days != null);
  });
}
function renderModels(d) {
  const champ = d.champion || {};
  const engine = d.engine || {};
  const learned = engine.learned_exit === false ? "learned exit нет" : engine.learned_exit === true ? "поле learned_exit истинно, контракт этапа его не принимает" : "learned_exit не в проекции";
  $("champion").innerHTML = '<div class="value">' + esc(champ.sha ? shortHash(champ.sha) : null) + '</div><div class="muted">promoted: нет · ' + esc(engine.champion_policy || "empirical_unchanged") + " · выход " + esc(engine.exit || "fixed_15m") + " · " + esc(learned) + " · mpc " + esc(engine.mpc === false ? "нет" : engine.mpc) + "</div>";
  $("models").innerHTML = table(["кандидат", "статус", "sha", "selected", "support", "validation", "test", "promotion"], (d.candidates || []).map(c => {
    const selected = c.selected_family ? String(c.selected_family) : plainValue(c.selected);
    const flag = testFlag(c);
    return [
      "<code>" + esc(c.candidate_id) + "</code>",
      candidateStatus(c),
      c.model_sha ? "<code>" + esc(shortHash(c.model_sha)) + "</code>" : miss("model_sha нет"),
      selected ? esc(selected) : miss("selected не в проекции"),
      supportText(c),
      metricBlob(c.validation),
      flag ? esc(flag) : miss("test не в проекции"),
      c.promoted === true ? '<span class="warn">поле promoted истинно, UI его не применяет</span>' : "нет"
    ];
  }));
}
function booksOf(d) {
  const paper = d.paper || {};
  if (Array.isArray(paper.books)) return paper.books;
  return null;
}
function money(value) {
  return value === null || value === undefined || value === "" ? null : String(value);
}
function bookId(book, index) {
  const exam = book && book.exam_id ? String(book.exam_id) : "";
  if (book && book.key) return exam + "|" + String(book.key);
  const sha = book && book.model_sha ? String(book.model_sha) : "";
  const backbone = book ? String(book.backbone || book.candidate || book.candidate_id || "") : "";
  if (sha || backbone) return exam + "|" + sha + ":" + backbone;
  return "row|" + index;
}
function bookLabel(book, champion) {
  const sha = book.model_sha ? shortHash(book.model_sha) : "sha нет";
  const backbone = book.backbone || book.candidate || book.candidate_id || "backbone нет";
  const exam = book.exam_id || "экзамен нет";
  const version = book.model_sha && champion && book.model_sha === champion ? "champion" : "candidate";
  return sha + " · " + backbone + " · " + exam + " · " + version;
}
function countCell(value) {
  if (Array.isArray(value)) return esc(value.length);
  if (value === null || value === undefined || value === "") return miss("нет в проекции");
  if (typeof value === "object") return miss("нет в проекции");
  return esc(value);
}
function sameBook(row, book) {
  if (!book) return true;
  if (row.exam_id && book.exam_id && row.exam_id !== book.exam_id) return false;
  if (row.model_sha && book.model_sha && row.model_sha !== book.model_sha) return false;
  const left = book.backbone || book.candidate || book.candidate_id;
  const right = row.backbone || row.candidate || row.candidate_id;
  if (left && right && left !== right) return false;
  return true;
}
function quantileTriple(q) {
  if (Array.isArray(q)) {
    if (q.length < 3) return null;
    const nums = [q[0], q[1], q[2]].map(Number);
    if (nums.some(item => !Number.isFinite(item))) return null;
    return {q10: nums[0], q50: nums[1], q90: nums[2]};
  }
  if (!q || typeof q !== "object") return null;
  const pick = (names) => {
    for (let i = 0; i < names.length; i += 1) {
      if (q[names[i]] != null && q[names[i]] !== "") return Number(q[names[i]]);
    }
    return null;
  };
  const q10 = pick(["q10", "p10"]);
  const q50 = pick(["q50", "p50"]);
  const q90 = pick(["q90", "p90"]);
  if ([q10, q50, q90].some(item => item === null || !Number.isFinite(item))) return null;
  return {q10: q10, q50: q50, q90: q90};
}
function quantText(q) {
  const triple = quantileTriple(q);
  if (!triple) return miss("квантили не в проекции");
  return esc("10 " + triple.q10.toFixed(4) + " · 50 " + triple.q50.toFixed(4) + " · 90 " + triple.q90.toFixed(4));
}
function bandPlot(triple) {
  if (!triple) return '<div class="empty">Нет квантилей решения. Полоса цены не рисуется.</div>';
  const values = [triple.q10, triple.q50, triple.q90, 0];
  let min = Math.min.apply(null, values);
  let max = Math.max.apply(null, values);
  if (min === max) { min -= 1; max += 1; }
  const w = 640, h = 128, padL = 36, padR = 36, axis = 78;
  const x = value => padL + (value - min) / (max - min) * (w - padL - padR);
  const anchorOf = px => px < 78 ? "start" : px > w - 78 ? "end" : "middle";
  const text = (px, py, value, anchor) => '<text x="' + px.toFixed(1) + '" y="' + py + '" text-anchor="' + anchor + '" fill="#d7e0ef" font-size="11" font-family="ui-monospace, monospace">' + esc(value) + "</text>";
  const x0 = x(0), x10 = x(triple.q10), x50 = x(triple.q50), x90 = x(triple.q90);
  const left = Math.min(x10, x90), right = Math.max(x10, x90);
  const beside = Math.abs(x0 - x50) < 64;
  return '<svg class="band" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="Квантили log return 15м">' +
    text(8, 16, "log return", "start") +
    '<line x1="' + padL + '" y1="' + axis + '" x2="' + (w - padR) + '" y2="' + axis + '" stroke="#314158"></line>' +
    '<rect x="' + left.toFixed(1) + '" y="52" width="' + Math.max(2, right - left).toFixed(1) + '" height="28" fill="#8fd0c8" opacity="0.4"></rect>' +
    '<line x1="' + x50.toFixed(1) + '" y1="36" x2="' + x50.toFixed(1) + '" y2="90" stroke="#d7c4a3" stroke-width="2"></line>' +
    '<line x1="' + x0.toFixed(1) + '" y1="30" x2="' + x0.toFixed(1) + '" y2="96" stroke="#e6c07b" stroke-dasharray="3 3"></line>' +
    text(x10, 112, "q10 " + triple.q10.toFixed(3), anchorOf(x10)) +
    text(x50, 30, "q50 " + triple.q50.toFixed(3), anchorOf(x50)) +
    text(x90, 112, "q90 " + triple.q90.toFixed(3), anchorOf(x90)) +
    text(beside ? Math.max(12, x0 - 6) : x0, 48, "0", beside ? "end" : anchorOf(x0)) +
    "</svg>" +
    '<div class="muted">Последнее решение: 10 ' + esc(triple.q10.toFixed(4)) + " · 50 " + esc(triple.q50.toFixed(4)) + " · 90 " + esc(triple.q90.toFixed(4)) + ". Ось — log return 15 минут, полоса q10–q90, черта q50, пунктир — ноль. Это не ряд цены и не realtime.</div>";
}
function utcDay(value) {
  const day = Number(value);
  if (!Number.isFinite(day)) return null;
  const date = new Date(day * 86400000);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString().slice(0, 10);
}
function pinballCell(sums, n, name) {
  const count = Number(n);
  if (!(count > 0)) return miss("среднее не считается");
  if (!sums || sums[name] == null || sums[name] === "") return miss("нет");
  const value = Number(sums[name]);
  if (!Number.isFinite(value)) return miss("нет");
  return esc((value / count).toFixed(5));
}
function horizonText(horizon) {
  if (horizon == null || horizon === "") return miss("горизонт не в строке");
  const value = Number(horizon);
  if (!Number.isFinite(value)) return miss("горизонт не читается");
  return esc(value * 5 + " мин");
}
function forecastTable(rows) {
  const note = '<p class="hint">Средний pinball = loss_sums / n только при n &gt; 0. Строка — одна версия, один горизонт и один UTC-день. Строки не складываются в общий loss и не доказывают edge. Минуты горизонта = horizon × 5, это не выход политики.</p>';
  const body = table(["UTC день", "версия", "горизонт", "n", "empirical", "linear", "gru"], rows.slice(0, 40).map(row => {
    const sums = row.loss_sums || {};
    const day = utcDay(row.UTC_day);
    return [
      day ? esc(day) : miss("день не читается"),
      row.model_sha ? "<code>" + esc(shortHash(row.model_sha)) + "</code>" : miss("sha нет"),
      horizonText(row.horizon),
      row.n == null ? miss("n нет") : esc(row.n),
      pinballCell(sums, row.n, "empirical"),
      pinballCell(sums, row.n, "linear"),
      pinballCell(sums, row.n, "gru")
    ];
  }));
  return note + body;
}
function lossBlob(forecasts) {
  if (!forecasts || typeof forecasts !== "object" || Array.isArray(forecasts)) return "";
  const loss = forecasts.loss || {};
  const names = Object.keys(loss);
  const head = "прогнозов " + (forecasts.prediction_count == null ? "—" : forecasts.prediction_count) + " · scored " + (forecasts.scored == null ? "—" : forecasts.scored) + " · pending " + (forecasts.pending == null ? "—" : forecasts.pending);
  return "<p>" + esc(head) + "</p>" + (names.length ? table(["имя", "n", "pinball sum"], names.map(name => {
    const item = loss[name] || {};
    return [esc(name), item.n == null ? miss("n нет") : esc(item.n), item.pinball_sum == null ? miss("нет") : esc(item.pinball_sum)];
  })) : "");
}
function renderPaper(d) {
  const paper = d.paper || {};
  const now = d.now_ns;
  const books = booksOf(d);
  const champion = d.champion && d.champion.sha;
  const select = $("book");
  if (books === null) {
    select.innerHTML = '<option value="">нет списка книг</option>';
    bookKey = "";
  } else if (!books.length) {
    select.innerHTML = '<option value="">список книг пуст</option>';
    bookKey = "";
  } else {
    select.innerHTML = books.map((book, index) => {
      const key = bookId(book, index);
      return '<option value="' + esc(key) + '">' + esc(bookLabel(book, champion)) + "</option>";
    }).join("");
    if (bookKey) select.value = bookKey;
    if (!select.value) select.value = select.options[0].value;
    bookKey = select.value;
  }
  const chosen = books && books.length ? books.find((book, index) => bookId(book, index) === bookKey) || books[0] : null;
  const cash = chosen ? money(chosen.cash) : null;
  const equity = chosen ? money(chosen.equity) : null;
  const realized = chosen ? money(chosen.realized) : null;
  const fees = chosen ? money(chosen.fees) : null;
  $("cash").textContent = cash == null ? "—" : cash;
  $("equity").textContent = equity == null ? "—" : equity;
  $("realized").textContent = realized == null ? "—" : realized;
  $("fees").textContent = fees == null ? "—" : fees;
  $("cash-delta").textContent = chosen ? "эта книга, не сумма экзамена" : "книги не пришли; сумма paper.cash не подставляется";
  const unrealized = chosen && chosen.unrealized != null ? chosen.unrealized : null;
  $("equity-delta").textContent = unrealized == null ? "нереализованный MTM не в проекции" : "MTM " + unrealized;
  $("realized-delta").textContent = chosen && chosen.mark_stale ? "mark stale" : "";
  $("fees-delta").textContent = "assumed costs, не execution truth";
  if (books && books.length) {
    $("books").innerHTML = table(["книга", "кэш", "equity", "realized", "fees", "open", "pending", "closed"], books.map(book => [
      esc(bookLabel(book, champion)),
      esc(money(book.cash) || "—"), esc(money(book.equity) || "—"), esc(money(book.realized) || "—"), esc(money(book.fees) || "—"),
      countCell(book.open_positions),
      countCell(book.pending),
      countCell(book.closed_trades != null ? book.closed_trades : (Array.isArray(book.closed) ? book.closed.length : null))
    ]));
  } else {
    $("books").innerHTML = '<div class="empty">' + (books === null ? "В статусе нет paper.books. Отдельные книги не собраны из суммы." : "Список книг пуст.") + "</div>";
  }
  const viewDecisions = chosen && Array.isArray(chosen.decisions) ? chosen.decisions : (paper.decisions || []).filter(row => sameBook(row, chosen));
  const viewPositions = chosen && Array.isArray(chosen.positions) ? chosen.positions : (paper.positions || []);
  const viewTrades = chosen && Array.isArray(chosen.closed) ? chosen.closed : (chosen && Array.isArray(chosen.trades) ? chosen.trades : (Array.isArray(paper.trades) ? paper.trades : []));
  const withQ = viewDecisions.filter(row => quantileTriple(row.q));
  withQ.sort((a, b) => Number(b.ns || 0) - Number(a.ns || 0));
  let triple = withQ.length ? quantileTriple(withQ[0].q) : null;
  if (!triple && chosen && Array.isArray(chosen.signals)) {
    const signals = chosen.signals.filter(row => quantileTriple(row.q));
    signals.sort((a, b) => Number(b.decision_ns || b.ns || 0) - Number(a.decision_ns || a.ns || 0));
    if (signals.length) triple = quantileTriple(signals[0].q);
  }
  $("band").innerHTML = bandPlot(triple);
  $("decisions").innerHTML = table(["время", "квантили", "действие", "stage", "причина", "pool"], viewDecisions.map(row => {
    const stageBits = [];
    stageBits.push(row.stage ? (STAGE[row.stage] || row.stage) : "stage не в проекции");
    if (row.filled === true) stageBits.push("filled");
    else if (row.filled === false) stageBits.push("не filled");
    else stageBits.push("filled не в проекции");
    if (row.action === "HOLD") stageBits.push("существующая или pending-экспозиция, не новая позиция");
    if (row.action === "ENTER" && row.filled !== true) stageBits.push("намерение, не сделка");
    const reason = row.reason ? say(row.reason) + (REASONS[row.reason] ? " <code>" + esc(row.reason) + "</code>" : "") : "—";
    return [whenText(row.ns, now), quantText(row.q), row.action ? esc(row.action) : '<span class="warn">не классифицировано</span>', esc(stageBits.join(" · ")), reason, esc(row.pool || "—")];
  }));
  const posRows = viewPositions.map(p => ["позиция", esc(p.pool || "—"), esc(p.qty || "—"), esc(p.status || "статус не в проекции"), p.exit_due_ns || p.exit_due_s ? "выход " + (fmtTime(p.exit_due_ns || p.exit_due_s) || "время не читается") : "срок выхода не в проекции"]);
  const tradeRows = viewTrades.map(t => ["сделка · fixed 15m", esc(t.pool || "—"), esc(t.net_pnl || "—"), "фиксированный 15 минут", whenText(t.exit_ns, now)]);
  $("ledger").innerHTML = table(["тип", "pool", "pnl / qty", "статус", "выход"], posRows.concat(tradeRows));
  const scores = Array.isArray(paper.forecast_scores) ? paper.forecast_scores : (Array.isArray(d.forecasts) ? d.forecasts : null);
  const blob = !Array.isArray(d.forecasts) && d.forecasts ? lossBlob(d.forecasts) : "";
  if (!scores && !blob) {
    $("forecasts").innerHTML = miss("прогнозные loss не в проекции");
    return;
  }
  $("forecasts").innerHTML = (scores ? forecastTable(scores) : "") + blob;
}
function borrowText(source) {
  const rule = "До expiry B09 сбор заимствует защищённый источник. Выбранный профиль не меняет его cadence и ждёт successor. Пауза останавливает оператор, не legacy. Стоп всё после подтверждения включает allowlist legacy. Продолжить не снимает доменный STOP.";
  if (source.borrowed === true || source.profile_effective === "frozen_B09") return rule + " Сейчас проекция помечает borrowed" + (source.profile_effective ? ", профиль " + source.profile_effective : "") + ".";
  if (source.borrowed === false) return "Проекция: источник не borrowed. " + rule;
  return "Признак borrowed не в проекции. " + rule;
}
function renderSource(d) {
  const source = d.source || {};
  const coverage = d.coverage || {};
  const now = d.now_ns;
  const fresh = source.freshness_s == null ? miss("нет") : esc(source.freshness_s) + " с";
  const budget = (source.budget_used == null && source.budget_max == null) ? miss("нет") : esc(source.budget_used == null ? "—" : source.budget_used) + " / " + esc(source.budget_max == null ? "—" : source.budget_max);
  const age = source.heartbeat_age_s != null ? esc(source.heartbeat_age_s) + " с" : (fmtAge(source.heartbeat_ns, now) ? esc(fmtAge(source.heartbeat_ns, now)) : miss("возраст не пришёл"));
  $("source").innerHTML = kv([
    ["Провайдер", source.provider ? esc(source.provider) : miss("нет")],
    ["Бар, с", source.bar_seconds == null ? miss("нет") : esc(source.bar_seconds)],
    ["Свежесть", fresh],
    ["Heartbeat", age],
    ["Бюджет", budget],
    ["Попытки", source.attempts == null ? miss("нет") : esc(source.attempts)],
    ["Cooldown", esc(source.cooldown_until_ns == null ? "нет метки" : (Number(source.cooldown_until_ns) === 0 ? "нет" : deadlineText(source.cooldown_until_ns, now)))],
    ["STOP", source.stop_reason ? say(source.stop_reason) : miss("нет причины")],
    ["Срок источника", source.expires_ns == null ? miss("нет") : whenText(source.expires_ns, now)],
    ["Бары / пулы", esc(coverage.bars == null ? "—" : coverage.bars) + " / " + esc(coverage.pools == null ? "—" : coverage.pools)],
    ["Событийные сутки", coverage.event_days == null && source.event_days == null ? miss("нет") : esc(source.event_days != null ? source.event_days : coverage.event_days)],
    ["Один провайдер", source.single_provider === true ? "да" : source.single_provider === false ? "нет" : miss("не сказано")]
  ]);
  $("borrow").textContent = borrowText(source);
}
function legacyActive(item) {
  const name = String((item && (item.id || item.label)) || "");
  return /active/i.test(name) && item.stopped !== true && item.port != null;
}
function renderLegacyLinks(d) {
  if (DEV_READ_ONLY) {
    $("legacy-links").innerHTML = '<span class="pill">историческая копия · живые ссылки отключены</span>';
    return;
  }
  const items = Array.isArray(d.legacy) ? d.legacy.filter(legacyActive) : [];
  if (!items.length) {
    $("legacy-links").innerHTML = '<span class="pill">активный порт не в legacy[]</span>';
    return;
  }
  $("legacy-links").innerHTML = items.map(item => {
    const href = item.url || ("http://127.0.0.1:" + item.port);
    const name = item.label || item.id || "legacy";
    return '<a href="' + esc(href) + '">' + esc(name) + " · " + esc(item.port) + "</a>";
  }).join("");
}
function triState(value, yes, no) {
  if (value === true) return yes;
  if (value === false) return no;
  return miss("неизвестно");
}
function byteText(value) {
  const n = finite(value);
  if (n === null || n < 0) return null;
  if (n >= 1e12) return (n / 1e12).toFixed(2) + " ТБ";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " ГБ";
  return String(Math.round(n)) + " Б";
}
function serviceRows(d) {
  const source = d.source || {};
  const rows = [[
    "source",
    "—",
    triState(source.healthy, "да", "нет"),
    triState(source.stopped, "да", "нет"),
    source.stop_reason ? say(source.stop_reason) : "—"
  ]];
  (d.legacy || []).forEach(item => {
    const active = legacyActive(item);
    const note = active ? "активный из legacy[]" : (item.stopped === true ? "история, не живая ссылка шапки" : "в списке legacy[]");
    rows.push([
      esc(item.id || item.label || "legacy"),
      DEV_READ_ONLY ? miss("историческая копия") : item.port == null ? miss("порт не в проекции") : '<a href="' + esc(item.url || ("http://127.0.0.1:" + item.port)) + '">' + esc(item.port) + "</a>",
      triState(item.healthy, "да", "нет"),
      triState(item.stopped, "да", "нет"),
      esc(note)
    ]);
  });
  if (!(d.legacy || []).length) rows.push([miss("legacy[] пуст"), "—", miss("неизвестно"), miss("неизвестно"), "порты не выдуманы"]);
  return rows;
}
function renderHost(d) {
  const host = d.host;
  if (host == null || typeof host !== "object") {
    $("host").innerHTML = miss("status.host нет. Здоровье машины не выдумано.");
    return;
  }
  const age = host.cache_age_s;
  const ageText = age == null ? miss("возраст кэша неизвестен") : (Number(age) === 0 ? "свежий замер, cache_age_s 0" : "тот же замер из кэша, cache_age_s " + esc(age));
  const disk = host.disk || {};
  const power = host.power || {};
  const up = host.uptime || {};
  const op = host.operator || {};
  const free = byteText(disk.free_bytes);
  const total = byteText(disk.total_bytes);
  const diskText = free && total ? esc(free + " свободно / " + total) : (disk.reason ? esc(String(disk.reason)) : miss("неизвестно"));
  const powerBits = [];
  if (power.source) powerBits.push(String(power.source));
  if (power.percent != null) powerBits.push(power.percent + "%");
  const powerText = powerBits.length ? esc(powerBits.join(" · ")) : (power.reason ? esc(String(power.reason)) : miss("неизвестно"));
  const rows = [
    ["Кэш", ageText],
    ["Причина снимка", host.reason ? esc(String(host.reason)) : "—"],
    ["Диск", diskText],
    ["Питание", powerText],
    ["LaunchAgent loaded", triState(op.loaded, "да", "нет")],
    ["Расположение", op.disposition ? esc(String(op.disposition)) : miss("неизвестно")],
    ["Процесс, с", up.process_s == null ? (up.process_reason ? esc(String(up.process_reason)) : miss("неизвестно")) : esc(up.process_s)],
    ["Загрузка машины, с", up.boot_s == null ? (up.boot_reason ? esc(String(up.boot_reason)) : miss("неизвестно")) : esc(Math.round(Number(up.boot_s)))]
  ];
  let services = "";
  if (Array.isArray(host.services) && host.services.length) {
    services = table(["label", "loaded", "state", "причина"], host.services.map(item => [
      esc(item.label || "—"),
      triState(item.loaded, "да", "нет"),
      item.state ? esc(String(item.state)) : miss("неизвестно"),
      item.reason ? esc(String(item.reason)) : "—"
    ]));
  }
  $("host").innerHTML = kv(rows) + services;
}
function renderStop(d) {
  const fx = d.stop_effects;
  if (!fx || typeof fx !== "object") {
    $("stop-line").textContent = "stop_effects нет. Успех остановки не предполагается.";
    $("stop-effect").innerHTML = miss("stop_effects нет. Успех остановки не предполагается.");
    return;
  }
  const phase = fx.phase || "none";
  const confirmed = fx.confirmed === true && phase === "applied";
  const phrases = {
    none: "подтверждённой остановки нет",
    pending: "запрос ждёт адаптер, остановка не подтверждена",
    retrying: "повтор запроса, остановка не подтверждена",
    applied: confirmed ? "адаптер подтвердил остановку" : "фаза applied без confirmed, успех не заявляется",
    failed: "остановка не подтверждена, нужен оператор"
  };
  const line = phrases[phase] || "фаза не распознана, успех не заявляется";
  const attempts = (fx.attempts == null ? "—" : fx.attempts) + " / " + (fx.max_attempts == null ? "—" : fx.max_attempts);
  $("stop-line").textContent = line + (phase === "none" ? "" : " · попытки " + attempts);
  $("stop-effect").innerHTML = kv([
    ["Фаза", esc(line)],
    ["Вид", fx.kind ? esc(fx.kind) : miss("не задан")],
    ["Попытки", esc(attempts)],
    ["Причина", fx.reason ? say(fx.reason) : "—"],
    ["confirmed", confirmed ? "да" : "нет"],
    ["Нужен оператор", fx.needs_operator === true ? "да" : fx.needs_operator === false ? "нет" : miss("неизвестно")]
  ]);
}
function serviceBits(services) {
  if (Array.isArray(services)) {
    if (!services.length) return "список пуст";
    return esc(services.map(item => {
      if (item == null || item === "") return "—";
      if (typeof item !== "object") return String(item);
      const name = item.id || item.name || item.label || "сервис";
      const bits = [name];
      if (item.state) bits.push(String(item.state));
      if (item.paused === true) bits.push("paused");
      return bits.join(" ");
    }).join(" · "));
  }
  if (services && typeof services === "object") {
    const names = Object.keys(services);
    if (!names.length) return "список пуст";
    return esc(names.map(name => name + (services[name] && services[name].state ? " " + services[name].state : "")).join(" · "));
  }
  return miss("сервисы не в проекции");
}
function renderRecovery(d) {
  const rec = d.recovery;
  if (!rec || typeof rec !== "object" || Array.isArray(rec)) {
    $("recovery").innerHTML = miss("status.recovery нет в проекции. Ручное снятие паузы отсюда не заявляется.");
    return;
  }
  const paused = rec.paused === true ? "пауза источника" : rec.paused === false ? "паузы нет" : miss("paused не задан");
  const retry = rec.retry_after_ns == null || rec.retry_after_ns === "" ? "повтор не задан" : deadlineText(rec.retry_after_ns, d.now_ns);
  $("recovery").innerHTML = kv([
    ["Состояние", paused.indexOf("<") >= 0 ? paused : esc(paused)],
    ["Попытки resume", rec.resume_attempts == null ? miss("нет") : esc(rec.resume_attempts)],
    ["Повтор", esc(retry)],
    ["Сервисы", serviceBits(rec.services)],
    ["Ручное действие", esc("заблокировано: карточка не снимает паузу и не отправляет команду")]
  ]);
}
function renderSystem(d) {
  const now = d.now_ns;
  const gates = d.gates || {};
  const failed = (gates.checks || []).filter(item => item && item.pass === false);
  const blockers = failed.map(item => esc((CHECK[item.id] || item.id) + " · " + labelReason(item.reason)));
  (d.warnings || []).forEach(item => blockers.push(say(item)));
  ((d.limits || {}).reasons || []).forEach(item => blockers.push(say(item)));
  if (d.domain_stop) blockers.unshift("Доменный STOP. Продолжить его не снимает и файлы STOP не удаляет.");
  if (d.expired) blockers.unshift("Срок оператора истёк. Новые команды отклоняются.");
  const expiry = d.expires_ns == null ? miss("срок не задан") : (d.expired ? "истёк · " + (fmtTime(d.expires_ns) || "") : whenText(d.expires_ns, now));
  $("blockers").innerHTML = kv([
    ["Срок оператора", expiry],
    ["Доменный STOP", d.domain_stop ? "да" : "нет"],
    ["Режим после resume", d.resume_mode ? esc(MODE[d.resume_mode] || d.resume_mode) + " · " + esc(d.resume_mode) : miss("не записан")],
    ["Автопричина", d.auto_reason ? say(d.auto_reason) : "—"]
  ]) + (blockers.length ? "<ul>" + blockers.map(item => "<li>" + item + "</li>").join("") + "</ul>" : '<p class="muted">Блокеров в проекции нет.</p>');
  $("services").innerHTML = table(["сервис", "порт", "здоров", "остановлен", "заметка"], serviceRows(d));
  $("commands").innerHTML = table(["время", "запрос", "действие", "причина"], (d.commands || []).map(c => [whenText(c.created_ns, now), "<code>" + esc(c.request_id) + "</code>", esc(c.action), say(c.reason)]));
  $("incidents").innerHTML = table(["время", "id", "причина", "evidence"], (d.incidents || []).map(i => [whenText(i.ns, now), esc(i.event_id), say(i.reason) + (i.reason && REASONS[i.reason] ? " <code>" + esc(i.reason) + "</code>" : ""), evidence(i.summary)]));
}
function compressNotes(notes) {
  const order = [];
  const groups = {};
  notes.forEach(note => {
    const cut = note.indexOf(": ");
    const name = cut < 0 ? note : note.slice(0, cut);
    const why = cut < 0 ? "" : note.slice(cut + 2);
    const key = why || name;
    if (!groups[key]) {
      groups[key] = [];
      order.push(key);
    }
    groups[key].push(name);
  });
  return order.map(key => {
    const names = groups[key];
    if (names.length === 1 && names[0] === key) return names[0];
    return names.join(", ") + ": " + key;
  }).join(" · ");
}
function stepText(fit) {
  if (!fit || (fit.step == null && fit.steps == null)) return "шаг не опубликован";
  const left = fit.step == null ? "нет" : String(fit.step);
  const right = fit.steps == null ? "нет" : String(fit.steps);
  return left + " / " + right;
}
function paintActions(d) {
  const gates = d.gates || {};
  const manual = gates.manual || {};
  const notes = [];
  function apply(id, action) {
    const slot = manual[action];
    const reasons = slot && slot.eligible === false ? (slot.reasons || ["ineligible"]) : [];
    const el = $(id);
    if (reasons.length) {
      el.classList.add("gated");
      el.dataset.block = reasons.map(labelReason).join(" · ");
      notes.push(el.textContent.replace(/\s+/g, " ").trim() + ": " + el.dataset.block);
    } else {
      el.classList.remove("gated");
      delete el.dataset.block;
    }
  }
  apply("act-collect", "collect");
  apply("act-snapshot", "snapshot");
  apply("act-train", "train");
  apply("act-paper", "paper");
  const autoReasons = [];
  if (d.mode === "paused") autoReasons.push("paused");
  if (d.mode === "stopped") autoReasons.push("stopped");
  if (d.expired) autoReasons.push("expired");
  if (d.domain_stop) autoReasons.push("domain_stop");
  if (gates.busy) autoReasons.push("busy");
  const auto = $("act-auto");
  if (autoReasons.length) {
    auto.classList.add("gated");
    auto.dataset.block = autoReasons.map(labelReason).join(" · ");
    notes.push("Авто: " + auto.dataset.block);
  } else {
    auto.classList.remove("gated");
    delete auto.dataset.block;
  }
  $("act-train").classList.toggle("needs", !$("snap").value);
  $("act-paper").classList.toggle("needs", !$("cand").value);
  if (!$("snap").value) notes.push("Обучить: снимок не выбран");
  if (!$("cand").value) notes.push("Paper exam: кандидат не выбран");
  $("gate-line").textContent = compressNotes(notes);
  ["act-collect", "act-snapshot", "act-train", "act-paper", "act-auto"].forEach(id => {
    const el = $(id);
    const blocked = DEV_READ_ONLY || Boolean(el.dataset.block);
    el.disabled = blocked;
    if (blocked) el.setAttribute("aria-disabled", "true");
    else el.removeAttribute("aria-disabled");
    if (blocked || el.classList.contains("needs")) el.setAttribute("aria-describedby", "gate-line");
    else el.removeAttribute("aria-describedby");
  });
}
function render(d) {
  last = d;
  const gates = d.gates || {};
  const source = d.source || {};
  const coverage = d.coverage || {};
  absorbTerminal(d);
  $("toast").textContent = statusLine(d);
  $("toast").className = d.domain_stop || d.expired ? "bad" : "live";
  $("mode").textContent = d.mode + " · " + d.phase;
  $("mode-ru").textContent = (MODE[d.mode] || d.mode || "—") + " · " + (NEXT[d.phase] || d.phase || "—");
  $("phase").textContent = d.phase || "—";
  $("phase-ru").textContent = NEXT[d.phase] || "";
  $("matured").textContent = gates.matured_now == null ? "—" : String(gates.matured_now);
  const delta = gates.matured_delta;
  $("matured-delta").textContent = delta == null ? "прирост не в проекции" : "прирост " + delta + " · порог " + (gates.minimum_new_matured == null ? "—" : gates.minimum_new_matured);
  $("runs").textContent = (gates.runs_used == null ? "—" : gates.runs_used) + " / " + (gates.runs_max == null ? "—" : gates.runs_max);
  $("runs-delta").textContent = "окно " + secondsText(gates.envelope_s);
  $("heartbeat").textContent = source.healthy === true ? "жив" : source.healthy === false ? "нет" : "—";
  $("heartbeat-delta").textContent = source.heartbeat_ns ? (fmtAge(source.heartbeat_ns, d.now_ns) || "возраст не читается") : "heartbeat не в проекции";
  $("chart").innerHTML = chart(coverage.history);
  const autoGate = gates.auto || {next_action: "none", eligible: false, reasons: []};
  $("next").innerHTML = '<div class="value">' + esc(autoGate.next_action) + '</div><div class="delta">' + esc(NEXT[autoGate.next_action] || "") + '</div><div class="' + (autoGate.eligible ? "good" : "warn") + '">' + (autoGate.eligible ? "можно перейти" : "ждёт") + '</div><div class="muted">' + (autoGate.reasons || []).map(say).join(" · ") + "</div>";
  $("checks").innerHTML = (gates.checks || []).map(item => '<div class="check"><span>' + esc((CHECK[item.id] || item.id) + " · " + item.id) + '</span><b class="' + (item.pass ? "good" : "warn") + '">' + (item.pass ? "да" : say(item.reason)) + "</b></div>").join("");
  $("side-next").textContent = "Дальше: " + (NEXT[autoGate.next_action] || autoGate.next_action || "—") + (autoGate.reasons && autoGate.reasons.length ? " · " + autoGate.reasons.map(labelReason).join(", ") : "");
  const order = [["collect", "Сбор"], ["snapshot", "Снимок"], ["train", "Обучение"], ["paper", "Paper"], ["waiting", "Ожидание"]];
  $("steps").innerHTML = order.map(pair => '<div class="step' + (d.phase === pair[0] ? " on" : "") + '">' + esc(pair[1]) + "<small>" + esc(pair[0]) + "</small></div>").join("");
  renderSource(d);
  renderMarkets(d);
  renderSnaps(d);
  const fit = d.training || {};
  $("fit").innerHTML = kv([
    ["job", fit.job_id ? "<code>" + esc(fit.job_id) + "</code>" : miss("нет")],
    ["статус", fit.status ? esc(fit.status) : miss("нет")],
    ["шаг", esc(stepText(fit))],
    ["checkpoint", fit.checkpoint_id ? "<code>" + esc(fit.checkpoint_id) + "</code>" : miss("не опубликован")],
    ["секунды / дедлайн", esc(fit.elapsed_s == null ? "—" : fit.elapsed_s) + " / " + esc(fit.deadline_s == null ? "—" : fit.deadline_s)]
  ]);
  $("quota").innerHTML = kv([
    ["Интервал", esc(secondsText(gates.min_refit_interval_s))],
    ["Порог matured", gates.minimum_new_matured == null ? miss("нет") : esc(gates.minimum_new_matured)],
    ["Прирост", gates.matured_delta == null ? miss("нет") : esc(gates.matured_delta)],
    ["Не раньше", esc(deadlineText(gates.not_before_ns, d.now_ns))]
  ]) + '<p class="muted">Первый train не ждёт интервал. Неудача тоже тратит попытку. Один и тот же снимок повторно не учится. Ручной снимок не требует прироста.</p>';
  $("jobs").innerHTML = table(["задача", "статус", "причина", "время"], (d.jobs || []).map(j => ["<code>" + esc(j.job_id) + "</code><br>" + esc(j.action), esc(j.status || "—"), j.reason ? say(j.reason) + " <code>" + esc(j.reason) + "</code>" : "—", whenText(j.finished_ns || j.created_ns, d.now_ns)]));
  renderModels(d);
  renderPaper(d);
  renderSystem(d);
  if (d.synthetic === true || d.data_kind === "synthetic") {
    $("synth").textContent = "SYNTHETIC · фикстура экрана, не рынок и не edge";
    $("synth").classList.remove("hidden");
  } else {
    $("synth").textContent = "";
    $("synth").classList.add("hidden");
  }
  const profile = $("profile");
  if (d.collect_profile === "history" || d.collect_profile === "balanced") profile.value = d.collect_profile;
  fillSelect("snap", d.snapshots || [], "dataset_id", s => s.dataset_id);
  fillSelect("cand", (d.candidates || []).filter(candidateReady), "candidate_id", c => c.candidate_id);
  paintActions(d);
  renderLegacyLinks(d);
  renderHost(d);
  renderStop(d);
  renderRecovery(d);
  $("foot").textContent = "Порт этой страницы " + location.port + ". Штатный контур 8792. Живые ссылки только из legacy[]. Остановка не считается выполненной, пока адаптер её не подтвердил. Продолжить не снимает доменный STOP. Revision " + d.revision + ".";
  renderLog();
}
function logRows() {
  const d = last || {};
  const rows = [];
  eventRows.forEach(item => rows.push({id: item.event_id, kind: item.kind || "audit", reason: item.reason, ns: item.ns, summary: item.summary, origin: "event"}));
  (d.timeline || []).forEach(item => rows.push({id: "rev-" + item.revision, kind: "revision", reason: item.reason, ns: item.ns, summary: {mode: item.mode, phase: item.phase, revision: item.revision}, origin: "status.timeline"}));
  (d.jobs || []).forEach(item => rows.push({id: item.job_id, kind: "job", reason: item.reason, ns: item.finished_ns || item.created_ns, summary: {action: item.action, status: item.status, origin: item.origin}, origin: "status.jobs"}));
  (d.incidents || []).forEach(item => rows.push({id: item.event_id, kind: "incident", reason: item.reason, ns: item.ns, summary: item.summary, origin: "status.incidents"}));
  const seen = {};
  const unique = [];
  rows.forEach(row => {
    const key = [row.origin, row.kind, row.id, row.reason].join("|");
    if (seen[key]) return;
    seen[key] = true;
    unique.push(row);
  });
  unique.sort((a, b) => Number(b.ns || 0) - Number(a.ns || 0));
  return unique;
}
function renderLog() {
  const query = ($("q").value || "").trim().toLowerCase();
  const kind = $("kind").value;
  let rows = logRows().filter(row => {
    if (kind && row.kind !== kind) return false;
    if (!query) return true;
    const blob = [row.id, row.kind, row.reason, row.origin, JSON.stringify(redact(row.summary || {}))].join(" ").toLowerCase();
    return blob.indexOf(query) >= 0;
  });
  const total = rows.length;
  if (rows.length > 200) rows = rows.slice(0, 200);
  const notes = [];
  if (eventsError) notes.push("Журнал не прочитан: " + eventsError);
  if (eventsTruncated) notes.push("Журнал длиннее окна чтения API.");
  if (total > rows.length) notes.push("Показаны 200 из " + total + ".");
  notes.push("Загружено событий API: " + eventRows.length + ".");
  $("log-note").textContent = notes.join(" ");
  $("log").innerHTML = table(["#", "вид", "причина", "время", "evidence"], rows.map(row => [
    esc(row.id), esc(row.kind) + "<br><span class=\"muted\">" + esc(row.origin) + "</span>",
    say(row.reason) + (row.reason ? " <code>" + esc(row.reason) + "</code>" : ""),
    whenText(row.ns, last && last.now_ns), evidence(row.summary)
  ]));
}
async function loadEvents() {
  let after = eventRows.reduce((last, row) => Math.max(last, Number(row.event_id) || 0), 0);
  const all = eventRows.slice();
  eventsTruncated = false;
  try {
    for (let i = 0; i < 8; i += 1) {
      const response = await fetch("/api/events?after=" + after + "&limit=200", {cache: "no-store"});
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      const batch = payload.events || [];
      all.push.apply(all, batch);
      if (batch.length < 200) break;
      const next = payload.next_after;
      if (next == null || next === after) { eventsTruncated = true; break; }
      after = next;
      if (i === 7) eventsTruncated = true;
    }
    eventRows = all;
    eventsError = "";
  } catch (err) {
    eventsError = err.message;
  }
  renderLog();
}
async function refresh() {
  const eventsPromise = loadEvents();
  try {
    const response = await fetch("/api/status", {cache: "no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (err) {
    $("toast").textContent = "Нет снимка: " + err.message;
    $("toast").className = "bad";
  }
  await eventsPromise;
}
function watchTime(ns) {
  return ns ? new Date(Number(ns) / 1e6).toLocaleTimeString() : "—";
}
function watchPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n >= 1 ? n.toLocaleString(undefined, {maximumFractionDigits: 6}) : n.toPrecision(6);
}
function watchChart(rows) {
  const values = (rows || []).map(row => Number(row.price)).filter(Number.isFinite);
  if (values.length < 2) return '<div class="empty">Ожидание ценовых баров.</div>';
  const low = Math.min.apply(null, values), high = Math.max.apply(null, values);
  const span = high - low || Math.abs(high) * 0.001 || 1;
  const points = values.map((value, index) => {
    const x = 8 + index * 624 / (values.length - 1);
    const y = 154 - (value - low) * 140 / span;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  return '<svg class="chart" viewBox="0 0 640 168" role="img" aria-label="Цена выбранного токена">' +
    '<polyline points="' + points + '" fill="none" stroke="#8fd0c8" stroke-width="2" vector-effect="non-scaling-stroke"/>' +
    '<text x="8" y="12" fill="#93a3bf" font-size="10">' + esc(watchPrice(high)) + '</text>' +
    '<text x="8" y="165" fill="#93a3bf" font-size="10">' + esc(watchPrice(low)) + '</text></svg>';
}
function renderWatch(view) {
  watchView = view;
  const running = view.status === "running";
  $("watch-start").disabled = DEV_READ_ONLY || running || !watchResolution || !view || view.status === "running";
  $("watch-stop").disabled = DEV_READ_ONLY || !running;
  if (running) {
    const age = view.last_success_ns ? Math.max(0, Date.now() - Number(view.last_success_ns) / 1e6) / 1000 : null;
    $("watch-status").textContent = "Наблюдение · " + esc(view.pool_name || view.pool || "рынок") +
      (age == null ? " · ожидание первого ответа" : " · источник обновлён " + Math.round(age) + " с назад") +
      (view.last_error ? " · " + esc(view.last_error) : "");
    $("watch-status").className = "watch-status " + (view.last_error ? "warn" : "live");
  } else {
    $("watch-status").textContent = DEV_READ_ONLY ? "Историческая копия: состояние Watch не включено." : view.status === "stopped" ? "Наблюдение остановлено." : "Watch Market остановлен.";
    $("watch-status").className = "watch-status";
  }
  const market = view.market || {};
  $("watch-chart").innerHTML = watchChart(view.price_history || []);
  const shownPrice = view.live_price || market.price;
  $("watch-market-meta").innerHTML = shownPrice
    ? '<div class="watch-value">$' + esc(watchPrice(shownPrice)) + '</div><div class="watch-meta">' +
      (view.live_price_kind === "provider_pool_snapshot" ? "Снимок pool provider · " + esc(watchTime(view.live_price_received_ns)) : "Последний закрытый 5-минутный бар") +
      ' · bar close ' +
      esc(watchTime(Number(market.close_s) * 1e9)) + ' · received ' + esc(watchTime(market.available_ns)) +
      ' · ' + esc(view.dex || "DEX") + ' · ' + esc(view.pool || "") + '</div>'
    : "Нет выбранного рынка.";
  const forecasts = view.forecasts || [];
  const forecastRows = forecasts.flatMap(item => Object.entries(item.q_by_model || {}).map(([model, q]) => {
    if (!Array.isArray(q) || q.length !== 3) return [esc(item.horizon_minutes + " мин"), esc(model), "—", "—", "—", "—"];
    return [
      esc(item.horizon_minutes + " мин"),
      esc(model + (model === "empirical" ? " · paper" : "")),
      esc((100 * (Math.exp(q[0]) - 1)).toFixed(2) + "%"),
      esc((100 * (Math.exp(q[1]) - 1)).toFixed(2) + "%"),
      esc((100 * (Math.exp(q[2]) - 1)).toFixed(2) + "%"),
      esc((100 * (Math.exp(q[2]) - Math.exp(q[0]))).toFixed(2) + " п.п.")
    ];
  }));
  $("watch-forecasts").innerHTML = forecasts.length
    ? table(["горизонт", "модель", "Q10", "медиана", "Q90", "ширина Q10–Q90"], forecastRows)
    : '<div class="empty">Brain ждёт 8 последовательных закрытых баров.</div>';
  $("watch-model-meta").textContent = forecasts.length
    ? "Frozen model pin · empirical используется paper-книгой · model " + String(view.model_sha || "").slice(0, 12) +
      " · forecast as-of " + watchTime(forecasts[forecasts.length - 1].predicted_ns)
    : view.model_sha ? "Frozen champion · empirical · model " + String(view.model_sha).slice(0, 12) : "";
  $("watch-action").textContent = view.action || "WAIT";
  $("watch-reason").textContent = (view.reason || "waiting_for_first_forecast") +
    (view.reason === "position_open_fixed_15m_exit" ? " · выход по фиксированному сроку" : "");
  const book = (view.portfolio || [])[0];
  $("watch-portfolio").innerHTML = book
    ? table(["кэш", "equity", "realized", "комиссии", "позиция", "pending"], [[
      esc("$" + Number(book.cash).toFixed(2)),
      esc("$" + Number(book.equity).toFixed(2)),
      esc("$" + Number(book.realized).toFixed(2)),
      esc("$" + Number(book.fees).toFixed(2)),
      esc((book.positions || []).length),
      esc((book.pending || []).length)
    ]]) + '<div class="watch-meta">Virtual only · assumptions: 50 bps slippage + 25 bps fee/side · ' +
      (book.mark_stale ? '<span class="warn">stale mark</span>' : "mark по последней цене") + '</div>'
    : '<div class="watch-meta">$1,000 · пустая книга до первого решения</div>';
  $("watch-ledger").innerHTML = table(["время", "действие", "причина", "цена / PnL"], (view.decisions || []).slice().reverse().map(row => {
    const action = row.kind === "exit" ? "EXIT" : row.kind === "entry" ? "ENTER · filled" :
      row.kind === "decision" && row.reason === "enter_next_observed_close" ? "ENTER · intent" : "WAIT";
    return [esc(watchTime(row.decision_ns || row.exit_ns || row.entry_ns)),
      esc(action), esc(row.reason || "—"), esc(row.price || row.net_pnl || "—")];
  }));
}
async function watchPost(action, params) {
  if (DEV_READ_ONLY) throw new Error("developer_read_only");
  const response = await fetch("/api/watch/" + action, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Operator-Token": document.querySelector('meta[name="operator-token"]').content},
    body: JSON.stringify({request_id: rid(), action: action, params: params || {}})
  });
  const body = await response.json();
  if (!response.ok || body.ok === false) throw new Error(body.reason || body.category || ("HTTP " + response.status));
  return body;
}
function renderPoolChoices() {
  const pools = watchResolution ? watchResolution.pools || [] : [];
  $("watch-pool").innerHTML = pools.length
    ? pools.map((pool, index) => '<option value="' + esc(pool.address) + '">' + esc(pool.name || pool.address) +
      (pool.dex ? " · " + esc(pool.dex) : "") +
      (pool.reserve_usd ? " · $" + esc(pool.reserve_usd) : "") + '</option>').join("")
    : '<option value="">Пулы не найдены</option>';
  $("watch-pool").disabled = DEV_READ_ONLY || !pools.length;
  const selected = pools.find(pool => pool.address === $("watch-pool").value) || pools[0];
  const tokens = selected ? [selected.base, selected.quote].filter(Boolean) : [];
  if (watchResolution && watchResolution.target_mint) {
    $("watch-mint").innerHTML = '<option value="' + esc(watchResolution.target_mint) + '">' + esc(watchResolution.target_mint) + '</option>';
    $("watch-mint").disabled = DEV_READ_ONLY;
  } else {
    $("watch-mint").innerHTML = tokens.length
      ? tokens.map(token => '<option value="' + esc(token.mint) + '">' + esc(token.symbol || token.name || token.mint) + " · " + esc(token.mint) + '</option>').join("")
      : '<option value="">Mint пары не вернулся</option>';
    $("watch-mint").disabled = DEV_READ_ONLY || !tokens.length;
  }
  $("watch-start").disabled = DEV_READ_ONLY || !pools.length || !tokens.length && !watchResolution?.target_mint || watchView?.status === "running";
}
async function refreshWatch() {
  try {
    const response = await fetch("/api/watch/status", {cache: "no-store"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    renderWatch(await response.json());
  } catch (err) {
    $("watch-status").textContent = "Watch Market: " + err.message;
    $("watch-status").className = "watch-status bad";
  }
}
function rid() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return "ui_" + Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");
}
const ACTION_BUTTON = {collect: "act-collect", snapshot: "act-snapshot", train: "act-train", paper: "act-paper", auto: "act-auto"};
async function send(action, params) {
  if (DEV_READ_ONLY) { noteText("developer_read_only"); return; }
  const button = ACTION_BUTTON[action];
  if (button && $(button).dataset.block) { noteText($(button).dataset.block); return; }
  $("toast").textContent = "Отправка " + action + "…";
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Operator-Token": document.querySelector('meta[name="operator-token"]').content},
      body: JSON.stringify({request_id: rid(), action: action, params: params || {}})
    });
    const body = await response.json();
    commandNote = labelReason(body.reason);
    if (body.reason && commandNote.indexOf(String(body.reason)) < 0) commandNote += " · " + body.reason;
    if ((action === "stop" || action === "stop_all") && body.ok) {
      commandNote = "Запрос записан. Остановка не подтверждена, пока адаптер не ответил.";
    }
    $("toast").textContent = last ? statusLine(last) : commandNote;
    $("toast").className = body.ok ? "good" : "bad";
    await refresh();
  } catch (err) {
    noteText("команда не ушла: " + err.message);
  }
}
function confirmBox(title, text) {
  $("dialog-title").textContent = title;
  $("dialog-text").textContent = text;
  const dialog = $("dialog");
  dialog.showModal();
  $("dialog-ok").focus();
  return new Promise(resolve => { pending = resolve; });
}
function settle(value) {
  const fn = pending;
  pending = null;
  if (fn) fn(value);
}
$("dialog-cancel").onclick = () => { $("dialog").close(); settle(false); };
$("dialog-ok").onclick = () => { $("dialog").close(); settle(true); };
$("dialog").addEventListener("cancel", () => settle(false));
$("dialog").addEventListener("keydown", event => {
  if (event.key !== "Tab") return;
  const nodes = [$("dialog-cancel"), $("dialog-ok")];
  const index = nodes.indexOf(document.activeElement);
  event.preventDefault();
  nodes[(index + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length].focus();
});
$("act-collect").onclick = () => send("collect", {profile: $("profile").value});
$("act-snapshot").onclick = () => send("snapshot", {});
$("act-train").onclick = () => {
  const id = $("snap").value;
  if (!id) { note("missing_snapshot"); return; }
  if ($("act-train").dataset.block) { noteText($("act-train").dataset.block); return; }
  send("train", {snapshot_id: id});
};
$("act-paper").onclick = () => {
  const id = $("cand").value;
  if (!id) { note("missing_candidate"); return; }
  if ($("act-paper").dataset.block) { noteText($("act-paper").dataset.block); return; }
  send("paper", {candidate_id: id});
};
$("act-auto").onclick = () => send("auto", {profile: $("profile").value});
$("act-pause").onclick = () => send("pause", {});
$("act-resume").onclick = () => send("resume", {});
$("act-stop").onclick = async () => {
  const yes = await confirmBox("Остановить оператор?", "Текущая задача оператора будет прервана. Чужие процессы B09/B10/B12 не останавливаются. Доказательства не удаляются. Доменный STOP этой кнопкой не снимается.");
  if (yes) send("stop", {confirm: true});
};
$("act-stop-all").onclick = async () => {
  const yes = await confirmBox("Остановить всё, включая legacy?", "Это также ставит на паузу allowlist прежних prospective сервисов B09/B10/B12. Доказательства не удаляются. Продолжить потом не снимает доменный STOP.");
  if (yes) send("stop_all", {confirm: true});
};
$("watch-resolve").onclick = async () => {
  const address = $("watch-address").value.trim();
  const kind = document.querySelector('input[name="watch-kind"]:checked').value;
  $("watch-status").textContent = "Запрос GeckoTerminal…";
  try {
    watchResolution = await watchPost("resolve", {address_kind: kind, address});
    renderPoolChoices();
    $("watch-status").textContent = (watchResolution.pools || []).length
      ? "Найдено пулов: " + watchResolution.pools.length + ". Выберите точный рынок и сторону токена."
      : "Пул не найден для этого адреса.";
    $("watch-status").className = "watch-status";
  } catch (err) {
    watchResolution = null;
    renderPoolChoices();
    $("watch-status").textContent = "Не удалось найти рынок: " + err.message;
    $("watch-status").className = "watch-status bad";
  }
};
$("watch-pool").onchange = renderPoolChoices;
$("watch-start").onclick = async () => {
  if (!watchResolution) return;
  try {
    const result = await watchPost("start", {
      resolution_id: watchResolution.resolution_id,
      pool: $("watch-pool").value,
      target_mint: $("watch-mint").value
    });
    $("watch-status").textContent = "Сессия " + result.session_id.slice(0, 12) + " запущена · model " + result.model_sha.slice(0, 12);
    await refreshWatch();
  } catch (err) {
    $("watch-status").textContent = "Watch не запущен: " + err.message;
    $("watch-status").className = "watch-status bad";
  }
};
$("watch-stop").onclick = async () => {
  const yes = await confirmBox("Остановить Watch Market?", "Остановить наблюдение выбранного пула и paper-книгу. AUTO и его источник не затрагиваются.");
  if (!yes) return;
  try {
    await watchPost("stop", {});
    await refreshWatch();
  } catch (err) {
    $("watch-status").textContent = "Не удалось остановить: " + err.message;
    $("watch-status").className = "watch-status bad";
  }
};
$("book").onchange = () => { bookKey = $("book").value; if (last) renderPaper(last); };
$("q").addEventListener("input", renderLog);
$("kind").addEventListener("change", renderLog);
document.querySelectorAll('[role="tab"]').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('[role="tab"]').forEach(node => {
      node.setAttribute("aria-selected", "false");
    });
    tab.setAttribute("aria-selected", "true");
    document.querySelectorAll('[role="tabpanel"]').forEach(panel => panel.classList.add("hidden"));
    $("panel-" + tab.id.slice(4)).classList.remove("hidden");
  };
});
document.querySelector('[role="tablist"]').addEventListener("keydown", event => {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const index = tabs.indexOf(document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
  next.focus();
  next.click();
});
if (DEV_READ_ONLY) {
  ["profile", "act-collect", "act-snapshot", "snap", "act-train", "cand", "act-paper",
    "act-auto", "act-pause", "act-resume", "act-stop", "act-stop-all", "watch-address",
    "watch-resolve", "watch-pool", "watch-mint", "watch-start", "watch-stop"].forEach(id => {
    const control = $(id);
    if (control) control.disabled = true;
  });
  document.querySelectorAll('[name="watch-kind"]').forEach(control => { control.disabled = true; });
}
refresh();
refreshWatch();
setInterval(refresh, 5000);
setInterval(refreshWatch, 5000);
</script>
</html>
"""


class OperatorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root: Path, port: int, *, read_only: bool = False) -> None:
        self.root = Path(root)
        self.read_only = read_only
        self.token = secrets.token_urlsafe(32)
        self.preview_banner = ""
        if read_only:
            preview = validate_preview(self.root)
            captured = html.escape(preview["source_snapshot_at"][:80])
            self.preview_banner = (
                '<div class="banner" role="alert"><strong>Developer read-only preview.</strong> '
                "Historical copied state; source snapshot at "
                f"<code>{captured}</code>. All write requests are disabled.</div>"
            )
        super().__init__(("127.0.0.1", port), OperatorHandler)
        self._halt = threading.Event()
        self._ticker: threading.Thread | None = None
        self._watch_ticker: threading.Thread | None = None
        if not read_only:
            self._ticker = threading.Thread(target=self._loop, name="operator-tick", daemon=True)
            self._ticker.start()
            self._watch_ticker = threading.Thread(
                target=self._watch_loop, name="watch-market-worker", daemon=True
            )
            self._watch_ticker.start()

    def _loop(self) -> None:
        while not self._halt.wait(1.0):
            try:
                tick(self.root)
            except Exception as exc:
                private_log(self.root, "tick_failed", exc)

    def _watch_loop(self) -> None:
        watch_root = self.root.resolve().parent / "watch-market"
        while not self._halt.wait(5.0):
            state_path = watch_root / "state.json"
            if state_path.is_symlink() or not state_path.is_file():
                continue
            try:
                state = json.loads(state_path.read_text())
                if state.get("status") == "running":
                    watch_market.tick(self.root)
            except Exception:
                continue

    def shutdown_background(self) -> None:
        self._halt.set()
        self.shutdown()
        self.server_close()
        if self._ticker is not None:
            self._ticker.join(timeout=2)
        if self._watch_ticker is not None:
            self._watch_ticker.join(timeout=20)


class OperatorHandler(BaseHTTPRequestHandler):
    server: OperatorServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _port(self) -> int:
        return int(self.server.server_address[1])

    def _hosts(self) -> set[str]:
        port = self._port()
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _origins(self) -> set[str]:
        return {"http://" + host for host in self._hosts()}

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, body: dict[str, Any]) -> None:
        safe = dict(body)
        safe.pop("http_status", None)
        encoded = json.dumps(safe, sort_keys=True, allow_nan=False).encode()
        self._send(code, encoded, "application/json")

    def _error(self, code: int, category: str, reason: str) -> None:
        self._json(code, {"ok": False, "category": category, "reason": reason})

    def _authorized_read(self) -> bool:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._error(403, "forbidden", "bad_host")
            return False
        if self.headers.get("Host") not in self._hosts():
            self._error(403, "forbidden", "bad_host")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self._origins():
            self._error(403, "forbidden", "bad_origin")
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorized_read():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = (
                PAGE.replace("__TOKEN__", self.server.token)
                .replace("__DEV_PREVIEW__", self.server.preview_banner)
                .replace("__DEV_READONLY__", "true" if self.server.read_only else "false")
                .encode()
            )
            self._send(200, page, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/bootstrap":
            self._json(
                200, {"schema": "operator-bootstrap-v1", "ok": True, "token": self.server.token}
            )
            return
        if parsed.path == "/api/status":
            if parsed.query:
                self._error(404, "bad_request", "not_found")
                return
            try:
                self._json(200, status(self.server.root))
            except Exception as exc:
                private_log(self.server.root, "status_failed", exc)
                self._error(500, "internal", "internal")
            return
        if parsed.path == "/api/watch/status":
            if parsed.query:
                self._error(404, "bad_request", "not_found")
                return
            if self.server.read_only:
                self._json(
                    200,
                    {
                        "schema": "watch-market-v1",
                        "status": "unavailable",
                        "reason": "developer_preview_watch_not_copied",
                        "auto_untouched": True,
                    },
                )
                return
            try:
                self._json(200, watch_market.status(self.server.root))
            except Exception:
                self._error(500, "internal", "watch_status_unavailable")
            return
        if parsed.path == "/api/events":
            query = parse_qs(parsed.query, keep_blank_values=False)
            if set(query) - {"after", "limit"}:
                self._error(400, "bad_request", "bad_params")
                return
            try:
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                self._error(400, "bad_request", "bad_params")
                return
            self._json(200, events(self.server.root, after, limit))
            return
        if parsed.path == "/api/command":
            self._error(405, "bad_request", "method")
            return
        self._error(404, "bad_request", "not_found")

    def do_POST(self) -> None:
        if self.server.read_only:
            self._error(405, "read_only", "developer_read_only")
            return
        if not self._authorized_read():
            return
        parsed = urlparse(self.path)
        watch_route = parsed.path in {
            "/api/watch/resolve",
            "/api/watch/start",
            "/api/watch/stop",
        }
        if (parsed.path != "/api/command" and not watch_route) or parsed.query:
            self._error(404, "bad_request", "not_found")
            return
        origin = self.headers.get("Origin")
        if origin not in self._origins():
            self._error(403, "forbidden", "bad_origin")
            return
        supplied = self.headers.get("X-Operator-Token", "")
        if not secrets.compare_digest(supplied, self.server.token):
            self._error(403, "forbidden", "bad_token")
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._error(400, "bad_request", "bad_content_type")
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._error(400, "bad_request", "bad_params")
            return
        if length < 0 or length > BODY_LIMIT:
            self._error(400, "bad_request", "body_limit")
            return
        raw = self.rfile.read(length)
        if len(raw) > BODY_LIMIT:
            self._error(400, "bad_request", "body_limit")
            return
        try:
            payload = json.loads(raw.decode())
        except (UnicodeError, json.JSONDecodeError):
            self._error(400, "bad_request", "bad_json")
            return
        if not isinstance(payload, dict):
            self._error(400, "bad_request", "bad_json")
            return
        request_id = payload.get("request_id")
        action = payload.get("action")
        params = payload.get("params", {})
        if not isinstance(request_id, str) or not isinstance(action, str):
            self._error(400, "bad_request", "bad_params")
            return
        if not isinstance(params, dict):
            self._error(400, "bad_request", "bad_params")
            return
        if watch_route:
            try:
                if parsed.path == "/api/watch/resolve":
                    body = watch_market.resolve(
                        self.server.root, params.get("address_kind"), params.get("address")
                    )
                elif parsed.path == "/api/watch/start":
                    body = watch_market.start(
                        self.server.root,
                        resolution_id=params.get("resolution_id"),
                        pool=params.get("pool"),
                        target_mint=params.get("target_mint"),
                    )
                else:
                    body = watch_market.stop(self.server.root)
                self._json(200, {"ok": True, **body})
            except ValueError as exc:
                reason = str(exc)
                known = {
                    "invalid_address",
                    "invalid_address_kind",
                    "invalid_resolution",
                    "watch_already_running",
                    "watch_pool_token_mismatch",
                    "watch_mint_mismatch",
                    "watch_champion_pin",
                    "watch_model_not_ready",
                }
                status_code = 409 if reason == "watch_already_running" else 400
                self._json(
                    status_code,
                    {
                        "ok": False,
                        "category": "watch_request",
                        "reason": reason if reason in known else "watch_request_failed",
                    },
                )
            except Exception:
                self._json(
                    502, {"ok": False, "category": "watch_provider", "reason": "watch_unavailable"}
                )
            return
        body = command(self.server.root, request_id, action, params)
        self._json(int(body.get("http_status", 500)), body)

    def do_PUT(self) -> None:
        self._error(405, "bad_request", "method")

    def do_DELETE(self) -> None:
        self._error(405, "bad_request", "method")


def make_server(root: Path, port: int, *, read_only: bool = False) -> OperatorServer:
    root = Path(root)
    if read_only:
        if root.is_symlink() or not root.is_dir() or not (root / "operator.sqlite").is_file():
            raise ValueError("developer_preview_root_invalid")
    else:
        initialize(root)
    return OperatorServer(root, port, read_only=read_only)


def serve(root: Path, port: int, *, read_only: bool = False) -> None:
    server = make_server(root, port, read_only=read_only)
    try:
        server.serve_forever()
    finally:
        server.shutdown_background()
