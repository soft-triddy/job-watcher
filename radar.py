# -*- coding: utf-8 -*-
"""
API-радар: компании на ATS с публичным JSON API (Greenhouse, Lever, Ashby, Workable, Recruitee,
BambooHR, Breezy, SmartRecruiters, Rippling, Teamtailor, Pinpoint, Workday).
Список — lists/radar_companies.csv (Name, Link, ATS). Код компании в ATS (slug) подбирается сам
и проверяется живым запросом; найденные кешируются в state/slugs.json.
"""
import csv, os, re, sys, time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from core import UA, TIMEOUT, is_marketing, near_misses, load, save, report, Health

IN_FILE = os.environ.get("RADAR_COMPANIES", "lists/radar_companies.csv")
STATE   = os.environ.get("RADAR_STATE", "state/seen.json")
SLUGS   = os.environ.get("RADAR_SLUGS", "state/slugs.json")
LABEL   = os.environ.get("RADAR_LABEL", "🆕 ATS")
HEALTH  = os.environ.get("RADAR_HEALTH", "state/health_api.json")
REJECTED = os.environ.get("RADAR_REJECTED", "state/rejected_api.json")
# компании, которые API-радар не смог опросить, — браузерный радар подхватит их в тот же день
FALLBACK = os.environ.get("RADAR_FALLBACK", "state/api_fallback.csv")

# ---------- slug из URL ----------
def _seg1(u):
    # первый сегмент пути, пропуская локаль (ats.rippling.com/en-GB/netwrix-corporation)
    p=[x for x in urlparse(u).path.split("/") if x]
    p=[x for x in p if not LOCALE.match(x.lower())]
    return p[0] if p else ""
def _sub(u):
    return urlparse(u).netloc.split(".")[0]

def slug_from_url(link, ats):
    h=urlparse(link.lower()).netloc
    if ats=="Greenhouse" and "greenhouse.io" in h:      # EU-борды живут на отдельном API
        s=_seg1(link); return (f"eu:{s}" if s and ".eu.greenhouse.io" in h else s)
    if ats=="Lever" and "lever.co" in h: return _seg1(link)
    if ats=="Ashby" and "ashbyhq.com" in h: return _seg1(link)
    if ats=="Workable":
        if "apply.workable.com" in h: return _seg1(link)
        if "workable.com" in h: return _sub(link)
    if ats=="Recruitee" and "recruitee.com" in h: return _sub(link)
    if ats=="BambooHR" and "bamboohr.com" in h: return _sub(link)
    if ats=="Breezy" and "breezy.hr" in h: return _sub(link)
    if ats=="Teamtailor" and "teamtailor.com" in h:     # insense.na.teamtailor.com -> insense.na
        return urlparse(link.lower()).netloc.split(".teamtailor.com")[0]
    if ats=="Pinpoint" and "pinpointhq.com" in h: return _sub(link)
    if ats=="Rippling":
        if "ats.rippling.com" in h: return _seg1(link)
        if "rippling" in h: return _sub(link)
    if ats=="SmartRecruiters" and "smartrecruiters.com" in h: return _seg1(link)
    if ats=="Workday" and "myworkdayjobs.com" in h:
        site=_seg1(link)
        return f"{urlparse(link).netloc.lower()}/{site}" if site else ""
    return ""

