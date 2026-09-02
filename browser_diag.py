# -*- coding: utf-8 -*-
"""
Диагностика браузерного прохода: НЕ ищет вакансии для отправки, а по каждой странице
печатает, почему получилось пусто. Различает:
  EMPTY  — реально пусто (нет ни JSON-вакансий, ни ссылок-вакансий в DOM)
  MISSED — вакансии на странице ЕСТЬ (ссылки в DOM), но наши методы их не достают
  OK     — что-то извлеклось нашими методами
  ERROR  — страница не открылась / таймаут
Ничего не шлёт в Telegram, состояние не трогает. Запускать вручную на Actions.
"""
import csv, re, sys
from browser_radar import extract_from_json, extract_jsonld

IN_FILE="browser_companies.csv"
WAIT_MS=4000
NAV_TIMEOUT=30000

# ссылки, которые пахнут вакансией (в href)
JOB_HREF = re.compile(r'/(jobs?|careers?|vacan\w*|positions?|openings?|opportunit\w*|apply|roles?)(/|-|_|\?|=|$)', re.I)

def diag_page(page, link):
    captured=[]
    def on_response(resp):
        try:
            if "json" in (resp.headers or {}).get("content-type","").lower():
                captured.append(resp.json())
        except Exception: pass
    page.on("response", on_response)
    nav_ok=True
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    except Exception:
        nav_ok=False
    page.wait_for_timeout(WAIT_MS)
    try: html=page.content()
    except Exception: html=""
    # наши методы
    jobs=[]
    for d in captured:
        g=extract_from_json(d, link)
        if len(g)>len(jobs): jobs=g
    ld=extract_jsonld(html, link)
    our = max(len(jobs), len(ld))
    # ссылки-вакансии в отрисованном DOM
    try:
        hrefs=page.eval_on_selector_all("a", "els => els.map(e => e.getAttribute('href') || '')")
    except Exception:
        hrefs=[]
    joblinks=len({h for h in hrefs if h and JOB_HREF.search(h)})
    page.remove_listener("response", on_response)
    return {"nav":nav_ok, "html":len(html), "json_resp":len(captured),
            "jsonld":len(ld), "our":our, "joblinks":joblinks}

def classify(d):
    if not d["nav"] and d["html"]<500: return "ERROR"
    if d["our"]>0: return "OK"
    if d["joblinks"]>=3: return "MISSED"
    return "EMPTY"

def main():
    companies=[]
    with open(IN_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): companies.append(r)

    from playwright.sync_api import sync_playwright
    from collections import Counter
    tally=Counter(); missed=[]
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
                d=diag_page(page, link)
            except Exception:
                d={"nav":False,"html":0,"json_resp":0,"jsonld":0,"our":0,"joblinks":0}
            page.close()
            c=classify(d); tally[c]+=1
            if c=="MISSED": missed.append((name, d["joblinks"], link))
            print(f"[{c:<6}] {name[:26]:<26} html:{d['html']//1000}k json:{d['json_resp']} "
                  f"our:{d['our']} joblinks:{d['joblinks']}")
        browser.close()

    print("\n==== ИТОГ ====")
    for k in ["OK","MISSED","EMPTY","ERROR"]:
        if tally[k]: print(f"{tally[k]:>4}  {k}")
    if missed:
        print("\n== MISSED (вакансии есть, но не достаём — кандидаты на 3-й метод) ==")
        for name,jl,link in sorted(missed, key=lambda x:-x[1]):
            print(f"  {jl:>3} ссылок  {name}  {link}")

if __name__=="__main__":
    main()
