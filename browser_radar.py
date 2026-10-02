# -*- coding: utf-8 -*-
"""
Браузерный радар: карьерные страницы без публичного API. Каждую открывает настоящим Chromium
(Playwright) и достаёт вакансии из (1) JSON-ответов, которые подгружает сама страница,
(2) разметки JobPosting (JSON-LD), (3) при их отсутствии — из ссылок на странице.
Chromium работает в отдельном процессе: завис сайт дольше PER_SITE секунд — процесс убиваем
и идём к следующей компании. Список — lists/browser_companies.csv (Name, Link).
"""
import contextlib, csv, json, os, re, sys, time
from urllib.parse import urljoin, urlparse

from core import is_marketing, near_misses, load, save, report, Health

IN_FILE = os.environ.get("BROWSER_COMPANIES", "lists/browser_companies.csv")
STATE   = os.environ.get("BROWSER_STATE", "state/seen_browser.json")
LABEL   = os.environ.get("BROWSER_LABEL", "🌐 Браузер")
HEALTH  = os.environ.get("BROWSER_HEALTH", "state/health_browser.json")
REJECTED = os.environ.get("BROWSER_REJECTED", "state/rejected_browser.json")
# компании, которые API-радар сегодня не смог опросить (пишет radar.py) — проверяем их тоже
EXTRA   = os.environ.get("BROWSER_EXTRA", "state/api_fallback.csv")
FROM_API = "↪ "         # пометка в отчёте здоровья: компания пришла из API-радара
WAIT_MS = 4000          # сколько ждать дозагрузку вакансий после открытия
NAV_TIMEOUT = 30000
PER_SITE = 75           # сек на одну компанию; дольше — процесс убиваем, компанию пропускаем
UA_STR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124 Safari/537.36")

TITLE_KEYS = ["title","name","jobtitle","position","text","role","vacancyname","jobopeningname","headline"]
URL_KEYS   = ["url","absolute_url","hostedurl","joburl","applyurl","apply_url","link",
              "careers_url","permalink","href","canonicalurl","landing_page_url"]
LOC_KEYS   = ["location","city","worklocation","office","joblocation","location_name","region"]

# URL-мусор: блоги/статьи/новости, которые притворяются вакансиями
URL_BAN = ["/insights/","/insight/","/blog/","/blogs/","/life-at-","/culture/","/news/",
           "/article","/stories/","/story/",".ghost.io","/press/","/resources/","/guide",
           "/webinar","/podcast","/events/","/event/","/case-stud","/customers/","/about"]

def _first(d, keys):
    low = {k.lower(): v for k, v in d.items() if isinstance(k, str)}
    for k in keys:
        if k in low and low[k]:
            return low[k]
    return None

def _as_text(v):
    if isinstance(v, str): return v
    if isinstance(v, dict):
        for k in ["name","label","city","addresslocality","text"]:
            for kk, vv in v.items():
                if kk.lower()==k and isinstance(vv,str): return vv
    if isinstance(v, list) and v:
        return _as_text(v[0])
    return ""

def _job_from_dict(d, base_url):
    title = _first(d, TITLE_KEYS)
    if not isinstance(title, str) or not title.strip():
        return None
    url = _first(d, URL_KEYS)
    url = _as_text(url) if url else ""
    if url and url.startswith("/"):
        url = urljoin(base_url, url)
    if not url:
        url = base_url
    loc = _as_text(_first(d, LOC_KEYS) or "")
    return {"title": title.strip(), "url": url, "location": loc}

def _quality(jobs, base_url):
    """Сколько элементов похожи на вакансии: своя ссылка (не сама страница) или локация.
       Массив без ссылок и локаций — это меню, список cookie-вендоров, фильтры, а не вакансии."""
    base=_norm_url(base_url); urls=set(); q=0
    for j in jobs:
        u=_norm_url(j.get("url"))
        if (u and u!=base and u not in urls) or j.get("location"): q+=1
        urls.add(u)
    return q

