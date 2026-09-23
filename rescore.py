# -*- coding: utf-8 -*-
"""Разовая переоценка вакансий, ушедших без оценки. Список jid — в rescore_ids.json."""
import csv, json
from radar import HANDLERS, send, load
import scorer

ids = json.load(open("rescore_ids.json", encoding="utf-8"))
titles = load("seen.json", {})
slugs = load("slugs.json", {})
names = {}
for r in csv.DictReader(open("radar_companies.csv", encoding="utf-8-sig")):
    names[f"{(r.get('ATS') or '').strip()}|{(r.get('Link') or '').strip()}"] = (r.get("Name") or "").strip()
by_slug = {}
for key, s in slugs.items():
    ats = key.split("|", 1)[0]
    by_slug[(ats, s)] = names.get(key) or s

want = {}
for jid in ids:
    ats, slug, jobid = jid.split(":", 2)
    want.setdefault((ats, slug), set()).add(jobid)

jobs, gone = [], []
for (ats, slug), need in want.items():
    try:
        got = {str(j.get("id")): j for j in HANDLERS[ats](slug)}
    except Exception as e:
        got = {}; print(f"!! {ats}:{slug}: {type(e).__name__}")
    for jobid in need:
        j = got.get(jobid)
        if not j:
            gone.append(f"{by_slug.get((ats, slug), slug)}: {titles.get(f'{ats}:{slug}:{jobid}', jobid)}"); continue
        jobs.append({**j, "company": by_slug.get((ats, slug), slug), "ats": ats, "slug": slug, "id": j.get("id")})

print(f"найдено {len(jobs)} из {len(ids)}, закрыто/не найдено {len(gone)}")
scorer.MAX_PER_RUN = max(scorer.MAX_PER_RUN, len(jobs))
scorer.score_jobs(jobs)
parts = [f"🔁 Переоценка вакансий из прогона 21:07 ({len(jobs)}):"] + [scorer.fmt_job(j) for j in scorer.rank(jobs)]
if gone: parts.append("Уже закрыты:\n" + "\n".join("• " + g for g in gone))
send("\n\n".join(parts) + scorer.diag_line())
