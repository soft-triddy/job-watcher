# -*- coding: utf-8 -*-
"""
Браузерный проход по JS-страницам, которые API-радар не берёт.
Рендерит каждую карьерную страницу настоящим браузером (Playwright), достаёт вакансии из:
  (1) JSON-ответов, которые страница сама подгружает (часто там и лежит список),
  (2) структурированных данных JobPosting (JSON-LD) в коде страницы.
Фильтр маркетинга и отправку в Telegram берёт из radar.py — правила в одном месте.
Бесплатно: только Playwright + разбор, никаких платных LLM.
"""
import csv, json, os, re, sys, time
from urllib.parse import urljoin, urlparse

# переиспользуем фильтр и отправку из основного радара (единый источник правил)
from radar import is_marketing, send, load

IN_FILE = os.environ.get("BROWSER_COMPANIES", "browser_companies.csv")
STATE   = os.environ.get("BROWSER_STATE", "seen_browser.json")
WAIT_MS = 4000          # сколько ждать дозагрузку вакансий после открытия
NAV_TIMEOUT = 30000

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

def extract_from_json(obj, base_url):
    """Ищем в JSON самый крупный массив «похожих на вакансии» словарей."""
    best = []
    def walk(x):
        nonlocal best
        if isinstance(x, list):
            if x and all(isinstance(e, dict) for e in x):
                cand = [_job_from_dict(e, base_url) for e in x]
                cand = [c for c in cand if c]
                if len(cand) > len(best):
                    best = cand
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

def scrape(page, link):
    """Открываем страницу, собираем JSON-ответы + JSON-LD, возвращаем список вакансий."""
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
    for resp in resps[:40]:
        try: captured.append(resp.json())
        except Exception: pass

    best=[]
    for data in captured:
        got=extract_from_json(data, link)
        if len(got)>len(best): best=got
    ld=extract_jsonld(html, link)
    if len(ld)>len(best): best=ld
    if best:                                       # структурированные данные ЕСТЬ — доверяем им
        return [j for j in best if _looks_like_job(j)]
    # структурированного нет вообще — только тогда DOM-фолбэк
    return [j for j in extract_from_dom(page, link) if _looks_like_job(j)]

def _norm_url(u):
    p=urlparse((u or "").strip().lower())
    return (p.netloc.replace("www.","")+p.path).rstrip("/")

# ---------- изоляция: браузер в дочернем процессе, зависший сайт = убить и продолжить ----------
PER_SITE = 75          # сек на одну компанию; дольше — процесс убиваем, компанию пропускаем
UA_STR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124 Safari/537.36")

def worker(start):
    """Режим --worker N: обходит компании с N-й, по строке JSON на компанию в stdout."""
    companies=[r for r in csv.DictReader(open(IN_FILE, encoding="utf-8-sig"))]
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

def run_isolated(companies):
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
            if not found: empty+=1
            base=_norm_url(link)
            for j in found:
                if not j.get("title") or not j.get("url"): continue
                if _norm_url(j["url"])==base: continue      # ссылка на саму карьерную страницу = заголовок, не вакансия
                jobs.append({**j,"company":name,"jid":j["url"]})
            print(f"  [{msg['i']+1}/{n}] {name}: {len(found)} за {time.time()-t0:.0f}с", flush=True)
            cur=msg["i"]+1
        if stuck:
            name=(companies[cur].get("Name") or "").strip()
            print(f"  !! [{cur+1}/{n}] {name}: завис > {PER_SITE}с — убиваю браузер, иду дальше", flush=True)
            errors.append(f"{name}: завис > {PER_SITE}с"); empty+=1; cur+=1
        try: os.killpg(proc.pid, 9)                        # убиваем воркер вместе с его Chromium
        except Exception: proc.kill()
        proc.wait()
        i = cur if cur > i else i+1                         # страховка от вечного цикла
    return jobs, errors, empty


def main():
    dump_all = "--all" in sys.argv
    print(f"=== browser_radar | режим: {'ВСЕ текущие' if dump_all else 'только новые'} ===")

    companies=[]
    with open(IN_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): companies.append(r)

    seen_existed=os.path.exists(STATE)
    seen=load(STATE, {})

    jobs, errors, empty = run_isolated(companies)

    # дедуп по url
    uniq={}
    for j in jobs: uniq[j["jid"]]=j
    jobs=list(uniq.values())

    mk=[j for j in jobs if is_marketing(j["title"], j.get("location",""))]
    new=[j for j in mk if j["jid"] not in seen]
    for j in mk: seen[j["jid"]]=j["title"]
    json.dump(seen, open(STATE,"w",encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"компаний: {len(companies)} | пусто (ничего не достали): {empty} | ошибок: {len(errors)}")
    print(f"вакансий: {len(jobs)} | маркетинговых: {len(mk)} | новых: {len(new)}")
    for e in errors[:20]: print("  ⚠",e)

    def fmt(items, header):
        from scorer import score_jobs, fmt_job, rank
        # описания JS-страниц добираем тем же браузером
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p2:
            b2=p2.chromium.launch(args=["--no-sandbox"])
            pg=b2.new_context(user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124 Safari/537.36")).new_page()
            pg.set_default_timeout(15000)
            score_jobs(items, page=pg)
            b2.close()
        from scorer import diag_line
        return "\n\n".join([header.rstrip()] + [fmt_job(j) for j in rank(items)]) + diag_line()

    if dump_all:
        if mk: send(fmt(mk, f"🌐 Браузер: все текущие маркетинг-вакансии ({len(mk)}):\n"))
        else:  send("🌐 Браузер: маркетинговых вакансий не найдено.")
        return
    if not seen_existed:
        send(f"🌐 Браузерный радар включён. Слежу за {len(mk)} вакансиями на JS-страницах. Дальше только новые.")
        print("Первый запуск: сводка отправлена."); return
    if new:
        send(fmt(new, f"🌐 Новые вакансии (браузер, {len(new)}):\n")); print(f"Отправлено новых: {len(new)}")
    else:
        print("Новых нет.")

if __name__=="__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--worker": worker(int(sys.argv[2]))
    else: main()