def extract_from_json(obj, base_url):
    """Ищем в JSON массив словарей, больше всего похожий на список вакансий."""
    best = []; best_q = 0
    def walk(x):
        nonlocal best, best_q
        if isinstance(x, list):
            if x and all(isinstance(e, dict) for e in x):
                cand = [_job_from_dict(e, base_url) for e in x]
                cand = [c for c in cand if c]
                q = _quality(cand, base_url)
                if (q, len(cand)) > (best_q, len(best)):
                    best, best_q = cand, q
            for e in x: walk(e)
        elif isinstance(x, dict):
            for v in x.values(): walk(v)
    walk(obj)
    return best

def extract_jsonld(html, base_url):
    out=[]
    for m in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            data = json.loads(m.strip())
        except Exception:
            continue
        stack=[data]
        while stack:
            node=stack.pop()
            if isinstance(node, list): stack.extend(node); continue
            if not isinstance(node, dict): continue
            t=node.get("@type","")
            t=t if isinstance(t,str) else ",".join(t) if isinstance(t,list) else ""
            if "JobPosting" in t:
                title=node.get("title")
                url=node.get("url") or node.get("@id") or base_url
                loc=""
                jl=node.get("jobLocation")
                if isinstance(jl, dict):
                    addr=jl.get("address",{})
                    if isinstance(addr,dict): loc=addr.get("addressLocality") or addr.get("addressRegion") or ""
                elif isinstance(jl, list) and jl:
                    a=(jl[0] or {}).get("address",{}) if isinstance(jl[0],dict) else {}
                    if isinstance(a,dict): loc=a.get("addressLocality","")
                if isinstance(title,str) and title.strip():
                    out.append({"title":title.strip(),"url":url,"location":loc or ""})
            for v in node.values():
                if isinstance(v,(list,dict)): stack.append(v)
    return out

# тексты-навигация, которые НЕ являются вакансиями
NAV_TEXTS = {"apply","apply now","learn more","read more","see all","see more","view all",
 "view job","view jobs","view details","view opening","view openings","all positions",
 "all jobs","open positions","open roles","join us","join our team","join the team","back",
 "share","load more","show more","next","previous","prev","home","about","about us","contact",
 "contact us","careers","career","jobs","explore","explore jobs","get in touch","sign in",
 "log in","login","subscribe","see openings","see opening","view opportunities","browse jobs",
 "search jobs","find jobs","more","details","read story","read","watch","play"}

ATS_HOSTS = ["applytojob.com","skailer.com","casthr.co","careers-page.com","greenhouse.io",
 "lever.co","ashbyhq.com","recruitee.com","workable.com","breezy.hr","pinpointhq.com",
 "teamtailor.com","bamboohr.com","smartrecruiters.com","rippling","softr.app","sage.hr",
 "hibob.com","peopleforce","freshteam","zoho","join.com","getro","huntflow"]
CAREER_SEG = ["/job","/vacan","/career","/position","/opening","/apply","/o/","/role"]

def _site(host):
    host=(host or "").lower().replace("www.","")
    parts=[p for p in host.split(".") if p]
    return parts[-2] if len(parts)>=2 else (parts[0] if parts else "")

def _in_zone(base_host, url):
    from urllib.parse import urlparse
    p=urlparse(url.lower()); host=p.netloc; path=p.path
    if any(h in host for h in ATS_HOSTS): return True          # ссылка на ATS-хост = точно вакансия
    if _site(host)!=_site(base_host): return False             # чужой домен (medium/entrepreneur) — нет
    return any(seg in path for seg in CAREER_SEG)              # свой домен + карьерный путь (только path!)

def _clean(t):
    return " ".join((t or "").split()).strip()

def _text_is_jobish(text):
    t = _clean(text)
    low = t.lower()
    if not t: return False
    if low in NAV_TEXTS: return False
    for p in ("apply","learn more","read more","see all","view all","load more","show more"):
        if low.startswith(p): return False
    if len(t) < 8 or len(t) > 140: return False      # слишком коротко/длинно — не тайтл
    if len(t.split()) > 16: return False              # это уже абзац, не заголовок
    if len(t.split()) < 2: return False               # «Marketing» — фильтр отдела, не вакансия
    return True

