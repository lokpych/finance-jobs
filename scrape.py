"""scrape.py - pull job postings for every employer in employers.csv.

Writes:
  data/current.json  today's open postings
  data/seen.json     first-seen date for every posting URL (for the NEW badge)
  data/status.json   one row per employer: ATS, jobs found, ok/error
"""
import csv, json, re, time, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
TIMEOUT = CFG.get("timeout_seconds", 25)
MAX = CFG.get("max_jobs_per_source", 2000)
HEADERS = {"User-Agent": "Mozilla/5.0 (personal job-board bot)", "Accept": "application/json"}


def http(method, url, **kw):
    """GET/POST with retries + backoff (Workday rate-limits bursts)."""
    for attempt in range(4):
        try:
            r = requests.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}")


# ---------- one fetcher per careers backend; each returns a list of dicts ----------
def greenhouse(token, search):
    d = http("GET", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return [{"title": j["title"], "location": (j.get("location") or {}).get("name", ""),
             "url": j["absolute_url"], "posted": (j.get("first_published") or j.get("updated_at") or "")[:10]}
            for j in d.get("jobs", [])]


def lever(token, search):
    d = http("GET", f"https://api.lever.co/v0/postings/{token}?mode=json")
    return [{"title": j["text"], "location": (j.get("categories") or {}).get("location", ""),
             "url": j["hostedUrl"],
             "posted": time.strftime("%Y-%m-%d", time.gmtime(j["createdAt"] / 1000)) if j.get("createdAt") else ""}
            for j in d]


def ashby(token, search):
    d = http("GET", f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    return [{"title": j["title"], "location": j.get("location", ""), "url": j["jobUrl"],
             "posted": (j.get("publishedAt") or "")[:10]} for j in d.get("jobs", [])]


def smartrecruiters(token, search):
    out, offset = [], 0
    while offset < MAX:
        d = http("GET", f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}")
        for j in d.get("content", []):
            loc = j.get("location") or {}
            out.append({"title": j["name"],
                        "location": ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x),
                        "url": f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                        "posted": (j.get("releasedDate") or "")[:10]})
        offset += 100
        if offset >= d.get("totalFound", 0):
            break
    return out


def workday_date(text):
    """Workday says 'Posted Today' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> a fixed date."""
    t = (text or "").lower()
    today = dt.date.today()
    if "30+" in t:
        return "30+ days ago"
    if "today" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s+days", t)
    return (today - dt.timedelta(days=int(m.group(1)))).isoformat() if m else ""


def workday(token, search):
    tenant, wd, site = token.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, offset, total = [], 0, None
    while offset < MAX:
        d = http("POST", api, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search or ""})
        if total is None:
            total = d.get("total", 0)  # Workday only reports the total on page 1
        posts = d.get("jobPostings", [])
        for j in posts:
            if "externalPath" in j:
                out.append({"title": j.get("title", ""), "location": j.get("locationsText", ""),
                            "url": f"{base}/en-US/{site}{j['externalPath']}", "posted": workday_date(j.get("postedOn"))})
        offset += 20
        if not posts or offset >= total:
            break
        time.sleep(0.3)  # be polite
    return out


def oracle(token, search):
    base, site = token.split("|")
    out, offset = [], 0
    while offset < MAX:
        kw = f'keyword="{search}",' if search else ""
        url = (f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},{kw}"
               f"limit=200,offset={offset},sortBy=POSTING_DATES_DESC")
        d = http("GET", url)["items"][0]
        reqs = d.get("requisitionList", [])
        for j in reqs:
            out.append({"title": j["Title"], "location": j.get("PrimaryLocation", ""),
                        "url": f"{base}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}",
                        "posted": j.get("PostedDate", "")})
        offset += 200
        if not reqs or offset >= d.get("TotalJobsCount", 0):
            break
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters, "workday": workday, "oracle": oracle}


# ---------- optional filters from config.json ----------
def _rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else None

