# -*- coding: utf-8 -*-
"""
Перебор ATS по названию компании (запуск вручную: workflow probe).
Для компаний, которые радар не видит полноценно — LinkedIn-списки, страницы в текстовом режиме,
сломанные страницы — подбираем код доски из названия и сайта и стучимся в публичные API ATS.
Нашлась доска с вакансиями — кандидат в API-радар.

Короткие названия совпадают с чужими досками (Pure, Glam, Gero...), поэтому у каждой находки —
уверенность:
  board  — ATS сама называет компанию, и название совпало;
  slug   — у ATS нет имени компании, но код доски = полное название (6+ символов);
  check  — вакансии есть, но чья доска — неясно: смотреть глазами по примерам вакансий.
Выход: discover/probe_result.csv. Списки радара не трогает.
"""
import csv, re, sys, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from core import UA, is_marketing, load
from radar import HANDLERS, gh_api

OUT = "discover/probe_result.csv"
ATS = ["Greenhouse", "Lever", "Ashby", "Workable", "Recruitee", "Teamtailor",
       "Breezy", "BambooHR", "SmartRecruiters", "Pinpoint"]          # Workday/Rippling: код не угадать
SUFFIX = r"\b(inc|llc|ltd|gmbh|group|technologies|technology|tech|labs?|ai|io|app|software|studio|games?|" \
         r"corp|corporation|company|co|the|global|international|systems?|solutions|digital|platform)\b"

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def slugs(name, site):
    """Варианты кода доски: из названия (целиком, без суффиксов, через дефис) и из домена сайта."""
    base = re.sub(r"\(.*?\)", " ", (name or "").lower())
    base = base.replace("&", " and ").replace("+", " plus ")
    words = re.findall(r"[a-z0-9]+", base)
    core = [w for w in words if not re.fullmatch(SUFFIX, w)] or words
    out = ["".join(words), "-".join(words), "".join(core), "-".join(core)]
    host = urlparse(site if "//" in (site or "") else "http://" + (site or "")).netloc.lower().replace("www.", "")
    labels = [l for l in host.split(".") if l]
    if len(labels) >= 2 and "linkedin" not in host and "notion" not in host:
        out.append(labels[-3] if labels[-2] in ("co", "com") and len(labels) >= 3 else labels[-2])
    out += [c + "hq" for c in out[:1]]
    return [s for s in dict.fromkeys(out) if len(s) >= 3]

def board_name(ats, slug):
    """Как ATS сама называет компанию (если умеет). Ошибка/нет имени -> ''."""
    try:
        if ats == "Greenhouse":
            return requests.get(gh_api(slug), headers=UA, timeout=15).json().get("name", "")
        if ats == "Workable":
            return requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                                headers=UA, timeout=15).json().get("name", "")
        if ats == "Recruitee":
            offers = requests.get(f"https://{slug}.recruitee.com/api/offers/", headers=UA, timeout=15).json().get("offers", [])
            return offers[0].get("company_name", "") if offers else ""
        if ats == "Teamtailor":
            r = requests.get(f"https://{slug}.teamtailor.com/jobs.rss", headers=UA, timeout=15)
            return (ET.fromstring(r.content).findtext("channel/title") or "")
        if ats == "SmartRecruiters":
            posts = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                                 headers=UA, timeout=15).json().get("content", [])
            return ((posts[0].get("company") or {}).get("name", "")) if posts else ""
    except Exception:
        pass
    return ""

def same(a, b):
    """Название доски ≈ название компании: совпадает целиком или одно — начало другого
       и покрывает 60%+ (Miro = Miro Inc; Glam ≠ Glamour Shop; Pure ≠ Pure Storage)."""
    a, b = _norm(re.sub(SUFFIX, "", (a or "").lower())), _norm(re.sub(SUFFIX, "", (b or "").lower()))
    if not (a and b): return False
    if a == b: return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 5 and long_.startswith(short) and len(short) / len(long_) >= 0.6

