# -*- coding: utf-8 -*-
"""
Job Radar. Каждый запуск:
  1) читает radar_companies.csv (Name, Link, ATS)
  2) по поддержанным ATS тянет текущие вакансии через публичные JSON API
  3) оставляет только маркетинговые (RU+EN ключевые слова)
  4) сравнивает с прошлым запуском (seen.json) и шлёт НОВЫЕ в Telegram
Первый запуск не спамит: запоминает текущее и шлёт короткую сводку.
Резолвер slug устойчивый: пробует несколько кандидатов и проверяет их живым запросом.
"""
import csv, json, os, re, sys, time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    sys.exit("Нет requests. В Replit: Shell -> pip install requests")

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT  = os.environ.get("TG_CHAT")
IN_FILE  = "radar_companies.csv"
STATE    = "seen.json"
SLUGS    = "slugs.json"
TIMEOUT  = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
VERSION = "radar-2026-09-23"

# что БЕРЁМ (подстрокой в ЗАГОЛОВКЕ). brand/email/acquisition убраны — не её специализация
KW = ["market","growth","crm","lifecycle","demand","seo","pmm","martech",
      "paid","performance","digital"]

# роль не та — выкидываем по ЗАГОЛОВКУ (подстрокой)
NEG_ROLE = ["market research analyst","stock market","supermarket","capital market",
           "brand","email","smm","social media","content","analyst","analytics",
           "design","product marketing","business development","public relations",
           "corporate communications","integration",
           "recruiter","community","customer success","product manager","acquisition",
           "engineer","account executive","partner","deployment","event","influencer",
           "talent acquisition","cpo",
           "crypto","web3","blockchain","affiliate","copywriter","producer",
           "data scien","account manager","account supervisor",
           # 2026-09-23: продажи, креатив, младшие грейды, «общие» заявки
           "sales","account strategist","curation","studio manager","creative","consultant",
           "representative","coordinator","assistant","junior","praktikant","media buyer",
           "operative","expression of interest","general application"]

# формат/гео — выкидываем по ЗАГОЛОВКУ + ЛОКАЦИИ вместе (тип работы и штат часто в локации!)
NEG_GEO_SUB = ["hybrid"]
NEG_GEO_WORD = ["us","usa"]

# US-only отсев: полные названия штатов (georgia НЕ баним — это ещё и страна СНГ)
US_STATE_FULL = ["alabama","alaska","arizona","arkansas","california","colorado",
 "connecticut","delaware","florida","hawaii","idaho","illinois","indiana","iowa",
 "kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan",
 "minnesota","mississippi","missouri","montana","nebraska","nevada","new hampshire",
 "new jersey","new mexico","new york","north carolina","north dakota","ohio","oklahoma",
 "oregon","pennsylvania","rhode island","south carolina","south dakota","tennessee",
 "texas","utah","vermont","virginia","washington","west virginia","wisconsin","wyoming"]

# двухбуквенные коды штатов; исключены пересечения со странами:
# CA(Канада) DE(Германия) MD(Молдова) AZ(Азербайджан) AL(Албания) IN(Индия)
US_STATE_CODE = {"AK","AR","CO","CT","FL","HI","IA","ID","IL","KS","KY","LA","MA","ME",
 "MI","MN","MO","MS","MT","NC","ND","NE","NH","NJ","NM","NV","NY","OH","OK","OR","PA",
 "RI","SC","SD","TN","TX","UT","VA","VT","WA","WI","WV","WY","DC"}

def is_marketing(title, location=""):
    raw_t = title or ""
    t = raw_t.lower()
    # 1) не та роль — по заголовку
    if any(n in t for n in NEG_ROLE): return False
    if re.search(r"\bpr\b", t): return False            # PR как целое слово
    if re.search(r"\bintern(ship)?\b", t): return False  # стажировка, но не «international»
    if "associate" in t and "associate director" not in t: return False   # младший грейд
    # 2) это вообще маркетинг? — по заголовку
    if not any(k in t for k in KW): return False
    # 3) формат/гео — по заголовку И локации вместе
    raw_blob = raw_t + " " + (location or "")
    blob = raw_blob.lower()
    if any(n in blob for n in NEG_GEO_SUB): return False           # hybrid
    if any(re.search(r"\b"+w+r"\b", blob) for w in NEG_GEO_WORD): return False   # us / usa
    if any(re.search(r"\b"+re.escape(s)+r"\b", blob) for s in US_STATE_FULL): return False
    if any(tok in US_STATE_CODE for tok in re.findall(r"\b[A-Z]{2}\b", raw_blob)): return False
    return True

# ---------- slug из URL ----------
def _seg1(u):
    # первый сегмент пути, пропуская локаль (ats.rippling.com/en-GB/netwrix-corporation)
    p=[x for x in urlparse(u).path.split("/") if x]
    p=[x for x in p if not re.match(r"^[a-zA-Z]{2}(-[a-zA-Z]{2})?$", x)]
    return p[0] if p else ""
def _sub(u):
    return urlparse(u).netloc.split(".")[0]

def slug_from_url(link, ats):
    h=urlparse(link.lower()).netloc
    if ats=="Greenhouse" and "greenhouse.io" in h: return _seg1(link)
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
def _json(url): return requests.get(url, headers=UA, timeout=TIMEOUT).json()