TITLE_RX = _rx(CFG.get("title_keywords", []))
EXCL_RX = _rx(CFG.get("exclude_title_keywords", []))
LOC_RX = _rx(CFG.get("location_keywords", []))
UNKNOWN_LOC = re.compile(r"^(\d+ Locations)?$")


def keep(job):
    if TITLE_RX and not TITLE_RX.search(job["title"]):
        return False
    if EXCL_RX and EXCL_RX.search(job["title"]):
        return False
    if LOC_RX and not LOC_RX.search(job["location"] or ""):
        return CFG.get("keep_unknown_locations", True) and bool(UNKNOWN_LOC.match((job["location"] or "").strip()))
    return True


def fetch_row(row):
    fn = FETCHERS.get(row["ats"])
    if fn is None:
        return row, [], "link only"
    try:
        jobs = [j for j in fn(row["token"], row.get("search", "")) if keep(j)]
        return row, jobs, "ok"
    except Exception as e:
        return row, [], f"error: {type(e).__name__}: {str(e)[:120]}"


def main():
    rows = list(csv.DictReader(open(ROOT / "employers.csv", newline="", encoding="utf-8")))
    now = dt.datetime.now(dt.timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # seen.json remembers WHEN we first saw each posting (by URL) -> powers the NEW badge
    seen_path = ROOT / "data" / "seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {"baseline": today, "first_seen": {}, "gone": {}}
    prev_cur_path = ROOT / "data" / "current.json"
    prev = json.loads(prev_cur_path.read_text())["jobs"] if prev_cur_path.exists() else []

    t0 = time.time()
    with ThreadPoolExecutor(CFG.get("threads", 8)) as ex:
        results = list(ex.map(fetch_row, rows))

    status, failed, current = {}, set(), {}
    for row, jobs, st in results:
        s = status.setdefault(row["company"], {"company": row["company"], "category": row["category"],
                                               "ats": row["ats"], "careers_url": row["careers_url"],
                                               "jobs": 0, "status": st})
        if st.startswith("error"):
            failed.add(row["company"])
            s["status"] = st
        for j in jobs:
            if j["url"] not in current:
                current[j["url"]] = {"company": row["company"], "category": row["category"], **j}
                s["jobs"] += 1
    # if a firm errored today, keep its jobs from the last good run instead of blanking it
    for j in prev:
        if j["company"] in failed and j["url"] not in current:
            current[j["url"]] = j

    fs, gone = seen["first_seen"], seen["gone"]
    for url, j in current.items():
        fs.setdefault(url, today)       # brand-new URL -> first seen today
        gone.pop(url, None)
        j["first_seen"] = fs[url]
    for url in fs:
        if url not in current:
            gone.setdefault(url, today)  # posting disappeared today
    cutoff = (now - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    for url, day in list(gone.items()):  # forget postings gone 30+ days
        if day < cutoff:
            gone.pop(url)
            fs.pop(url, None)

    jobs_sorted = sorted(current.values(), key=lambda j: (j["company"], j["title"], j["url"]))
    (ROOT / "data").mkdir(exist_ok=True)
    seen_path.write_text(json.dumps(seen, indent=0, sort_keys=True))
    # one job per line keeps the daily git diffs small
    lines = ",\n".join(json.dumps(j, ensure_ascii=False, sort_keys=True) for j in jobs_sorted)
    prev_cur_path.write_text(f'{{"updated": "{now.isoformat(timespec="seconds")}", "baseline": "{seen["baseline"]}", "jobs": [\n{lines}\n]}}\n')
    (ROOT / "data" / "status.json").write_text(json.dumps(list(status.values()), indent=1, ensure_ascii=False))

    ok = sum(1 for s in status.values() if s["status"] == "ok")
    link = sum(1 for s in status.values() if s["status"] == "link only")
    print(f"{len(status)} employers | {ok} fetched OK | {len(failed)} errors | {link} link-only")
    print(f"{len(jobs_sorted)} jobs | took {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