def _dom_candidates(anchors, base):
    """anchors: список dict {href, text, head}. Возвращает кандидатов-вакансий по ТЕКСТУ."""
    from urllib.parse import urljoin, urlparse
    base_host=urlparse(base).netloc
    out=[]; seen=set()
    for a in anchors:
        href=(a.get("href") or "").strip()
        if not href or href.lower().startswith(("javascript:","mailto:","tel:")): continue
        title = _clean(a.get("head")) or _clean(a.get("text"))   # приоритет вложенному заголовку
        if not _text_is_jobish(title): continue
        url = href if href.startswith("http") else urljoin(base, href)
        if not _in_zone(base_host, url): continue     # только карьерная зона / ATS-хосты
        key=(title.lower(), url)
        if key in seen: continue
        seen.add(key)
        out.append({"title":title, "url":url, "location":""})
    return out

def _looks_like_job(j):
    url=(j.get("url") or "").lower()
    if any(b in url for b in URL_BAN):        # блог/статья/новость по URL — не вакансия
        return False
    return True

def extract_from_dom(page, base):
    try:
        anchors = page.eval_on_selector_all("a[href]", '''els => els.map(e => {
            const h = e.querySelector("h1,h2,h3,h4");
            return {href: e.getAttribute("href") || "",
                    text: (e.innerText || "").trim(),
                    head: h ? (h.innerText || "").trim() : ""};
        })''')
    except Exception:
        anchors=[]
    cands=_dom_candidates(anchors, base)
    if cands: return cands
    # аварийный запас: голые заголовки без ссылок
    try:
        heads = page.eval_on_selector_all("h1,h2,h3", "els => els.map(e => (e.innerText||\"\").trim())")
    except Exception:
        heads=[]
    return [{"title":_clean(h),"url":base,"location":""} for h in heads if _text_is_jobish(h)]

