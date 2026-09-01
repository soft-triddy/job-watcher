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

IN_FILE = "browser_companies.csv"
STATE   = "seen_browser.json"
WAIT_MS = 4000          # сколько ждать дозагрузку вакансий после открытия
NAV_TIMEOUT = 30000

TITLE_KEYS = ["title","name","jobtitle","position","text","role","vacancyname","jobopeningname","headline"]
URL_KEYS   = ["url","absolute_url","hostedurl","joburl","applyurl","apply_url","link",
              "careers_url","permalink","href","canonicalurl","landing_page_url"]
LOC_KEYS   = ["location","city","worklocation","office","joblocation","location_name","region"]

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

def scrape(page, link):
    """Открываем страницу, собираем JSON-ответы + JSON-LD, возвращаем список вакансий."""
    captured=[]
    def on_response(resp):
        try:
            ct=(resp.headers or {}).get("content-type","")
            if "json" in ct.lower():
                captured.append(resp.json())
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

    best=[]
    for data in captured:
        got=extract_from_json(data, link)
        if len(got)>len(best): best=got
    ld=extract_jsonld(html, link)
    if len(ld)>len(best): best=ld
    return best

def main():
    dump_all = "--all" in sys.argv
    print(f"=== browser_radar | режим: {'ВСЕ текущие' if dump_all else 'только новые'} ===")

    companies=[]
    with open(IN_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): companies.append(r)

    seen_existed=os.path.exists(STATE)
    seen=load(STATE, {})

    from playwright.sync_api import sync_playwright
    jobs=[]; errors=[]; empty=0
    with sync_playwright() as p:
        browser=p.chromium.launch(args=["--no-sandbox"])
        ctx=browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124 Safari/537.36"))
        for i,r in enumerate(companies,1):
            name=(r.get("Name") or "").strip(); link=(r.get("Link") or "").strip()
            if not link: continue
            page=ctx.new_page()
            try:
                found=scrape(page, link)
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__} {str(e)[:50]}"); found=[]
            page.close()
            if not found: empty+=1
            for j in found:
                if not j.get("title") or not j.get("url"): continue
                jobs.append({**j,"company":name,"jid":j["url"]})
            if i%20==0: print(f"  ...{i}/{len(companies)}")
        browser.close()

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
        lines=[header]
        for j in items:
            loc=f" — {j['location']}" if j.get("location") else ""
            lines.append(f"• {j['company']}: {j['title']}{loc}\n{j['url']}")
        return "\n".join(lines)

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
    main()
