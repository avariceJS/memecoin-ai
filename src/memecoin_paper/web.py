"""Read-only loopback Control Center. Only the explicit public projection is served."""
# ruff: noqa: E501

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML = r"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Brain · Live Control Center</title>
<style>
:root{color-scheme:dark;--bg:#0b1015;--card:#141d25;--line:#283540;--muted:#99acba;--accent:#82e8c0;--warn:#efc385}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef4f7;font:14px/1.5 system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:32px 40px}header{display:flex;justify-content:space-between;align-items:center;gap:24px}h1{font-size:32px;margin:0;letter-spacing:-1px}h2{font-size:20px;font-weight:600;margin:28px 0 12px}h3{margin:0;font-size:18px}p{color:var(--muted)}.eyebrow{color:var(--accent);font:11px monospace;letter-spacing:2px}.pill{border:1px solid var(--line);border-radius:30px;padding:8px 14px;display:inline-block}.warn{color:var(--warn)}.good{color:var(--accent)}.muted,small{color:var(--muted)}.banner{border-left:3px solid var(--warn);padding:12px 18px;background:#1e211f;margin:24px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat,.book{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}.value{font-size:28px;letter-spacing:-1px;display:block}.books{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.book .money{font-size:29px;margin:16px 0}.book h3{text-transform:uppercase}.book em{font-style:normal;font-size:11px;color:var(--accent);float:right}.row{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:7px 0;gap:12px}.panel{border:1px solid var(--line);border-radius:12px;overflow:auto;background:var(--card)}table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap}td,th{padding:12px 16px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.6px}code{font-size:11px;color:var(--muted)}.split>*{min-width:0}.split{display:grid;grid-template-columns:1fr 1fr;gap:20px}.empty{padding:24px;color:var(--muted)}footer{margin-top:30px;color:var(--muted);font-size:12px}svg{width:100%;height:140px}.dot{display:inline-block;width:8px;height:8px;background:var(--accent);border-radius:100%;margin-right:8px}#connection{font-size:12px}details{margin-top:10px}summary{cursor:pointer;color:var(--muted)}@media(max-width:1000px){main{padding:20px}.books,.split{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}header{align-items:flex-start;flex-direction:column}}
</style><main><header><div><div class="eyebrow">MEMECOIN AI / BRAIN DEVELOPMENT</div><h1>Живой Brain</h1><span class="muted">Рынок → прогноз → решение → виртуальный результат → обучение</span></div><div><span class="pill"><span class="dot"></span>Только виртуальные деньги</span><div id="connection" class="muted">Подключение…</div></div></header>
<div class="banner">Paper / price proxy · Цена GeckoTerminal, свеча 5 минут. Slippage 50 bps + fee 25 bps на сторону — допущение, не измеренное исполнение. Paper PnL не доказывает edge.</div>
<div class="stats"><div class="stat"><small>Свежие рынки / наблюдаемые</small><span class="value" id="markets">—</span></div><div class="stat"><small>Позиции / ожидают входа</small><span class="value" id="positions">—</span></div><div class="stat"><small>Закрытые виртуальные сделки</small><span class="value" id="trades">—</span></div><div class="stat"><small>Бары в локальном corpus</small><span class="value" id="corpus">—</span></div></div>
<div id="source-health" class="banner" hidden></div><h2>Модели и виртуальные портфели</h2><p>У каждой версии независимые $1 000. Вход $100, максимум 3 позиции. Сигнал: q50 &gt; 1,6% в simple return, q10 ≥ −25% log return. Вход на следующей новой доступной закрытой свече; выход по времени через 15 минут от свечи входа.</p><div class="books" id="books"><div class="empty">Ожидаем первые прогнозы после активации. История решений не дорисовывается.</div></div>
<h2>Что Brain наблюдает</h2><div class="panel" id="market"></div>
<div class="split"><section><h2>Последние решения</h2><div class="panel" id="signals"></div></section><section><h2>Виртуальные входы и выходы</h2><div class="panel" id="executions"></div></section></div>
<div class="split"><section><h2>День за днём: реализованный результат</h2><div class="panel" id="daily"></div></section><section><h2>Новые outcomes: paired forecast loss</h2><div class="panel" id="forecast"></div></section></div>
<h2>Обучение кандидатов</h2><p>Раз в сутки при ≥100 новых matured samples, с ограниченным числом запусков в этом rollout. Linear / GRU × два seed, chronological split. Обучение на рыночных targets; paper PnL используется для отдельной оценки политики. Автоматического promotion нет.</p><div class="panel" id="training"></div>
<footer id="footer"></footer></main>
<script>
const $=id=>document.getElementById(id), esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])), short=s=>String(s).slice(0,9), usd=n=>Number(n).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2}), when=n=>new Date(n/1e6).toLocaleString('ru-RU'), day=n=>new Date(n*864e5).toISOString().slice(0,10);
const reason={enter_next_observed_close:'Вошёл бы → ждёт новую свечу',median_below_cost_buffer:'Остаться в cash: прогноз ниже затрат',predicted_tail_risk:'Отказ: риск хвоста',already_exposed:'Позиция / заявка уже есть',position_limit:'Лимит трёх позиций',cash_limit:'Лимит средств',stale_or_quarantined:'Нет свежих допустимых данных',source_paused:'Источник остановлен',late_prediction_no_backfill:'Прогноз устарел'};
function table(headers,rows){return rows.length?'<table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')+'</tbody></table>':'<div class="empty">Пока нет наблюдений. Нулевой результат не подменяет отсутствие данных.</div>'}
function draw(d){let now=Date.now()*1e6, lag=(now-d.generated_ns)/1e9;const fresh=o=>o.quality==='active'&&now/1e9-o.close_s<=300&&now/1e9>=o.close_s;const active=d.source_ok&&now<d.expires_ns&&lag<120;
$('connection').textContent=(active?'Обновляется':'Пауза / устаревший снимок')+' · '+when(d.generated_ns)+' · '+Math.round(lag)+' с';$('connection').className=active?'good':'warn';
$('markets').textContent=d.market.filter(fresh).length+' / '+(d.source_health?.admitted_pools??d.market.length);$('positions').textContent=d.books.reduce((s,b)=>s+b.positions.length,0)+' / '+d.books.reduce((s,b)=>s+b.pending.length,0);$('trades').textContent=d.books.reduce((s,b)=>s+b.trades,0);$('corpus').textContent=d.corpus_bars.toLocaleString('ru-RU');
if(d.source_health){$('source-health').hidden=false;const h=d.source_health;$('source-health').textContent='Источник · '+h.admitted_pools+' рынков · '+h.bars+' candles · '+h.attempts+'/'+h.max_requests+' GET · '+h.captures+' raw captures · '+(h.cooldown_ns>now?'COOLDOWN до '+when(h.cooldown_ns):'сбор активен')}
if(d.books.length)$('books').innerHTML=d.books.map(b=>'<article class="book"><em>'+((b.candidate==='empirical'&&b.model_sha===d.champion_model_sha)?'CHAMPION':'CHALLENGER')+'</em><h3>'+esc(b.candidate)+'</h3><code>'+esc(short(b.model_sha))+'</code><div class="money">'+usd(b.equity)+' <small style="font-size:12px">equity · MTM'+(b.mark_stale?' / STALE':'')+'</small></div><div class="row"><span>Реализованный PnL</span><b>'+usd(b.realized)+'</b></div><div class="row"><span>Cash / комиссии</span><span>'+usd(b.cash)+' / '+usd(b.fees)+'</span></div><div class="row"><span>Позиции / pending / закрыто</span><span>'+b.positions.length+' / '+b.pending.length+' / '+b.trades+'</span></div><div class="row"><span>Решений / входов за всё время</span><span>'+(d.model_behavior?.[b.key]?.decisions??'—')+' / '+(d.model_behavior?.[b.key]?.entries??'—')+'</span></div><small>MTM по reference price, без будущих exit costs.</small></article>').join('');
$('market').innerHTML=table(['Pool','Цена USD','Закрытие свечи','Доступна','Возраст / качество'],d.market.map(o=>['<code>'+esc(o.pool)+'</code>',esc(Number(o.price).toPrecision(6)),esc(when(o.close_s*1e9)),esc(when(o.available_ns)), '<span class="'+(fresh(o)?'good':'warn')+'">'+Math.round((now/1e9-o.close_s)/60)+' мин · '+esc(o.quality)+'</span>']));
let signals=d.books.flatMap(b=>b.signals.map(s=>({...s,kind:b.candidate}))).sort((a,b)=>b.decision_ns-a.decision_ns).slice(0,24);
$('signals').innerHTML=table(['Время / модель','Pool / q50','Решение'],signals.map(s=>[esc(when(s.decision_ns))+'<br><code>'+esc(s.kind)+' '+esc(short(s.model_sha))+'</code>',esc(short(s.pool))+'<br>'+((Math.exp(s.q[1])-1)*100).toFixed(2)+'%',esc(reason[s.reason]||s.reason)]));
let executions=d.books.flatMap(b=>[...b.positions.map(p=>({...p,status:'Открыта',kind:b.candidate,version:b.model_sha})),...b.closed.map(p=>({...p,status:'Закрыта',kind:b.candidate,version:b.model_sha}))]).sort((a,b)=>(b.exit_ns||b.entry_ns)-(a.exit_ns||a.entry_ns)).slice(0,24);
$('executions').innerHTML=table(['Модель / pool','Вход → выход','Результат'],executions.map(p=>[esc(p.kind)+' '+esc(short(p.version))+'<br><code>'+esc(short(p.pool))+'</code>',esc(when(p.entry_ns))+'<br>→ '+esc(p.exit_ns?when(p.exit_ns):'не раньше '+when(p.exit_due_s*1e9)),esc(p.status)+'<br>'+(p.net_pnl!==undefined?usd(p.net_pnl):'MTM '+usd(Number(p.qty)*Number(p.mark_price)-Number(p.cost)))]));
$('daily').innerHTML=table(['UTC день','Версия / модель','Сделок','Net PnL'],d.daily.map(x=>[esc(day(x.UTC_day)),esc(short(x.book))+':'+esc(x.book.split(':')[1]),x.trades,usd(x.net_pnl)]));
$('forecast').innerHTML=table(['UTC день / версия','Горизонт / n','Empirical / Linear / GRU'],d.forecast_scores.map(x=>[esc(day(x.UTC_day))+'<br><code>'+esc(short(x.model_sha))+'</code>',x.horizon*5+' мин / '+x.n,['empirical','linear','gru'].map(k=>(x.loss_sums[k]/x.n).toFixed(5)).join(' / ')]));
$('training').innerHTML=table(['Статус / версия','Samples','Время','Promotion'],d.training.map(x=>[esc(x.status)+'<br><code>'+esc(short(x.model_sha||'reservation'))+'</code>',esc(x.matured),x.seconds?Number(x.seconds).toFixed(1)+' с':esc(when(x.started_ns||x.finished_ns)),x.promoted?'Да':'Нет · development only']));
$('footer').textContent='Активация '+when(d.activation_ns)+' · rollout до '+when(d.expires_ns)+' · ledger '+short(d.ledger_head)+' · '+d.events+' событий. Только локальный read-only интерфейс. Пауза компьютера означает паузу наблюдений; пропущенное время не восстанавливается как online.';
}
async function refresh(){try{const r=await fetch('/api/snapshot',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);draw(await r.json())}catch(e){$('connection').textContent='Нет свежего снимка · '+e.message;$('connection').className='warn'}}refresh();setInterval(refresh,10000);
</script></html>"""


def handler(root, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in allowed or self.headers.get(
                "Origin", f"http://127.0.0.1:{port}"
            ) not in {"http://" + h for h in allowed}:
                self.send_error(403)
                return
            if self.path == "/":
                data, kind = HTML.encode(), "text/html; charset=utf-8"
            elif self.path == "/api/snapshot" and (root / "dashboard.json").exists():
                raw = json.loads((root / "dashboard.json").read_text())
                data, kind = json.dumps(raw, allow_nan=False).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(root, port):
    ThreadingHTTPServer(("127.0.0.1", port), handler(root, port)).serve_forever()