# несколько паттернов эмбеда на каждый ATS
EMBED = {
 "Greenhouse":[r"[?&]for=([\w-]+)", r"job-boards[\w.]*\.greenhouse\.io/([\w-]+)",
               r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board/?)?(?:v1/boards/)?([\w-]+)",
               r"greenhouse\.io/([\w-]+)/jobs"],
 "Lever":[r"jobs\.lever\.co/([\w-]+)", r"api\.lever\.co/v0/postings/([\w-]+)"],
 "Ashby":[r"ashbyhq\.com/(?:posting-api/job-board/)?([\w-]+)", r"job-board/([\w-]+)"],
 "Workable":[r"apply\.workable\.com/([\w-]+)", r"data-account=[\"']([\w-]+)",
             r"workable\.com/api/accounts/([\w-]+)", r"([\w-]+)\.workable\.com",
             r"workable\.com/(?:spi/v3/)?([\w-]+)"],
 "Recruitee":[r"([\w-]+)\.recruitee\.com", r"recruitee\.com/(?:c|o)/([\w-]+)",
              r"\"companyName\"\s*:\s*\"([\w-]+)\"", r"data-recruitee[\w-]*=[\"']([\w-]+)"],
 "BambooHR":[r"([\w-]+)\.bamboohr\.com"],
 "Breezy":[r"([\w-]+)\.breezy\.hr"],
 "SmartRecruiters":[r"smartrecruiters\.com/([\w-]+)", r"companyIdentifier=([\w-]+)"],
 "Teamtailor":[r"([\w-]+)\.teamtailor\.com"],
 "Rippling":[r"ats\.rippling\.com/([\w-]+)", r"board/([\w-]+)/jobs", r"([\w-]+)\.rippling"],
 "Pinpoint":[r"([\w-]+)\.pinpointhq\.com"],
}
JUNK={"www","api","embed","careers","jobs","job","boards","board","for","v0","v1",
      "spi","v3","postings","list","assets","cdn","static","widget","c","o","en"}

# названия самих ATS и их хостов — НИКОГДА не слаг компании.
# (баг: jobs.lever.co/Termius -> угадывалось "lever" -> приходили вакансии самого Lever Inc.)
ATS_WORDS={"lever","greenhouse","ashby","ashbyhq","workable","recruitee","bamboohr","breezy",
           "smartrecruiters","rippling","teamtailor","pinpoint","pinpointhq","ats","apply",
           "job-boards","boards-api","hire","hr","talent","people","career","vacancies"}
LOCALE=re.compile(r"^[a-z]{2}(-[a-z]{2})?$")      # en, en-gb, ru ... — сегменты локали

def _bad_slug(c):
    if c.startswith("eu:"): c=c[3:]
    return (not c) or c in JUNK or c in ATS_WORDS or bool(LOCALE.match(c))

def _is_ats_host(h):
    return any(w in h for w in ("lever.co","greenhouse.io","ashbyhq.com","workable.com",
               "recruitee.com","bamboohr.com","breezy.hr","smartrecruiters.com","rippling.com",
               "teamtailor.com","pinpointhq.com"))

def name_guesses(link, name=""):
    """Догадки по домену КОМПАНИИ. С хоста самой ATS ничего не угадываем."""
    out=[]
    h=urlparse(link.lower()).netloc.replace("www.","")
    if not _is_ats_host(h):
        labels=[x for x in h.split(".") if x]
        if len(labels)>=2: out.append(labels[-2])   # registrable-ish
        if labels: out.append(labels[0])            # первый сабдомен
    n=re.sub(r"[^a-z0-9 -]","",(name or "").lower()).strip()
    if n:
        out += [n.replace(" ",""), n.replace(" ","-")]
    return out

def _norm(s): return re.sub(r"[^a-z0-9]","",(s or "").lower())

def _affinity(c, link, name):
    """Насколько кандидат похож на компанию (0..2). Нужен, чтобы из нескольких
       эмбедов на странице (ClickHouse -> langfuse) первым пробовать «свой»."""
    base=_norm(name) or ""
    h=urlparse(link.lower()).netloc.replace("www.","")
    labels=[x for x in h.split(".") if x]
    dom=_norm(labels[-2]) if len(labels)>=2 else ""
    cc=_norm(c)
    score=0
    for ref in (base, dom):
        if ref and cc and (cc in ref or ref in cc): score+=1
    return score

