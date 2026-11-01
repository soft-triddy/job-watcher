# -*- coding: utf-8 -*-
"""
Разовый сбор ПОЛНЫХ текстов вакансий (без обрезки) и дат публикации для анализа.
Вход: discover/oct_items.json — [{vid, title, company, urls, api: [ats, slug, id] | null}].
Выход: discover/oct_fulltext.json — [{vid, text, date_posted, method, url}].
Порядок: публичный API ATS (Greenhouse / Lever / Ashby: текст + дата) → JSON-LD JobPosting на странице
(description + datePosted) → видимый текст страницы через браузер. В Telegram ничего не шлёт.
"""
import html, json, re, sys, time

import requests
from core import UA
from scorer import _strip, _jsonld_desc

IN, OUT = "discover/oct_items.json", "discover/oct_fulltext.json"
T = 30

def _get(url):
    return requests.get(url, headers=UA, timeout=T)

def from_api(ats, slug, jid):
    if ats == "Greenhouse":
        base = ("https://boards-api.eu.greenhouse.io/v1/boards/" + slug[3:]) if slug.startswith("eu:") else \
               "https://boards-api.greenhouse.io/v1/boards/" + slug
        d = _get(f"{base}/jobs/{jid}").json()
        return _strip(html.unescape(d.get("content", ""))), d.get("first_published") or d.get("updated_at") or "", d.get("absolute_url", "")
    if ats == "Lever":
        d = _get(f"https://api.lever.co/v0/postings/{slug}/{jid}").json()
        parts = [d.get("descriptionPlain", "")] + [f"{l.get('text','')}:\n{_strip(l.get('content',''))}" for l in (d.get("lists") or [])] \
                + [d.get("additionalPlain", "")]
        ts = d.get("createdAt")
        return "\n".join(p for p in parts if p), (time.strftime("%Y-%m-%d", time.gmtime(ts / 1000)) if ts else ""), d.get("hostedUrl", "")
    if ats == "Ashby":
        for j in _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json().get("jobs", []):
            if j.get("id") == jid:
                return j.get("descriptionPlain") or _strip(j.get("descriptionHtml", "")), j.get("publishedAt", ""), j.get("jobUrl", "")
    return "", "", ""

def _date_ld(h):
    m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', h or "")
    return m.group(1) if m else ""

def main():
    items = json.load(open(IN, encoding="utf-8"))
    out, need_browser = {}, []
    for it in items:
        rec = {"vid": it["vid"], "text": "", "date_posted": "", "method": "", "url": (it["urls"] or [""])[0]}
        if it.get("api"):
            try:
                t, d, u = from_api(*it["api"])
                if len(t) > 300: rec.update(text=t, date_posted=d, method="api", url=u or rec["url"])
            except Exception as e:
                print(f"  api {it['vid']}: {type(e).__name__}")
        for u in ([] if rec["text"] else it["urls"]):
            try:
                h = _get(u).text
                t = _jsonld_desc(h)
                if len(t) > 300: rec.update(text=t, date_posted=_date_ld(h), method="json-ld", url=u); break
                rec["date_posted"] = rec["date_posted"] or _date_ld(h)
            except Exception as e:
                print(f"  page {it['vid']}: {type(e).__name__}")
        if not rec["text"] and it["urls"]: need_browser.append(rec)
        out[it["vid"]] = rec
        time.sleep(0.3)
    print(f"API/JSON-LD: {sum(1 for r in out.values() if r['text'])} | в браузер: {len(need_browser)}", flush=True)
    if need_browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"]); pg = b.new_context(user_agent=UA["User-Agent"]).new_page()
            for rec in need_browser:
                try:
                    pg.goto(rec["url"], wait_until="domcontentloaded", timeout=30000); pg.wait_for_timeout(3000)
                    h = pg.content()
                    t = _jsonld_desc(h) or pg.inner_text("body", timeout=10000)
                    if len(t) > 300:
                        rec.update(text=t, method="browser", date_posted=rec["date_posted"] or _date_ld(h))
                except Exception as e:
                    print(f"  browser {rec['vid']}: {type(e).__name__}")
            b.close()
    json.dump(list(out.values()), open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"готово: с текстом {sum(1 for r in out.values() if r['text'])} из {len(out)} -> {OUT}")

if __name__ == "__main__":
    main()
