"""build_site.py - turn data/current.json + data/status.json into docs/index.html + docs/data.js"""
import json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
cur = json.loads((ROOT / "data" / "current.json").read_text())
status = json.loads((ROOT / "data" / "status.json").read_text())

# compact lists keep data.js small: firms by index, jobs as short arrays
cats = sorted({s["category"] for s in status})
firms = sorted(status, key=lambda s: s["company"].lower())
idx = {f["company"]: i for i, f in enumerate(firms)}
firm_rows = [[f["company"], cats.index(f["category"]), f["ats"], f["careers_url"], f["jobs"], f["status"]] for f in firms]
job_rows = [[idx[j["company"]], j["title"], j["location"], j["url"], j["posted"], j["first_seen"]] for j in cur["jobs"]]

data = {"updated": cur["updated"], "baseline": cur["baseline"], "newDays": CFG.get("new_days", 7),
        "cats": cats, "firms": firm_rows}
out = ROOT / "docs"
out.mkdir(exist_ok=True)
body = ",\n".join(json.dumps(r, ensure_ascii=False) for r in job_rows)
(out / "data.js").write_text("const DATA = " + json.dumps(data, ensure_ascii=False)[:-1]
                             + ', "jobs": [\n' + body + "\n]};\n", encoding="utf-8")

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finance Job Board</title>
<style>
 body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1d2330}
 header{background:#0b2545;color:#fff;padding:18px 24px}
 header h1{margin:0;font-size:22px} header p{margin:4px 0 0;opacity:.85;font-size:14px}
 .wrap{padding:16px 24px}
 .tabs button{border:0;background:#dde3ec;padding:8px 14px;margin-right:6px;border-radius:6px 6px 0 0;cursor:pointer;font-size:14px}
 .tabs button.on{background:#fff;font-weight:600}
 .panel{background:#fff;border-radius:0 8px 8px 8px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;align-items:center}
 input,select{padding:7px 9px;border:1px solid #c8cfda;border-radius:6px;font-size:14px}
 input[type=search]{min-width:240px}
 table{border-collapse:collapse;width:100%;font-size:14px}
 th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #eceff4;vertical-align:top}
 th{background:#f0f3f7;position:sticky;top:0;cursor:pointer;white-space:nowrap}
 tr:hover td{background:#fafbfd}
 a{color:#1456b8;text-decoration:none} a:hover{text-decoration:underline}
 .new{background:#16a34a;color:#fff;font-size:11px;font-weight:700;padding:2px 6px;border-radius:10px;margin-left:6px}
 .muted{color:#6b7385;font-size:13px} .d{white-space:nowrap} .err{color:#b42318}
 #more{margin-top:10px;padding:8px 14px;border:0;background:#0b2545;color:#fff;border-radius:6px;cursor:pointer}
</style></head><body>
<header><h1>Finance Job Board</h1><p id="meta"></p></header>
<div class="wrap">
 <div class="tabs"><button id="tJobs" class="on">Jobs</button><button id="tFirms">Employers</button></div>
 <div class="panel" id="pJobs">
  <div class="filters">
   <input type="search" id="q" placeholder="Search title, company, location...">
   <select id="cat"><option value="">All categories</option></select>
   <select id="firm"><option value="">All companies</option></select>
   <input id="loc" placeholder="Location (e.g. New York)">
   <label><input type="checkbox" id="onlyNew"> New only</label>
   <span class="muted" id="count"></span>
  </div>
  <table><thead><tr><th data-k="1">Title</th><th data-k="0">Company</th><th>Category</th><th data-k="2">Location</th><th data-k="4">Posted</th><th data-k="5">First seen</th></tr></thead>
  <tbody id="rows"></tbody></table>
  <button id="more">Show more</button>
 </div>
 <div class="panel" id="pFirms" style="display:none">
  <div class="filters"><input type="search" id="fq" placeholder="Search employers..."><span class="muted">Employers without a job feed link to their careers page.</span></div>
  <table><thead><tr><th>Employer</th><th>Category</th><th>Source</th><th>Open jobs</th><th>Careers page</th></tr></thead><tbody id="frows"></tbody></table>
 </div>
</div>
<script src="data.js"></script>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const F = DATA.firms, C = DATA.cats;
const cutoff = new Date(Date.now() - DATA.newDays*864e5).toISOString().slice(0,10);
const isNew = j => j[5] > DATA.baseline && j[5] >= cutoff;   // first-run jobs are never "new"
const live = F.filter(f => f[2] !== "link").length;
$("meta").textContent = `${DATA.jobs.length.toLocaleString()} open jobs from ${live} employers with job feeds (${F.length} tracked) · Last updated ${new Date(DATA.updated).toLocaleString()}`;
C.forEach((c,i) => $("cat").add(new Option(c, i)));
function fillFirms(){ const c=$("cat").value; $("firm").length=1;
  F.forEach((f,i)=>{ if(f[2]!=="link" && (c===""||f[1]==c)) $("firm").add(new Option(f[0], i)); }); }
fillFirms();
let shown = 100, sortK = 5, sortDir = -1, list = [];
function apply(){
  const q=$("q").value.toLowerCase(), c=$("cat").value, fi=$("firm").value, l=$("loc").value.toLowerCase(), n=$("onlyNew").checked;
  list = DATA.jobs.filter(j => (c===""||F[j[0]][1]==c) && (fi===""||j[0]==fi) && (!n||isNew(j))
     && (!l || j[2].toLowerCase().includes(l))
     && (!q || (j[1]+" "+F[j[0]][0]+" "+j[2]).toLowerCase().includes(q)));
  const key = j => sortK===0 ? F[j[0]][0] : String(j[sortK]);
  list.sort((a,b) => key(a) < key(b) ? -sortDir : key(a) > key(b) ? sortDir : 0);
  shown = 100; render();
}
function render(){
  $("rows").innerHTML = list.slice(0, shown).map(j => `<tr><td><a href="${esc(j[3])}" target="_blank" rel="noopener">${esc(j[1])}</a>${isNew(j)?'<span class="new">NEW</span>':""}</td>
   <td>${esc(F[j[0]][0])}</td><td class="muted">${esc(C[F[j[0]][1]])}</td><td>${esc(j[2])}</td><td class="muted d">${esc(j[4])}</td><td class="muted d">${esc(j[5])}</td></tr>`).join("");
  $("count").textContent = `${list.length.toLocaleString()} matching · ${list.filter(isNew).length} new`;
  $("more").style.display = shown < list.length ? "" : "none";
}
function renderFirms(){
  const q=$("fq").value.toLowerCase();
  $("frows").innerHTML = F.filter(f=>!q||(f[0]+" "+C[f[1]]).toLowerCase().includes(q)).map(f=>`<tr><td>${esc(f[0])}</td><td class="muted">${esc(C[f[1]])}</td>
   <td>${f[2]==="link"?'<span class="muted">link only</span>':esc(f[2])}${f[5].startsWith("error")?' <span class="err">(error)</span>':""}</td>
   <td>${f[2]==="link"?"":f[4]}</td><td><a href="${esc(f[3])}" target="_blank" rel="noopener">open</a></td></tr>`).join("");
}
["q","loc"].forEach(id => $(id).addEventListener("input", apply));
["firm","onlyNew"].forEach(id => $(id).addEventListener("change", apply));
$("cat").addEventListener("change", () => { fillFirms(); apply(); });
$("more").onclick = () => { shown += 200; render(); };
document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
  const k = +th.dataset.k; sortDir = (sortK===k) ? -sortDir : (k>=4 ? -1 : 1); sortK = k; apply(); });
$("fq").addEventListener("input", renderFirms);
$("tJobs").onclick = () => { $("pJobs").style.display=""; $("pFirms").style.display="none"; $("tJobs").className="on"; $("tFirms").className=""; };
$("tFirms").onclick = () => { $("pJobs").style.display="none"; $("pFirms").style.display=""; $("tFirms").className="on"; $("tJobs").className=""; };
apply(); renderFirms();
</script></body></html>
"""
(out / "index.html").write_text(HTML, encoding="utf-8")
(out / ".nojekyll").write_text("")
new = sum(1 for j in cur["jobs"] if j["first_seen"] > cur["baseline"]
          and j["first_seen"] >= (dt.date.today() - dt.timedelta(days=CFG.get("new_days", 7))).isoformat())
print(f"Wrote docs/index.html + docs/data.js: {len(job_rows)} jobs, {len(firm_rows)} employers, {new} new")