def candidates(link, ats, html, name=""):
    cands=[]
    s=slug_from_url(link, ats)
    if s: cands += [s]
    emb=[]
    for pat in EMBED.get(ats,[]):
        emb += re.findall(pat, html or "", re.I)
    if ats=="Greenhouse":   # эмбед EU-борды: тот же слаг, но через EU API
        emb = [f"eu:{c}" for c in re.findall(r"(?:job-boards|boards)\.eu\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", html or "", re.I)] + emb
    if ats=="Workday":   # эмбед Workday: хост + сайт, угадывать по имени бессмысленно
        for host, site in re.findall(r"([\w-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", html or ""):
            emb.append(f"{host.lower()}/{site}")
        cands += [c for c in emb if "/" in c]
        return list(dict.fromkeys(cands))
    cands += emb
    cands += name_guesses(link, name)
    seen=set(); out=[]
    for c in cands:
        c=(c or "").strip()
        if _bad_slug(c.lower()) or c.lower() in seen: continue
        seen.add(c.lower()); out.append(c)
    # слаг из URL — первым; остальных сортируем по похожести на компанию (стабильно)
    head=out[:1] if s and out and out[0].lower()==s.lower() else []
    tail=out[len(head):]
    tail.sort(key=lambda c: -_affinity(c, link, name))
    # чужие эмбеды (0 похожести) пробуем, только если своих нет — но оставляем в конце
    res=[]
    for c in head+tail:
        res.append(c)
        if c!=c.lower(): res.append(c.lower())      # Lever/Ashby бывают регистрозависимы
    seen=set(); final=[]
    for c in res:
        if c not in seen: seen.add(c); final.append(c)
    return final

# ---------- обработчики: slug -> [{id,title,url,location}] (кидают исключение на не-JSON) ----------
def _json(url):
    """GET -> JSON. Любой не-2xx — исключение: иначе 404 «борда не найдена» выглядел бы как
       «у компании 0 вакансий», и битый слаг жил бы в кеше вечно."""
    r=requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def gh_api(s):
    return ("https://boards-api.eu.greenhouse.io/v1/boards/"+s[3:]) if s.startswith("eu:") \
        else ("https://boards-api.greenhouse.io/v1/boards/"+s)

def h_greenhouse(s):
    return [{"id":j.get("id"),"title":j.get("title"),"url":j.get("absolute_url"),
             "location":(j.get("location") or {}).get("name","")}
            for j in _json(f"{gh_api(s)}/jobs")["jobs"]]
def h_lever(s):
    return [{"id":j.get("id"),"title":j.get("text"),"url":j.get("hostedUrl"),
             "location":(j.get("categories") or {}).get("location","")}
            for j in _json(f"https://api.lever.co/v0/postings/{s}?mode=json")]
def h_ashby(s):
    return [{"id":j.get("id") or j.get("jobUrl"),"title":j.get("title"),
             "url":j.get("jobUrl") or j.get("applyUrl"),"location":j.get("location","")}
            for j in _json(f"https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=true").get("jobs",[])]
def h_workable(s):
    out=[]
    for j in _json(f"https://apply.workable.com/api/v1/widget/accounts/{s}").get("jobs",[]):
        sc=j.get("shortcode") or j.get("id")
        out.append({"id":sc,"title":j.get("title"),
                    "url":j.get("url") or f"https://apply.workable.com/{s}/j/{sc}/",
                    "location":j.get("location") or j.get("city","")})
    return out
def h_recruitee(s):
    return [{"id":j.get("id"),"title":j.get("title"),
             "url":j.get("careers_url") or j.get("url"),"location":j.get("location","")}
            for j in _json(f"https://{s}.recruitee.com/api/offers/").get("offers",[])]
def h_bamboohr(s):
    out=[]
    for j in _json(f"https://{s}.bamboohr.com/careers/list").get("result",[]):
        loc=j.get("location") or {}
        city=loc.get("city","") if isinstance(loc,dict) else str(loc)
        out.append({"id":j.get("id"),"title":j.get("jobOpeningName"),
                    "url":f"https://{s}.bamboohr.com/careers/{j.get('id')}","location":city})
    return out
def h_breezy(s):
    return [{"id":j.get("id") or j.get("_id"),"title":j.get("name"),"url":j.get("url"),
             "location":(j.get("location") or {}).get("name","")}
            for j in _json(f"https://{s}.breezy.hr/json")]
def h_smartrecruiters(s):
    out=[]
    for j in _json(f"https://api.smartrecruiters.com/v1/companies/{s}/postings").get("content",[]):
        loc=j.get("location") or {}
        out.append({"id":j.get("id"),"title":j.get("name"),
                    "url":f"https://jobs.smartrecruiters.com/{s}/{j.get('id')}",
                    "location":loc.get("city","") if isinstance(loc,dict) else ""})
    return out