def try_one(ats, slug):
    s = ("eu:" + slug) if ats == "GreenhouseEU" else slug
    try:
        jobs = HANDLERS["Greenhouse" if ats == "GreenhouseEU" else ats](s)
    except Exception:
        return None
    jobs = [j for j in jobs if j.get("title")]
    return (ats.replace("EU", ""), s, jobs) if jobs else None

def probe(company):
    name, site = company["Name"], company.get("Site", "")
    tries = [(a, s) for s in slugs(name, site) for a in ATS + ["GreenhouseEU"]]
    with ThreadPoolExecutor(12) as ex:
        hits = [h for h in ex.map(lambda t: try_one(*t), tries) if h]
    best = []
    for ats, slug, jobs in hits:
        bn = board_name(ats, slug)
        conf = "board" if bn and same(bn, name) else \
               "slug" if not bn and len(_norm(slug.removeprefix("eu:"))) >= 6 and _norm(slug.removeprefix("eu:")) == _norm(name) else "check"
        if bn and not same(bn, name): conf = "чужая"           # ATS назвала другую компанию
        best.append({**company, "ATS": ats, "Slug": slug, "Board name": bn, "Confidence": conf,
                     "Jobs": len(jobs), "Marketing": sum(is_marketing(j["title"], j.get("location", "")) for j in jobs),
                     "Sample": " | ".join(f'{j["title"]} ({j.get("location","")})' for j in jobs[:4])[:400]})
    order = {"board": 0, "slug": 1, "check": 2, "чужая": 3}
    best.sort(key=lambda r: (order[r["Confidence"]], -r["Jobs"]))
    return best

def targets():
    """Кого перебираем: LinkedIn-списки + страницы в текстовом режиме + сломанные (по отчётам здоровья)."""
    out = {}
    for f, radar in [("lists/linkedin_watchlist.csv", "основной"), ("lists/music_linkedin_watchlist.csv", "музтех")]:
        for r in csv.DictReader(open(f, encoding="utf-8-sig")):
            out.setdefault(r["Name"].strip(), {"Name": r["Name"].strip(), "Site": r.get("Site", ""), "From": f"LinkedIn ({radar})"})
    for lst, health, radar in [("lists/browser_companies.csv", "state/health_browser.json", "основной"),
                               ("lists/music_browser_companies.csv", "state/health_music_browser.json", "музтех")]:
        links = {r["Name"].strip(): r["Link"] for r in csv.DictReader(open(lst, encoding="utf-8-sig"))}
        for k, v in load(health, {}).get("sources", {}).items():
            n = k.removeprefix("↪ ")
            why = "сломан" if v.get("bad_streak") else "текст" if (v.get("note") or "").startswith("текст") else ""
            if why and n in links: out.setdefault(n, {"Name": n, "Site": links[n], "From": f"браузер, {why} ({radar})"})
    return list(out.values())

def main():
    todo = targets()
    only = {a.lower() for a in sys.argv[1:]}
    if only: todo = [c for c in todo if c["Name"].lower() in only]
    print(f"=== перебор ATS: {len(todo)} компаний ===", flush=True)
    rows = []
    for i, c in enumerate(todo, 1):
        found = probe(c)
        rows += found or [{**c, "Confidence": "нет"}]
        top = found[0] if found else None
        print(f"[{i}/{len(todo)}] {c['Name']}: " + (f"{top['Confidence']} {top['ATS']}:{top['Slug']} jobs={top['Jobs']} mk={top['Marketing']}"
                                                  if top else "—"), flush=True)
    cols = ["Name", "From", "Site", "ATS", "Slug", "Board name", "Confidence", "Jobs", "Marketing", "Sample"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    from collections import Counter
    print("\nИтого (лучшее на компанию):", dict(Counter(
        next((r["Confidence"] for r in rows if r["Name"] == c["Name"]), "нет") for c in todo)))

if __name__ == "__main__":
    main()
