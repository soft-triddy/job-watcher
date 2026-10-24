# -*- coding: utf-8 -*-
"""
Слепок: все открытые сейчас маркетинговые вакансии со всех источников радара — с описаниями,
без оценщика и без Telegram. seen и отчёты здоровья не трогает.
Результат: discover/snapshot.json (оценку и разбор делает Claude по этому файлу).

Источники: API-компании (основной + музтех), браузерные страницы (основной + музтех),
Himalayas (те же запросы, что у радара), MBW, Хабр. Фильтр — тот же is_marketing, что у радаров.
Поле sent: присылал ли радар эту вакансию раньше (есть в seen-файлах).
"""
import csv, json, os, re, sys, tempfile, time

import requests
from core import UA, TIMEOUT, is_marketing, load

# ROLE=marketing (по умолчанию) — маркетинг для Насти; другие роли — разовые слепки для знакомых
ROLE = os.environ.get("ROLE", "marketing")
ROLES = {
    "ios": {"title": re.compile(r"\bios\b|\bswift(ui)?\b|\biphone\b|mobile[\s-]*(engineer|developer|dev)\b", re.I),
            "himalayas": ["ios", "swift", "swiftui", "mobile developer", "mobile engineer"],
            "habr": ["iOS", "Swift"]},
}
OUT = "discover/snapshot.json" if ROLE == "marketing" else f"discover/snapshot_{ROLE}.json"

def _match(j):
    if ROLE == "marketing": return is_marketing(j.get("title") or "", j.get("location") or "")
    return bool(ROLES[ROLE]["title"].search(j.get("title") or ""))
DESC = 4000                                 # символов описания на вакансию

class _NoHealth:
    def mark(self, *a, **k): pass

def _seen_keys():
    keys = set()
    for f in os.listdir("state"):
        if f.startswith("seen") and f.endswith(".json"): keys |= set(load(os.path.join("state", f), {}))
    return keys

def api_jobs():
    import radar
    out = []
    for lst, slugs_f, music in [("lists/radar_companies.csv", "state/slugs.json", False),
                                ("lists/music_radar_companies.csv", "state/slugs_music.json", True)]:
        slugs = load(slugs_f, {})
        for r in csv.DictReader(open(lst, encoding="utf-8-sig")):
            name, link, ats = r["Name"].strip(), r["Link"].strip(), (r.get("ATS") or "").strip()
            if ats not in radar.HANDLERS: continue
            try: slug, js = radar.fetch_company(link, ats, slugs, name)
            except Exception as e: print(f"  ⚠ {name}: {type(e).__name__}"); continue
            for j in js or []:
                if j.get("title") and j.get("url"):
                    out.append({**j, "company": name, "ats": ats, "slug": slug, "music": music,
                                "key": f"{ats}:{slug}:{j.get('id')}", "source": f"API ({ats})"})
            time.sleep(0.2)
    print(f"API: {len(out)}", flush=True)
    return out

def browser_jobs():
    out = []
    for lst, music in [("lists/browser_companies.csv", False), ("lists/music_browser_companies.csv", True)]:
        rows = list(csv.DictReader(open(lst, encoding="utf-8-sig")))
        tmp = os.path.join(tempfile.gettempdir(), f"snap_{int(music)}.csv")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh); w.writerow(["Name", "Link"]); [w.writerow([r["Name"], r["Link"]]) for r in rows]
        os.environ.update(BROWSER_COMPANIES=tmp, BROWSER_EXTRA="")    # воркеры читают список из окружения
        import browser_radar
        browser_radar.IN_FILE, browser_radar.EXTRA = tmp, ""
        js = browser_radar.run_isolated(browser_radar.load_companies(), _NoHealth())
        out += [{**j, "music": music, "key": j["url"], "source": "браузер"} for j in js]
    print(f"браузер: {len(out)}", flush=True)
    return out

def himalayas_jobs():
    if ROLE != "marketing": return _himalayas_role()
    import himalayas_radar as h
    js = h.collect(_NoHealth())
    out = [{**j, "music": False, "key": j["guid"], "source": "Himalayas"} for j in js]
    print(f"Himalayas: {len(out)}", flush=True)
    return out