def scrape(page, link, dbg=None):
    """Открываем страницу, собираем JSON-ответы + JSON-LD, возвращаем список вакансий.
       dbg (dict) — режим диагностики: туда складываются подробности, почему вышло столько."""
    captured=[]; resps=[]
    def on_response(resp):
        try:
            h=resp.headers or {}
            ct=h.get("content-type","").lower()
            if "json" in ct and "stream" not in ct:
                resps.append(resp)          # в обработчике тело не читаем — это может повиснуть
        except Exception:
            pass
    page.on("response", on_response)
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(WAIT_MS)
    try: html=page.content()
    except Exception: html=""
    page.remove_listener("response", on_response)
    if dbg is not None:
        try: dbg.update(final_url=page.url, page_title=page.title())
        except Exception: pass
        dbg.update(html_len=len(html), json_responses=[r.url[:150] for r in resps][:25])
        try:
            dbg["iframes"]=[f.url[:150] for f in page.frames if f.url and f.url!="about:blank"][1:10]
            dbg["text_head"]=" ".join(page.inner_text("body").split())[:600]
        except Exception: pass
    # сначала ответы, похожие на API вакансий; аналитика и прочее — потом
    jobish=re.compile(r"job|career|vacanc|position|opening|posting|role|recruit|ats", re.I)
    resps.sort(key=lambda r: 0 if jobish.search(r.url or "") else 1)
    for resp in resps[:60]:
        try: captured.append(resp.json())
        except Exception: pass

    best=[]; best_q=0
    for data in captured:
        got=extract_from_json(data, link); q=_quality(got, link)
        if (q, len(got))>(best_q, len(best)): best, best_q = got, q
    ld=extract_jsonld(html, link)
    if len(ld)>len(best): best, best_q = ld, len(ld)
    # структурированным данным доверяем, только если они правда похожи на вакансии
    # (раньше любой крупный JSON-массив — меню, cookie-вендоры — глушил DOM, и вакансии терялись)
    if dbg is not None:
        dom_dbg=[j for j in extract_from_dom(page, link) if _looks_like_job(j)]
        dbg.update(structured=len(best), structured_quality=best_q, jsonld=len(ld),
                   structured_sample=[j["title"][:80] for j in best[:6]],
                   dom=len(dom_dbg), dom_sample=[f'{j["title"][:70]} -> {j["url"][:90]}' for j in dom_dbg[:8]])
    if best and best_q >= max(1, len(best)//2):
        return [j for j in best if _looks_like_job(j)]
    dom=[j for j in extract_from_dom(page, link) if _looks_like_job(j)]
    return dom or [j for j in best if _looks_like_job(j)]

def _norm_url(u):
    p=urlparse((u or "").strip().lower())
    return (p.netloc.replace("www.","")+p.path).rstrip("/")

# ---------- изоляция: браузер в дочернем процессе, зависший сайт = убить и продолжить ----------

def load_companies():
    """Основной список + то, что сегодня не смог опросить API-радар (без дублей)."""
    rows=[r for r in csv.DictReader(open(IN_FILE, encoding="utf-8-sig"))]
    have={_norm_url(r.get("Link")) for r in rows} | {(r.get("Name") or "").strip().lower() for r in rows}
    if EXTRA and os.path.exists(EXTRA):
        for r in csv.DictReader(open(EXTRA, encoding="utf-8-sig")):
            name=(r.get("Name") or "").strip(); link=(r.get("Link") or "").strip()
            if not link or _norm_url(link) in have or name.lower() in have: continue
            have.add(_norm_url(link)); rows.append({"Name": FROM_API+name, "Link": link})
    return rows

def worker(start):
    """Режим --worker N: обходит компании с N-й, по строке JSON на компанию в stdout."""
    companies=load_companies()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(args=["--no-sandbox"])
        ctx=browser.new_context(user_agent=UA_STR)
        for i in range(start, len(companies)):
            link=(companies[i].get("Link") or "").strip()
            print(json.dumps({"i":i,"start":1}), flush=True)
            found=[]; err=""
            if link:
                page=ctx.new_page(); page.set_default_timeout(15000)
                try: found=scrape(page, link)
                except Exception as e: err=f"{type(e).__name__} {str(e)[:50]}"
                try: page.close()
                except Exception: pass
            print(json.dumps({"i":i,"found":found,"err":err}, ensure_ascii=False), flush=True)
        browser.close()

def run_isolated(companies, health):
    import subprocess, selectors
    jobs=[]; errors=[]; empty=0; i=0; n=len(companies)
    while i < n:
        proc=subprocess.Popen([sys.executable, __file__, "--worker", str(i)],
                              stdout=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
        sel=selectors.DefaultSelector(); sel.register(proc.stdout, selectors.EVENT_READ)
        cur=i; t0=time.time(); stuck=False
        while True:
            if not sel.select(timeout=max(1, PER_SITE-(time.time()-t0))):
                if time.time()-t0 >= PER_SITE: stuck=True; break
                continue
            line=proc.stdout.readline()
            if not line: break                              # воркер закончил или упал
            try: msg=json.loads(line)
            except Exception: continue
            if msg.get("start"): cur=msg["i"]; t0=time.time(); continue
            r=companies[msg["i"]]; name=(r.get("Name") or "").strip(); link=(r.get("Link") or "").strip()
            found=msg.get("found") or []
            if msg.get("err"): errors.append(f"{name}: {msg['err']}")
            base=_norm_url(link); kept=0
            for j in found:
                if not j.get("title") or not j.get("url"): continue
                if _norm_url(j["url"])==base: continue      # ссылка на саму карьерную страницу = заголовок, не вакансия
                jobs.append({**j,"company":name.removeprefix(FROM_API),"jid":j["url"]}); kept+=1
            if not kept: empty+=1
            health.mark(name, jobs=kept, err=msg.get("err") or "")
            print(f"  [{msg['i']+1}/{n}] {name}: {len(found)} за {time.time()-t0:.0f}с", flush=True)
            cur=msg["i"]+1
        if stuck:
            name=(companies[cur].get("Name") or "").strip()
            print(f"  !! [{cur+1}/{n}] {name}: завис > {PER_SITE}с — убиваю браузер, иду дальше", flush=True)
            errors.append(f"{name}: завис > {PER_SITE}с"); empty+=1
            health.mark(name, err=f"завис > {PER_SITE}с"); cur+=1
        try: os.killpg(proc.pid, 9)                        # убиваем воркер вместе с его Chromium
        except Exception: proc.kill()
        proc.wait()
        i = cur if cur > i else i+1                         # страховка от вечного цикла
    return jobs, errors, empty


@contextlib.contextmanager
def _description_page():
    """Страница Playwright, чтобы оценщик мог дочитать описания JS-вакансий."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(args=["--no-sandbox"])
        page=b.new_context(user_agent=UA_STR).new_page(); page.set_default_timeout(15000)
        try: yield page
        finally: b.close()

def main():
    companies=load_companies()
    seen=load(STATE, {})
    health=Health(HEALTH, LABEL, empty_is_bad=True)
    extra=sum(1 for r in companies if r["Name"].startswith(FROM_API))
    print(f"=== браузерный радар | {len(companies)} компаний (из них {extra} от API-радара) | {'--all' if '--all' in sys.argv else 'только новые'} ===")
    jobs, errors, empty = run_isolated(companies, health)
    jobs=list({j["jid"]: j for j in jobs}.values())         # дедуп по url
    mk=[j for j in jobs if is_marketing(j["title"], j.get("location",""))]
    save(REJECTED, near_misses(jobs))
    print(f"пусто: {empty} | ошибок: {len(errors)} | вакансий: {len(jobs)}")
    for e in errors[:20]: print("  ⚠", e)
    report(mk, seen, STATE, label=LABEL, page_factory=_description_page)
    health.finish({"jobs_total": len(jobs), "marketing": len(mk)})

def diag(names):
    """--diag [имена через запятую]: подробно разбирает страницы (по умолчанию — все, что в отчёте
       здоровья числятся сломанными) и пишет state/diag_browser.json. Вакансии не шлёт, seen не трогает."""
    companies=load_companies()
    if names: want={n.strip().lower() for n in names.split(",") if n.strip()}
    else:
        h=load(HEALTH, {}).get("sources", {})
        want={k.removeprefix(FROM_API).lower() for k,v in h.items() if v.get("bad_streak")}
    todo=[r for r in companies if r["Name"].removeprefix(FROM_API).lower() in want]
    print(f"=== диагностика: {len(todo)} компаний ===")
    from playwright.sync_api import sync_playwright
    out={}
    with sync_playwright() as p:
        browser=p.chromium.launch(args=["--no-sandbox"])
        ctx=browser.new_context(user_agent=UA_STR)
        for r in todo:
            name=r["Name"]; link=(r.get("Link") or "").strip(); d={"link":link}; t0=time.time()
            page=ctx.new_page(); page.set_default_timeout(15000)
            import signal
            def _alarm(*_): raise TimeoutError(f"завис > {PER_SITE}с")
            signal.signal(signal.SIGALRM, _alarm); signal.alarm(PER_SITE)
            try:
                found=scrape(page, link, d); d["found"]=len(found)
                d["found_sample"]=[f'{j["title"][:70]} -> {j["url"][:90]}' for j in found[:6]]
            except Exception as e:
                d["error"]=f"{type(e).__name__} {str(e)[:120]}"
            signal.alarm(0); d["sec"]=round(time.time()-t0)
            try: page.close()
            except Exception: pass
            out[name]=d; print(f"  {name}: {d.get('found')} за {d['sec']}с", flush=True)
        browser.close()
    save(os.environ.get("BROWSER_DIAG", "state/diag_browser.json"), out)

if __name__=="__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--worker": worker(int(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--diag": diag(sys.argv[2] if len(sys.argv) > 2 else "")
    else: main()
