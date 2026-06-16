"""`agenticmeter ui` — a local, zero-dependency web viewer.

Stdlib http.server only (keeps the local-first, no-deps promise). Serves:
  GET /                  the single-page app
  GET /api/runs          recent runs (json)
  GET /api/run/<id>      one run: header + span tree + behavior insights (json)
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading
import webbrowser

from .sinks.sqlite import SQLiteSink, DEFAULT_PATH
from .analysis.insights import analyze


def _run_payload(spans):
    root = next((s for s in spans if s.parent_id is None), None)
    steps = sum(1 for s in spans if s.parent_id is not None)
    tokens = sum((s.total_tokens or 0) for s in spans)
    cost = sum((s.cost_usd or 0) for s in spans)
    dur = (root.duration_ms / 1000) if (root and root.duration_ms) else 0.0
    return {
        "header": {"name": root.name if root else "run", "steps": steps,
                   "tokens": tokens, "cost": cost, "duration": dur,
                   "status": root.status if root else "ok"},
        "spans": [{"span_id": s.span_id, "parent_id": s.parent_id,
                   "type": s.type.value, "name": s.name,
                   "tokens": s.total_tokens, "cost": s.cost_usd,
                   "duration_ms": s.duration_ms, "status": s.status,
                   "model": s.model} for s in spans],
        "insights": [i.to_dict() for i in analyze(spans)],
    }


def make_handler(sink):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                if self.path == "/" or self.path.startswith("/?"):
                    self._send(200, INDEX_HTML, "text/html; charset=utf-8")
                elif self.path == "/api/runs":
                    self._send(200, json.dumps(sink.list_runs(50)))
                elif self.path.startswith("/api/run/"):
                    tid = self.path.rsplit("/", 1)[-1]
                    spans = sink.get_trace(tid)
                    if not spans:
                        for r in sink.list_runs(200):
                            if r["trace_id"].startswith(tid):
                                spans = sink.get_trace(r["trace_id"]); break
                    self._send(200, json.dumps(_run_payload(spans)))
                else:
                    self._send(404, json.dumps({"error": "not found"}))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
    return Handler


def serve(db_path: str = DEFAULT_PATH, port: int = 4319, open_browser: bool = True):
    sink = SQLiteSink(db_path)
    handler = make_handler(sink)
    for p in range(port, port + 20):
        try:
            httpd = socketserver.ThreadingTCPServer(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("no free port found")
    url = f"http://127.0.0.1:{p}"
    print(f"agenticmeter ui  →  {url}   (db: {db_path})\nCtrl-C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.shutdown()


INDEX_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>AgenticMeter</title>
<style>
:root{--ink:#0b1018;--surface:#121d2c;--line:#21324a;--blue:#4dabf7;--green:#3ddc97;
--yellow:#ffd43b;--red:#ff6b6b;--purple:#a78bfa;--text:#e7eef6;--muted:#8b9bb0;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:system-ui,-apple-system,sans-serif;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--ink);color:var(--text);font-family:var(--sans);height:100vh;display:flex;overflow:hidden}
.brand{font-weight:700;font-size:15px;padding:14px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:8px}
.brand .dot{width:9px;height:9px;border-radius:50%;background:var(--blue);box-shadow:0 0 0 3px rgba(77,171,247,.18)}
.brand b{color:var(--blue)}
/* left */
#left{width:320px;border-right:1px solid var(--line);display:flex;flex-direction:column;height:100vh}
#runs{overflow-y:auto;flex:1}
.run{padding:11px 16px;border-bottom:1px solid #182536;cursor:pointer}
.run:hover{background:#0f1928}
.run.sel{background:#16273b;border-left:3px solid var(--blue);padding-left:13px}
.run .top{display:flex;justify-content:space-between;align-items:center}
.run .nm{font-weight:600;font-size:13.5px}
.run .when{color:var(--muted);font-size:11px;font-family:var(--mono)}
.run .sub{color:var(--muted);font-size:11.5px;font-family:var(--mono);margin-top:3px;display:flex;gap:10px}
.run .err{color:var(--red)}
.poll{font-size:10.5px;color:var(--muted);padding:7px 16px;border-top:1px solid var(--line);font-family:var(--mono)}
/* right */
#right{flex:1;overflow-y:auto;height:100vh;padding:0 0 60px}
.head{padding:18px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--ink);z-index:5}
.head h1{font-size:19px;font-weight:700}
.head .meta{color:var(--muted);font-family:var(--mono);font-size:12.5px;margin-top:5px}
.section-label{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:22px 24px 10px}
/* warnings */
.warn{margin:0 24px 10px;background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--yellow);border-radius:10px;padding:13px 15px;cursor:pointer;transition:border-color .15s,transform .1s}
.warn:hover{border-color:var(--blue);transform:translateX(2px)}
.warn .t{font-weight:600;font-size:14px}
.warn .d{color:var(--muted);font-size:12.5px;margin-top:4px;line-height:1.5}
.warn .a{color:var(--blue);font-size:12.5px;margin-top:5px}
.warn .tier{float:right;font-family:var(--mono);font-size:10px;color:var(--muted);border:1px solid var(--line);padding:1px 6px;border-radius:5px}
.warn.patt{border-left-color:var(--purple)}
.ok{margin:0 24px;color:var(--green);font-family:var(--mono);font-size:13px}
/* tree */
.tree{margin:6px 24px 0;font-family:var(--mono);font-size:12.5px}
.node{display:flex;align-items:center;gap:10px;padding:6px 8px;border-radius:7px;border:1px solid transparent}
.node:hover{background:#0f1928}
.node.hl{background:rgba(255,212,59,.10);border-color:var(--yellow);animation:flash .9s ease-out}
@keyframes flash{0%{background:rgba(255,212,59,.34)}100%{background:rgba(255,212,59,.10)}}
.badge{font-size:9.5px;letter-spacing:.04em;text-transform:uppercase;padding:2px 6px;border-radius:5px;flex:none;width:42px;text-align:center}
.b-llm{background:rgba(77,171,247,.16);color:var(--blue)}
.b-tool{background:rgba(61,220,151,.16);color:var(--green)}
.b-retrieval{background:rgba(167,139,250,.16);color:var(--purple)}
.b-agent{background:rgba(139,155,176,.16);color:var(--muted)}
.b-custom{background:rgba(139,155,176,.12);color:var(--muted)}
.node .nm{flex:1}
.node .val{color:var(--muted)}
.node .x{color:var(--red)}
.empty{color:var(--muted);padding:40px 24px;font-family:var(--mono);font-size:13px}
</style></head>
<body>
<div id="left">
  <div class="brand"><span class="dot"></span>Agentic<b>Meter</b></div>
  <div id="runs"></div>
  <div class="poll" id="poll">live · polling…</div>
</div>
<div id="right"><div class="empty">Select a run on the left.</div></div>
<script>
let SEL=null, RUNS=[];
const $=(s,r=document)=>r.querySelector(s);
const fmtTok=n=>n>=1000?(n/1000).toFixed(1)+'k':String(n||0);
const fmtCost=c=>c==null?'$0':(c>=0.0001?'$'+c.toFixed(4):'$'+c.toFixed(6));
const fmtWhen=ts=>{const d=new Date(ts*1000);return d.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});};

async function loadRuns(){
  try{
    const r=await fetch('/api/runs'); RUNS=await r.json();
    renderRuns();
    $('#poll').textContent='live · '+RUNS.length+' runs · '+new Date().toLocaleTimeString();
    if(SEL===null && RUNS.length){selectRun(RUNS[0].trace_id);}
  }catch(e){$('#poll').textContent='offline';}
}
function renderRuns(){
  $('#runs').innerHTML=RUNS.map(r=>`
    <div class="run ${r.trace_id===SEL?'sel':''}" onclick="selectRun('${r.trace_id}')">
      <div class="top"><span class="nm">${r.name}</span><span class="when">${fmtWhen(r.start)}</span></div>
      <div class="sub"><span>${r.steps} steps</span><span>${fmtTok(r.tokens)} tok</span>
        <span>${fmtCost(r.cost)}</span>${r.status!=='ok'?'<span class="err">✗</span>':''}</div>
    </div>`).join('');
}
async function selectRun(id){
  SEL=id; renderRuns();
  const r=await fetch('/api/run/'+id); const data=await r.json();
  renderDetail(data);
}
function renderDetail(d){
  const h=d.header;
  let html=`<div class="head"><h1>${h.name} ${h.status!=='ok'?'<span style="color:var(--red)">✗</span>':''}</h1>
    <div class="meta">${h.steps} steps · ${fmtTok(h.tokens)} tok · ${fmtCost(h.cost)} · ${h.duration.toFixed(1)}s</div></div>`;
  // warnings
  if(d.insights.length){
    html+=`<div class="section-label">Behavior — ${d.insights.length} finding${d.insights.length>1?'s':''}</div>`;
    d.insights.forEach((i,idx)=>{
      html+=`<div class="warn ${i.tier==='pattern'?'patt':''}" onclick='highlight(${JSON.stringify(i.evidence)})'>
        <span class="tier">${i.tier}</span>
        <div class="t">⚠ ${i.title}</div><div class="d">${i.detail}</div><div class="a">→ ${i.action}</div></div>`;
    });
  } else {
    html+=`<div class="section-label">Behavior</div><div class="ok">✓ No behavior issues detected.</div>`;
  }
  // tree
  html+=`<div class="section-label">Trace</div><div class="tree" id="tree">`;
  const kids={}; let root=null;
  d.spans.forEach(s=>{(kids[s.parent_id]=kids[s.parent_id]||[]).push(s); if(!s.parent_id)root=s;});
  function walk(node,depth){
    (kids[node.span_id]||[]).sort((a,b)=>0).forEach(c=>{
      const b='b-'+c.type;
      let val='';
      if(c.type==='llm'&&c.tokens){val=fmtTok(c.tokens)+' tok'+(c.cost?' · '+fmtCost(c.cost):'');}
      else if(c.duration_ms){val=c.duration_ms.toFixed(0)+'ms';}
      html+=`<div class="node" data-id="${c.span_id}" style="margin-left:${depth*18}px">
        <span class="badge ${b}">${c.type.slice(0,4)}</span><span class="nm">${c.name}</span>
        <span class="val">${val}</span>${c.status!=='ok'?'<span class="x">✗</span>':''}</div>`;
      walk(c,depth+1);
    });
  }
  if(root)walk(root,0);
  html+=`</div>`;
  $('#right').innerHTML=html;
}
function highlight(ids){
  document.querySelectorAll('.node.hl').forEach(n=>n.classList.remove('hl'));
  let first=null;
  ids.forEach(id=>{const n=document.querySelector('.node[data-id="'+id+'"]');
    if(n){n.classList.remove('hl');void n.offsetWidth;n.classList.add('hl');if(!first)first=n;}});
  if(first)first.scrollIntoView({behavior:'smooth',block:'center'});
}
loadRuns();
setInterval(loadRuns,4000);
</script></body></html>"""