def _himalayas_role():
    """Поиск Himalayas по запросам роли: все типы занятости, без ограничения по странам."""
    out = {}
    for q in ROLES[ROLE]["himalayas"]:
        for page in range(1, 6):
            r = None
            for attempt in range(4):
                r = requests.get("https://himalayas.app/jobs/api/search", headers=UA, timeout=TIMEOUT,
                                 params={"q": q, "sort": "recent", "page": page})
                if r.status_code in (429, 500, 502, 503): time.sleep(15 * (attempt + 1)); continue
                break
            if r is None or r.status_code != 200: print(f"  ⚠ Himalayas «{q}» p{page}: {getattr(r, 'status_code', '?')}"); break
            d = r.json(); batch = d.get("jobs", [])
            for j in batch:
                locs = [x if isinstance(x, str) else (x.get("name") or "") for x in (j.get("locationRestrictions") or [])]
                loc = ", ".join(locs) if len(locs) <= 6 else f"{len(locs)} стран: " + ", ".join(locs[:4]) + "…"
                out[j.get("guid")] = {"title": j.get("title") or "", "company": j.get("companyName") or "",
                                      "url": j.get("applicationLink") or "", "location": (loc or "Worldwide") + " · Remote",
                                      "description": j.get("description") or j.get("excerpt") or "",
                                      "etype": j.get("employmentType") or "", "seniority": ", ".join(j.get("seniority") or []),
                                      "music": False, "key": j.get("guid"), "source": "Himalayas"}
            if not batch or page * (d.get("limit") or len(batch)) >= d.get("totalCount", 0): break
            time.sleep(1.2)
    print(f"Himalayas: {len(out)}", flush=True)
    return list(out.values())

def mbw_jobs():
    import mbw_radar
    out = []
    for page in range(1, 4):
        try: items = mbw_radar.fetch(page)
        except Exception: break
        out += [{**mbw_radar.to_job(it), "music": True, "source": "MBW"} for it in items]
        if len(items) < 100: break
    out = [{**j, "key": j["guid"]} for j in out if not j.get("filled")]
    print(f"MBW: {len(out)}", flush=True)
    return out

def habr_jobs():
    import boards_radar
    out = []
    for q in (boards_radar.HABR if ROLE == "marketing" else ROLES[ROLE]["habr"]):
        try: out += [{**j, "music": False, "key": j["guid"], "source": "Хабр"} for j in boards_radar.habr(q)]
        except Exception as e: print("  ⚠ Хабр:", e)
    return out

def main():
    steps = [("api", api_jobs), ("himalayas", himalayas_jobs), ("mbw", mbw_jobs), ("habr", habr_jobs), ("browser", browser_jobs)]
    only = set(sys.argv[1:])
    alljobs = []
    for name, fn in steps:
        if only and name not in only: continue
        try: alljobs += fn()
        except Exception as e: print(f"!! {name}: {type(e).__name__} {e}", flush=True)
    seen = _seen_keys()
    mk, dedup = [], set()
    for j in alljobs:
        if not _match(j): continue
        k = ((j.get("title") or "").lower().strip(), (j.get("company") or "").lower().strip(), (j.get("location") or "").lower().strip())
        if k in dedup: continue
        dedup.add(k); mk.append(j)
    print(f"всего: {len(alljobs)} | {ROLE}: {len(mk)}", flush=True)

    from scorer import describe
    rows = []
    for j in mk:
        try: text, full = describe(j)
        except Exception: text, full = "", False
        rows.append({"company": j.get("company", ""), "title": j["title"], "location": j.get("location", ""),
                     "url": j["url"], "source": j["source"], "music": j.get("music", False),
                     "sent": j.get("key") in seen or j["url"] in seen,
                     "etype": j.get("etype", ""), "seniority": j.get("seniority", ""),
                     "description": " ".join((text or "").split())[:DESC], "full": full})
        time.sleep(0.2)
    os.makedirs("discover", exist_ok=True)
    json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"записано: {len(rows)} -> {OUT}")

if __name__ == "__main__":
    main()
