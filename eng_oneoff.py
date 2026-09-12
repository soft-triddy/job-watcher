# -*- coding: utf-8 -*-
"""
РАЗОВЫЙ прогон по всем трём источникам с ИНЖЕНЕРНЫМ фильтром (для подруги).
Ловит только Engineering Manager / Head of Engineering. В телегу — одним сообщением.
Не трогает seen-файлы и маркетинговый фильтр. После использования удали этот файл и eng.yml.
"""
import csv, time
from radar import HANDLERS, fetch_company, send, _chunks
import himalayas_radar as H
import browser_radar as B

def is_eng(title):
    t = (title or "").lower()
    return ("engineering manager" in t) or ("head of engineering" in t)

def main():
    results = []   # (source, company, title, url)

    # 1) API-радар (твои ATS-компании)
    print("API-радар...")
    slugs = {}
    try:
        rows = list(csv.DictReader(open("radar_companies.csv", encoding="utf-8-sig")))
    except Exception as e:
        rows = []; print("  нет radar_companies.csv:", e)
    for r in rows:
        ats = (r.get("ATS") or "").strip()
        if ats not in HANDLERS: continue
        try:
            slug, js = fetch_company((r.get("Link") or "").strip(), ats, slugs)
        except Exception:
            continue
        for j in (js or []):
            if is_eng(j.get("title")):
                results.append(("API", r.get("Name",""), j["title"], j.get("url","")))

    # 2) Himalayas (свои инженерные запросы, тот же гейт worldwide+Full Time)
    print("Himalayas...")
    H.QUERIES = ["engineering manager", "head of engineering", "engineering"]
    try:
        for j in H.collect():
            if is_eng(j.get("title")):
                results.append(("Himalayas", j.get("company",""), j["title"], j.get("url","")))
    except Exception as e:
        print("  himalayas упал:", e)

    # 3) Браузер (JS-страницы)
    print("Браузер...")
    try:
        from playwright.sync_api import sync_playwright
        brows = list(csv.DictReader(open("browser_companies.csv", encoding="utf-8-sig")))
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            ctx = browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124 Safari/537.36"))
            for i, r in enumerate(brows, 1):
                link = (r.get("Link") or "").strip()
                if not link: continue
                page = ctx.new_page()
                try:
                    found = B.scrape(page, link)
                except Exception:
                    found = []
                page.close()
                for j in (found or []):
                    if is_eng(j.get("title")):
                        results.append(("Browser", r.get("Name",""), j["title"], j.get("url","")))
                if i % 30 == 0: print(f"  ...{i}/{len(brows)}")
            browser.close()
    except Exception as e:
        print("  браузер упал:", e)

    # дедуп по url
    uniq = {}
    for src, comp, title, url in results:
        uniq[url or f"{comp}|{title}"] = (src, comp, title, url)
    rows_out = list(uniq.values())

    print(f"\nНайдено (Eng Manager / Head of Engineering): {len(rows_out)}")
    if not rows_out:
        send("🛠 Разовый инженерный поиск: Engineering Manager / Head of Engineering — ничего не найдено сейчас.")
        return
    lines = [f"🛠 Инженерные роли (разовый прогон, {len(rows_out)}):\n"]
    for src, comp, title, url in rows_out:
        lines.append(f"• {comp}: {title} [{src}]\n{url}")
    send("\n".join(lines))
    print("Отправлено в телегу.")

if __name__ == "__main__":
    main()
