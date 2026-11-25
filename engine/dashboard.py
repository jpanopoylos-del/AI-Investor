"""
Builds docs/index.html — a single self-contained page that GitHub Pages serves.
Open it on your phone: https://<your-user>.github.io/ai-investor/

All data is embedded as JSON; a small script draws the chart (no libraries).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def _latest_proposal(root: Path) -> dict | None:
    files = sorted((root / "proposals" / "processed").glob("*.json")) + \
            sorted((root / "proposals" / "inbox").glob("*.json"))
    if not files:
        return None
    f = max(files, key=lambda p: p.name)
    try:
        doc = json.loads(f.read_text())
        doc["_file"] = f.name
        return doc
    except Exception:
        return None


def build_dashboard(root: Path) -> Path:
    data = {
        "summary": json.loads((root / "data" / "summary.json").read_text())
        if (root / "data" / "summary.json").exists() else None,
        "equity": _csv(root / "data" / "equity.csv"),
        "trades": _csv(root / "data" / "trades.csv")[-60:][::-1],
        "decisions": _csv(root / "data" / "decisions.csv")[-40:][::-1],
        "proposal": _latest_proposal(root),
        "quotes": json.loads((root / "data" / "quotes.json").read_text())
        if (root / "data" / "quotes.json").exists() else None,
        "discovered": json.loads((root / "data" / "discovered.json").read_text())
        if (root / "data" / "discovered.json").exists() else {},
    }
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(data, default=str).replace("</", "<\\/"))
    out = root / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    (root / "docs" / ".nojekyll").write_text("")
    return out


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Investor — paper portfolio</title>
<style>
:root{
  color-scheme:light;
  --bg:#f4f3ef; --surface:#fcfcfb; --line:#e4e2dc; --grid:#ecebe6;
  --ink:#0b0b0b; --ink2:#52514e; --ink3:#7a7974;
  --s1:#2a78d6; --s2:#eb6834; --good:#0ca30c; --bad:#d03b3b; --warn:#b37a00;
}
@media (prefers-color-scheme:dark){:root{
  color-scheme:dark;
  --bg:#111110; --surface:#1a1a19; --line:#2c2c2a; --grid:#262624;
  --ink:#f0efec; --ink2:#c3c2b7; --ink3:#8f8e86;
  --s1:#3987e5; --s2:#d95926; --good:#0ca30c; --bad:#e66767; --warn:#fab219;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:920px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink2);margin:28px 0 10px;font-weight:600}
.muted{color:var(--ink3);font-size:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px}
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
@media(min-width:640px){.tiles{grid-template-columns:repeat(4,1fr)}}
.tile .k{font-size:12px;color:var(--ink2)}
.tile .v{font-size:24px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums;margin-top:2px}
.tile .d{font-size:13px;font-variant-numeric:tabular-nums;margin-top:2px}
.up{color:var(--good)} .down{color:var(--bad)}
.legend{display:flex;gap:16px;font-size:13px;color:var(--ink2);margin-bottom:6px}
.sw{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
#chart{position:relative}
#chart svg{display:block;width:100%;height:auto;overflow:visible}
#tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;white-space:nowrap;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums}
th{text-align:left;font-weight:600;color:var(--ink2);font-size:12px;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:8px;border-bottom:1px solid var(--grid);vertical-align:top}
td.r,th.r{text-align:right}
td:first-child{white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:99px;border:1px solid currentColor}
.idea{padding:12px 0;border-bottom:1px solid var(--grid)} .idea:last-child{border:0}
.idea b{font-size:15px}
.thesis{color:var(--ink2);font-size:13.5px;margin-top:4px}
td.thesis-row{white-space:normal;padding-top:0}
td.thesis-row .thesis{position:sticky;left:12px;max-width:min(720px,calc(100vw - 72px))}
tr.pos td{border-bottom:none}
.empty{color:var(--ink3);font-size:14px;padding:6px 0}
details summary{cursor:pointer;color:var(--ink2);font-size:13px;margin-top:8px}
.foot{margin-top:36px;font-size:12px;color:var(--ink3)}
</style>
</head>
<body>
<main>
  <header>
    <h1>AI Investor · <span id="cap">paper</span> portfolio</h1>
    <span class="muted" id="asof"></span>
  </header>

  <section class="tiles" id="tiles"></section>

  <h2>Portfolio vs benchmark</h2>
  <div class="card">
    <div class="legend" id="legend"></div>
    <div id="chart"><div id="tip"></div></div>
    <details><summary>Show as table</summary><div class="scroll"><table id="eqtable"></table></div></details>
  </div>

  <h2>Open positions</h2>
  <div class="card scroll"><table id="positions"></table></div>

  <h2>Pending orders</h2>
  <div class="card scroll"><table id="pending"></table></div>

  <h2>Latest AI scan</h2>
  <div class="card" id="scan"></div>

  <h2>Discovered by the AI</h2>
  <div class="card scroll"><table id="discovered"></table></div>

  <h2>Engine decisions</h2>
  <div class="card scroll"><table id="decisions"></table></div>

  <h2>Trade log</h2>
  <div class="card scroll"><table id="trades"></table></div>

  <p class="foot">Simulated paper trading. No real money, no real orders, not financial advice.
  Fills at the next market open after each proposal, with fees and FX costs included.
  Fills are recorded within the hour after each open; live prices refresh hourly in market hours (Yahoo, ~15 min delayed).
  Times are Athens time.</p>
</main>
<script>
const D = /*__DATA__*/null;
const $ = id => document.getElementById(id);
const eur = (v,d=2) => "€" + Number(v).toLocaleString("en-IE",{minimumFractionDigits:d,maximumFractionDigits:d});
const pct = v => (v>0?"+":"") + Number(v).toFixed(2) + "%";
const px = (v,c) => (c==="USD"?"$":"€") + Number(v).toLocaleString("en-IE",{minimumFractionDigits:2,maximumFractionDigits:2});
const shares = p => { let q = p.qty;
  if (q == null) { const t = (D.trades||[]).find(t => t.symbol===p.symbol && t.side==="BUY"); q = t ? Number(t.qty) : null; }
  return q == null ? "–" : Number(q).toLocaleString("en-IE",{maximumFractionDigits:4}); };
const sign = v => v>0 ? '<span class="up">▲ '+pct(v)+'</span>' : v<0 ? '<span class="down">▼ '+pct(v)+'</span>' : '<span>'+pct(v)+'</span>';
const esc = s => String(s ?? "").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const S = D.summary, Q = D.quotes;
const START = (S && S.starting_cash_eur) || 1000;
document.getElementById("cap").textContent = eur(START,0) + " paper";
const QP = {}; if(Q) for(const q of Q.positions||[]) QP[q.symbol]=q;
if(!S){ $("tiles").innerHTML='<div class="card empty" style="grid-column:1/-1">No runs yet — the first daily run will fill this page.</div>'; }
else{
  $("asof").textContent = (Q ? "live prices " + Q.updated_athens + " Athens · " : "") + "closed books " + S.as_of + " · started " + S.start_date;
  const ret = Q ? Q.live_return_pct : S.return_pct, bret = Q ? Q.benchmark.return_pct : S.benchmark_return_pct;
  const alpha = ret - bret;
  $("tiles").innerHTML = [
    ["Portfolio value"+(Q?" (live)":""), eur(Q ? Q.live_equity_eur : S.equity_eur), sign(ret)],
    ["Benchmark ("+S.benchmark+")", eur(Q ? Q.benchmark.value_eur : START*(1+bret/100)), sign(bret)],
    ["Ahead of benchmark", (alpha>=0?"+":"")+alpha.toFixed(2)+" pts", alpha>=0?'<span class="up">beating it</span>':'<span class="down">trailing it</span>'],
    ["Cash", eur(S.cash_eur), '<span class="muted">'+S.positions.length+(S.positions.length===1?' position · ':' positions · ')+S.open_slots+' slots free'+(S.buying_halted?' · <b class="down">buying halted</b>':'')+'</span>'],
  ].map(([k,v,d])=>`<div class="card tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join("");

  $("positions").innerHTML = S.positions.length ? `<tr><th>Symbol</th><th class="r">Shares</th><th class="r">Bought / Now</th><th class="r">Value</th><th class="r">P&amp;L</th><th class="r">Stop / Target</th><th>Since</th></tr>` +
    S.positions.map(p=>{const q=QP[p.symbol]; const now=q?q.price:p.last_close, val=q?q.value_eur:p.value_eur, pl=q?q.pnl_pct:p.pnl_pct;
      return `<tr class="pos"><td><b>${esc(p.symbol)}</b>${q?`<div class="muted">${q.live?"live "+esc(q.time_athens.slice(11)):"last close"}</div>`:""}</td><td class="r">${shares(p)}</td><td class="r">${px(p.entry_price,p.currency)}<div class="muted">${px(now,p.currency)}</div></td><td class="r">${eur(val)}<div class="muted">${p.weight_pct}%</div></td><td class="r">${sign(pl)}</td><td class="r">${p.stop_price.toFixed(2)}${q?` <span class="muted">${pct(q.to_stop_pct).replace(/\.\d+/,m=>m.slice(0,2))}</span>`:""}<div class="muted">${p.target_price.toFixed(2)}${q?" "+pct(q.to_target_pct).replace(/\.\d+/,m=>m.slice(0,2)):""}</div></td><td>${p.entry_date}</td></tr><tr><td colspan="7" class="thesis-row"><div class="thesis">${esc(p.thesis)}</div></td></tr>`}).join("")
    : '<tr><td class="empty">All cash — no open positions.</td></tr>';
}

// ---------- pending orders ----------
(function(){
  const P = (Q && Q.pending_buys) || [];
  $("pending").innerHTML = P.length ? '<tr><th>Symbol</th><th class="r">Price now</th><th>Market</th><th class="r">Stop / Target if filled now</th><th>From</th></tr>' +
    P.map(p=>`<tr><td><span class="pill" style="color:var(--s1)">BUY</span> <b>${esc(p.symbol)}</b></td><td class="r">${p.price==null?"–":px(p.price,p.currency)}<div class="muted">${p.price==null?"no quote":p.live?esc(p.time_athens.slice(11)):"last close"}</div></td><td>${p.market_open?"open — fills next hourly run":p.currency==="USD"?"waits for 16:30 open":"waits for 10:00 open"}</td><td class="r">${p.stop_if_filled_now==null?"–":p.stop_if_filled_now.toFixed(2)}<div class="muted">${p.target_if_filled_now==null?"":p.target_if_filled_now.toFixed(2)}</div></td><td class="muted">${esc(p.proposal_file)}</td></tr>`).join("")
    : '<tr><td class="empty">No pending orders.</td></tr>';
})();

// ---------- chart: two lines, crosshair + tooltip ----------
(function(){
  const rows = D.equity.map(r=>({d:r.date, p:+r.equity_eur, b:+r.benchmark_eur}));
  const host = $("chart");
  $("legend").innerHTML = '<span><i class="sw" style="background:var(--s1)"></i>AI portfolio</span><span><i class="sw" style="background:var(--s2)"></i>Benchmark</span>';
  $("eqtable").innerHTML = '<tr><th>Date</th><th class="r">Portfolio</th><th class="r">Benchmark</th></tr>' + rows.slice().reverse().map(r=>`<tr><td>${r.d}</td><td class="r">${eur(r.p)}</td><td class="r">${eur(r.b)}</td></tr>`).join("");
  if(rows.length < 2){ host.insertAdjacentHTML("beforeend",'<div class="empty">The chart appears after two trading days.</div>'); return; }
  const W=Math.max(300,host.clientWidth||860), H=W<520?230:300, m={l:48,r:W<520?62:90,t:12,b:26};
  const vals = rows.flatMap(r=>[r.p,r.b]).concat([START]);
  let lo=Math.min(...vals), hi=Math.max(...vals); const pad=(hi-lo)*.12||10; lo-=pad; hi+=pad;
  const x=i=>m.l+i*(W-m.l-m.r)/(rows.length-1), y=v=>m.t+(hi-v)*(H-m.t-m.b)/(hi-lo);
  const ticks=[]; const step=Math.pow(10,Math.floor(Math.log10((hi-lo)/4))); const st=[1,2,2.5,5,10].map(k=>k*step).find(s=>(hi-lo)/s<=5);
  for(let v=Math.ceil(lo/st)*st; v<=hi; v+=st) ticks.push(v);
  const path=k=>rows.map((r,i)=>(i?"L":"M")+x(i).toFixed(1)+","+y(r[k]).toFixed(1)).join("");
  const last=rows[rows.length-1], li=rows.length-1;
  let ly1=y(last.p), ly2=y(last.b); if(Math.abs(ly1-ly2)<16){const mid=(ly1+ly2)/2, s=ly1<ly2?-1:1; ly1=mid+s*8; ly2=mid-s*8;}
  const xl=(W<520?[0,li]:[0,Math.floor(li/2),li]).filter((v,i,a)=>a.indexOf(v)===i);
  host.insertAdjacentHTML("afterbegin",`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Portfolio value versus benchmark over time">
    ${ticks.map(t=>`<line x1="${m.l}" x2="${W-m.r}" y1="${y(t)}" y2="${y(t)}" stroke="var(--grid)"/><text x="${m.l-8}" y="${y(t)+4}" text-anchor="end" font-size="12" fill="var(--ink3)">€${Math.round(t)}</text>`).join("")}
    <line x1="${m.l}" x2="${W-m.r}" y1="${y(START)}" y2="${y(START)}" stroke="var(--ink3)" stroke-dasharray="3 4"/>
    ${xl.map(i=>`<text x="${x(i)}" y="${H-6}" text-anchor="${i===0?'start':i===li?'end':'middle'}" font-size="12" fill="var(--ink3)">${rows[i].d}</text>`).join("")}
    <path d="${path('b')}" fill="none" stroke="var(--s2)" stroke-width="2" stroke-linejoin="round"/>
    <path d="${path('p')}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${x(li)}" cy="${y(last.p)}" r="4" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/>
    <circle cx="${x(li)}" cy="${y(last.b)}" r="4" fill="var(--s2)" stroke="var(--surface)" stroke-width="2"/>
    <text x="${x(li)+10}" y="${ly1+4}" font-size="12" fill="var(--ink)" font-weight="600">${eur(last.p,0)}</text>
    <text x="${x(li)+10}" y="${ly2+4}" font-size="12" fill="var(--ink2)">${eur(last.b,0)}</text>
    <g id="xh" style="display:none"><line id="xhl" y1="${m.t}" y2="${H-m.b}" stroke="var(--ink3)"/><circle id="xh1" r="5" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/><circle id="xh2" r="5" fill="var(--s2)" stroke="var(--surface)" stroke-width="2"/></g>
    <rect x="${m.l}" y="0" width="${W-m.l-m.r}" height="${H}" fill="transparent" id="hit"/>
  </svg>`);
  const svg=host.querySelector("svg"), tip=$("tip"), xh=$("xh");
  const move=ev=>{const pt=(ev.touches?ev.touches[0]:ev); const b=svg.getBoundingClientRect(); const sx=(pt.clientX-b.left)*W/b.width;
    const i=Math.max(0,Math.min(li,Math.round((sx-m.l)/((W-m.l-m.r)/li)))); const r=rows[i];
    xh.style.display=""; $("xhl").setAttribute("x1",x(i)); $("xhl").setAttribute("x2",x(i));
    $("xh1").setAttribute("cx",x(i)); $("xh1").setAttribute("cy",y(r.p)); $("xh2").setAttribute("cx",x(i)); $("xh2").setAttribute("cy",y(r.b));
    tip.style.display="block"; tip.innerHTML=`<b>${r.d}</b><br><i class="sw" style="background:var(--s1)"></i>AI ${eur(r.p)}<br><i class="sw" style="background:var(--s2)"></i>Benchmark ${eur(r.b)}`;
    const px=x(i)*b.width/W; tip.style.left=Math.min(Math.max(px-70,0),b.width-160)+"px"; tip.style.top="8px";};
  const hide=()=>{xh.style.display="none"; tip.style.display="none";};
  const hit=$("hit"); hit.addEventListener("mousemove",move); hit.addEventListener("touchmove",move,{passive:true}); hit.addEventListener("touchstart",move,{passive:true});
  hit.addEventListener("mouseleave",hide); hit.addEventListener("touchend",()=>setTimeout(hide,1500));
})();

// ---------- latest AI scan ----------
(function(){
  const P=D.proposal, el=$("scan");
  if(!P){ el.innerHTML='<div class="empty">No AI scan yet.</div>'; return; }
  let h=`<div class="muted">${esc(P._file)} · created ${esc(P.created_at||"")}</div>`;
  if(P.market_view) h+=`<p class="thesis" style="font-size:14px;color:var(--ink)">${esc(P.market_view)}</p>`;
  const props=P.proposals||[];
  h += props.length ? props.map(p=>`<div class="idea"><span class="pill" style="color:${p.action==="BUY"?"var(--s1)":"var(--s2)"}">${esc(p.action)}</span> <b>${esc(p.symbol)}</b>
      <span class="muted">${p.size_pct?" · size "+Math.round(p.size_pct*100)+"%":""}${p.stop_loss_pct?" · stop −"+Math.round(p.stop_loss_pct*100)+"%":""}${p.take_profit_pct?" · target +"+Math.round(p.take_profit_pct*100)+"%":""}${p.confidence!=null?" · confidence "+Math.round(p.confidence*100)+"%":""}</span>
      <div class="thesis">${esc(p.thesis)}</div>${p.risks?`<div class="thesis"><b>Risks:</b> ${esc(p.risks)}</div>`:""}</div>`).join("")
    : '<div class="empty">No trades proposed — the agent chose to wait.</div>';
  if(P.watchlist&&P.watchlist.length) h+=`<div class="muted" style="margin-top:8px">Watchlist: ${P.watchlist.map(esc).join(", ")}</div>`;
  el.innerHTML=h;
})();

// ---------- discovered tickers ----------
(function(){
  const e=Object.entries(D.discovered||{});
  $("discovered").innerHTML = e.length ? '<tr><th>Symbol</th><th>First found</th><th>Last mentioned</th><th class="r">Mentions</th></tr>' +
    e.sort((a,b)=>b[1].last_seen.localeCompare(a[1].last_seen)).map(([s,v])=>`<tr><td><b>${esc(s)}</b></td><td>${v.first_seen}</td><td>${v.last_seen}</td><td class="r">${v.times_seen}</td></tr>`).join("")
    : '<tr><td class="empty">None yet — tickers the AI finds outside the universe list appear here and join the nightly screen.</td></tr>';
})();

// ---------- decisions + trades ----------
$("decisions").innerHTML = D.decisions.length ? '<tr><th>Date</th><th>Action</th><th>Result</th><th>Why</th></tr>' + D.decisions.map(d=>
  `<tr><td>${d.run_date}</td><td>${esc(d.action)} <b>${esc(d.symbol)}</b></td><td>${d.status==="FILLED"?'<span class="up">✓ filled</span>':'<span class="down">✕ rejected</span>'}</td><td>${esc(d.reason)}</td></tr>`).join("")
  : '<tr><td class="empty">No decisions yet.</td></tr>';
$("trades").innerHTML = D.trades.length ? '<tr><th>Date</th><th>Side</th><th>Symbol</th><th class="r">Price</th><th class="r">€ Value</th><th class="r">Fee</th><th class="r">P&amp;L</th><th>Reason</th></tr>' + D.trades.map(t=>
  `<tr><td>${t.date}</td><td>${t.side}</td><td><b>${esc(t.symbol)}</b></td><td class="r">${(+t.price).toFixed(2)} ${t.currency}</td><td class="r">${eur(t.value_eur)}</td><td class="r">${eur(t.fee_eur)}</td><td class="r">${t.pnl_eur===""?"":(+t.pnl_eur>=0?'<span class="up">+'+eur(t.pnl_eur)+'</span>':'<span class="down">−'+eur(Math.abs(t.pnl_eur))+'</span>')}</td><td class="muted">${esc(t.reason)}</td></tr>`).join("")
  : '<tr><td class="empty">No trades yet.</td></tr>';
</script>
</body>
</html>
"""