def h_rippling(s):
    d=_json(f"https://api.rippling.com/platform/api/ats/v1/board/{s}/jobs")
    items = d.get("items") or d.get("jobs") or (d if isinstance(d,list) else [])
    out=[]
    for j in items:
        loc=j.get("workLocation") or j.get("location") or ""
        if isinstance(loc,dict): loc=loc.get("label") or loc.get("name") or ""
        out.append({"id":j.get("id") or j.get("uuid") or j.get("url"),
                    "title":j.get("name") or j.get("title"),
                    "url":j.get("url") or j.get("jobUrl") or j.get("applicationUrl") or j.get("apply_url"),
                    "location":loc})
    return out

def h_teamtailor(s):
    r=requests.get(f"https://{s}.teamtailor.com/jobs.rss", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    root=ET.fromstring(r.content)          # не-XML (404) кинет исключение -> кандидат отсеется
    out=[]
    for item in root.iter("item"):
        title=(item.findtext("title") or "").strip()
        link=(item.findtext("link") or "").strip()
        loc=""
        for el in item:                    # локация в namespaced-теге, best-effort
            if el.tag.lower().endswith("location") and (el.text or "").strip():
                loc=el.text.strip(); break
        if title and link:
            out.append({"id":link,"title":title,"url":link,"location":loc})
    return out

def h_pinpoint(s):
    d=_json(f"https://{s}.pinpointhq.com/postings.json")
    if "Head of DEI" in str(d)[:5000]:                   # неизвестный поддомен Pinpoint отдаёт демо-вакансии
        raise ValueError("pinpoint: демо-доска, а не компания")
    items = d.get("data") if isinstance(d,dict) else d
    if items is None and isinstance(d,dict): items = d.get("postings") or d.get("results") or []
    out=[]
    for j in (items or []):
        a = j.get("attributes", j) if isinstance(j,dict) else {}
        loc = a.get("location") or a.get("location_name") or a.get("city") or ""
        if isinstance(loc,dict): loc = loc.get("name") or loc.get("city") or ""
        out.append({"id": j.get("id") or a.get("id") or a.get("url"),
                    "title": a.get("title") or a.get("name"),
                    "url": a.get("url") or a.get("link") or a.get("apply_url") or a.get("careers_url"),
                    "location": loc})
    return out

# Workday: публичный JSON (тот же, что дёргает их собственная страница).
# slug = "tenant.wdN.myworkdayjobs.com/SiteName". Вакансий у крупных тысячи, поэтому
# спрашиваем поиском по нашим же ключевикам — фильтр потом всё равно отсеет лишнее.
WD_QUERIES=["marketing","growth","demand","crm","seo","lifecycle","digital","performance","martech"]
def h_workday(s):
    host, site = s.split("/",1)
    tenant = host.split(".")[0]
    api=f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out={}; ok=False
    for q in WD_QUERIES:
        off=0
        while off<200:
            r=requests.post(api, json={"appliedFacets":{},"limit":20,"offset":off,"searchText":q},
                            headers={**UA,"Content-Type":"application/json","Accept":"application/json"},
                            timeout=TIMEOUT)
            r.raise_for_status(); d=r.json(); ok=True                   # не-JSON -> исключение -> кандидат отсеется
            posts=d.get("jobPostings") or []
            for j in posts:
                path=j.get("externalPath") or ""
                if not path: continue
                out[path]={"id":path,"title":j.get("title"),
                           "url":f"https://{host}/{site}{path}","location":j.get("locationsText","")}
            off+=20
            if off>=(d.get("total") or 0) or not posts: break
    if not ok: raise ValueError("workday: нет ответа")
    return list(out.values())

HANDLERS={"Workday":h_workday,"Greenhouse":h_greenhouse,"Lever":h_lever,"Ashby":h_ashby,"Workable":h_workable,
          "Recruitee":h_recruitee,"BambooHR":h_bamboohr,"Breezy":h_breezy,
          "SmartRecruiters":h_smartrecruiters,"Rippling":h_rippling,"Teamtailor":h_teamtailor,"Pinpoint":h_pinpoint}

def fetch_company(link, ats, cache, name=""):
    """Возвращает (slug, jobs) либо (None, None).
       Порядок: свои кандидаты (похожие на компанию) -> чужие эмбеды только если своих нет."""
    key=f"{ats}|{link}"
    cached=cache.get(key)
    if cached:
        try: return cached, HANDLERS[ats](cached)
        except (requests.ConnectionError, requests.Timeout):
            raise                                  # сеть/таймаут — временное, кеш не трогаем
        except requests.HTTPError as e:
            code=getattr(e.response, "status_code", 0) or 0
            if code==429 or code>=500: raise       # ATS лежит/лимит — временное, кеш не трогаем
        except Exception: pass  # 404 / не-JSON: закешированный slug протух — резолвим заново
    from_url=(slug_from_url(link, ats) or "").lower()
    html=""
    if not from_url:
        try: html=requests.get(link, headers=UA, timeout=TIMEOUT).text
        except Exception: pass
    own_valid=None; foreign_hit=None
    for slug in candidates(link, ats, html, name):
        mine = slug.lower()==from_url or _affinity(slug, link, name)>0
        try:
            jobs=HANDLERS[ats](slug)      # кинет исключение если не JSON
        except Exception:
            continue
        if mine:
            if jobs: cache[key]=slug; return slug, jobs
            if own_valid is None: own_valid=(slug, jobs)
        elif foreign_hit is None:
            foreign_hit=(slug, jobs)      # чужой эмбед — запасной вариант
    for hit in (own_valid, foreign_hit):
        if hit:
            if hit is foreign_hit:
                print(f"  ? {name}: слаг '{hit[0]}' не похож на компанию — проверь руками")
            cache[key]=hit[0]; return hit
    return None, None

def main():
    companies=list(csv.DictReader(open(IN_FILE, encoding="utf-8-sig")))
    seen=load(STATE, {}); slugs=load(SLUGS, {})
    health=Health(HEALTH, LABEL)
    print(f"=== API-радар | {len(companies)} компаний | {'--all' if '--all' in sys.argv else 'только новые'} ===")

    jobs={}; failed=[]
    for r in companies:
        name=(r.get("Name") or "").strip(); link=(r.get("Link") or "").strip()
        ats=(r.get("ATS") or "").strip()
        try:
            slug, js = fetch_company(link, ats, slugs, name) if ats in HANDLERS else (None, None)
            err = "" if slug else (f"не подобрался слаг ({ats})" if ats in HANDLERS else f"ATS «{ats}» не поддерживается")
        except Exception as e:
            slug, err = None, f"{type(e).__name__} {str(e)[:60]}"
        if not slug:
            print(f"  ⚠ {name}: {err}")
            health.mark(name, err=err); failed.append(r); continue
        n=0
        for j in js:
            if j.get("title") and j.get("url"):
                jid=f"{ats}:{slug}:{j.get('id')}"
                jobs[jid]={**j, "company":name, "jid":jid, "ats":ats, "slug":slug}; n+=1
        health.mark(name, jobs=n, note=f"{ats}:{slug}")
        time.sleep(0.2)
    save(SLUGS, slugs)

    # то, что API не смог опросить, отдаём браузерному радару (он идёт позже в тот же день)
    with open(FALLBACK, "w", encoding="utf-8", newline="") as f:
        w=csv.writer(f); w.writerow(["Name","Link"])
        for r in failed: w.writerow([(r.get("Name") or "").strip(), (r.get("Link") or "").strip()])

    alljobs=list(jobs.values())
    mk=[j for j in alljobs if is_marketing(j["title"], j.get("location",""))]
    save(REJECTED, near_misses(alljobs))
    print(f"компаний: {len(companies)} | вакансий: {len(jobs)} | не опросились: {len(failed)}")
    report(mk, seen, STATE, label=LABEL)
    health.finish({"jobs_total": len(jobs), "marketing": len(mk)})

if __name__=="__main__":
    main()