def h_greenhouse(s):
    return [{"id":j.get("id"),"title":j.get("title"),"url":j.get("absolute_url"),
             "location":(j.get("location") or {}).get("name","")}
            for j in _json(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs").get("jobs",[])]
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
            d=r.json(); ok=True                   # не-JSON -> исключение -> кандидат отсеется
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
    if cached and _bad_slug(cached.lower()):
        cache.pop(key, None); cached=None          # старый битый слаг (lever/ats/en-gb) — выкидываем
    if cached:
        try: return cached, HANDLERS[ats](cached)
        except Exception: pass  # закешированный slug протух — резолвим заново
    html=""
    if not slug_from_url(link, ats):
        try: html=requests.get(link, headers=UA, timeout=TIMEOUT).text
        except Exception: html=""
    own_valid=None; foreign_hit=None
    from_url=(slug_from_url(link, ats) or "").lower()
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

def send(text):
    if not (TG_TOKEN and TG_CHAT):
        print("!! Нет TG_TOKEN/TG_CHAT. Сообщение не отправлено:\n"+text[:300]); return False
    ok=True
    for chunk in _chunks(text, 3800):
        try:
            r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                            json={"chat_id":TG_CHAT,"text":chunk,"disable_web_page_preview":True},
                            timeout=TIMEOUT).json()
        except Exception as e:
            print("!! Telegram: сеть/ошибка:",e); ok=False; continue
        if not r.get("ok"):
            print(f"!! Telegram отклонил: {r.get('error_code')} {r.get('description')}"); ok=False
    if ok: print("Telegram: отправлено ✓")
    return ok

def _chunks(text, limit):
    """Режем по границам строк: строка (=вакансия с её ссылкой) не разрывается.
       Если одна строка длиннее лимита — только тогда режем её жёстко."""
    out=[]; cur=""
    for line in text.split("\n"):
        piece = (cur + "\n" + line) if cur else line
        if len(piece) <= limit:
            cur = piece
        else:
            if cur: out.append(cur)
            if len(line) <= limit:
                cur = line
            else:                       # аварийный случай: сверхдлинная одиночная строка
                for i in range(0, len(line), limit):
                    out.append(line[i:i+limit])
                cur = ""
    if cur: out.append(cur)
    return out

def load(path,default):
    try: return json.load(open(path,encoding="utf-8"))
    except Exception: return default

def main():
    dump_all = "--all" in sys.argv
    print(f"=== {VERSION} | режим: {'ВСЕ текущие' if dump_all else 'только новые'} ===")
    print(f"фильтр: роль-бан {len(NEG_ROLE)} | hybrid(гео) в бане: {'hybrid' in NEG_GEO_SUB} | "
          f"штатов(полных): {len(US_STATE_FULL)}")
    companies=[]
    with open(IN_FILE,encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): companies.append(r)
    seen_existed=os.path.exists(STATE)
    seen=load(STATE,{}); slugs=load(SLUGS,{})

    jobs=[]; skipped=0; errors=[]; seen_links=set()
    for r in companies:
        name=(r.get("Name") or "").strip(); link=(r.get("Link") or "").strip()
        ats=(r.get("ATS") or "").strip()
        if ats not in HANDLERS: skipped+=1; continue
        lk=link.lower().rstrip("/").strip()
        if lk in seen_links: continue      # компания-дубль в списке
        seen_links.add(lk)
        try:
            slug,js=fetch_company(link,ats,slugs,name)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__} {str(e)[:50]}"); continue
        if not slug or js is None:
            errors.append(f"{name}: не удалось определить рабочий slug ({ats})"); continue
        for j in js:
            if not j.get("title") or not j.get("url"): continue
            jobs.append({**j,"company":name,"jid":f"{ats}:{slug}:{j.get('id')}","ats":ats,"slug":slug})
        time.sleep(0.2)

    uniq={}
    for j in jobs: uniq[j["jid"]]=j
    jobs=list(uniq.values())
    mk=[j for j in jobs if is_marketing(j["title"], j.get("location",""))]
    new=[j for j in mk if j["jid"] not in seen]
    for j in mk: seen[j["jid"]]=j["title"]
    json.dump(seen,open(STATE,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    json.dump(slugs,open(SLUGS,"w",encoding="utf-8"),ensure_ascii=False,indent=1)

    print(f"компаний: {len(companies)} | опрошено ATS: {len(companies)-skipped} | пропущено: {skipped}")
    print(f"вакансий: {len(jobs)} | маркетинговых: {len(mk)} | новых: {len(new)} | не срослось: {len(errors)}")
    for e in errors[:20]: print("  ⚠",e)

    def fmt(items, header):
        from scorer import score_jobs, fmt_job, rank
        score_jobs(items)
        from scorer import diag_line
        return "\n\n".join([header.rstrip()] + [fmt_job(j) for j in rank(items)]) + diag_line()

    if dump_all:
        if mk: send(fmt(mk, f"📋 Все текущие маркетинг-вакансии ({len(mk)}):\n"))
        else:  send("📋 Сейчас маркетинговых вакансий не найдено.")
        print(f"Режим --all: отправлено {len(mk)}."); return
    if not seen_existed:
        send(f"✅ Радар включён. Слежу за {len(mk)} маркетинговыми вакансиями в {len(HANDLERS)} типах ATS. "
             f"Дальше только новые."); print("Первый запуск: сводка отправлена."); return
    if new:
        send(fmt(new, f"🆕 Новые маркетинг-вакансии ({len(new)}):\n")); print(f"Отправлено новых: {len(new)}")
    else:
        print("Новых нет.")

if __name__=="__main__":
    main()
