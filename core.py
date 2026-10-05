# -*- coding: utf-8 -*-
"""
Общее ядро всех радаров: фильтр «это маркетинговая вакансия для меня», отправка в Telegram,
состояние (что уже видели) и единая логика отчёта «первый запуск / --all / только новые».
"""
import json, os, re, sys, time

import requests

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT  = os.environ.get("TG_CHAT")
TIMEOUT  = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}

# что БЕРЁМ (подстрокой в ЗАГОЛОВКЕ). brand/email/acquisition убраны — не её специализация
# 2026-10-02: lifecycle убран (не её профиль); + inbound, hubspot, campaign; + русские
KW = ["market","growth","crm","demand","seo","pmm","martech",
      "paid","performance","digital","inbound marketing","head of inbound","inbound manager",
      "inbound lead","inbound growth","hubspot","campaign",
      "маркет","перформанс","лидген","интернет-маркет","продвижени"]

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
           "account strategist","curation","studio manager","creative","consultant",
           "representative","coordinator","assistant","junior","praktikant","media buyer",
           "operative","expression of interest","general application",
           # 2026-10-02: русские аналоги того же
           "аналитик","дизайн","продаж","контент","копирайт","стажер","стажёр","ассистент",
           "рекрут","бренд","smm-","таргетолог","младш",
           # украинские написания (DOU, Djinni)
           "аналітик","асистент","стажист","копірайт","молодш",
           "продуктовый маркетолог","продуктовий маркетолог",
           # колл-центры: «Inbound» у них — входящие звонки (Teleperformance, 2026-10-02)
           "call center","call centre","contact center","customer service","customer support",
           "kundenberater","kundenservice","оператор"]

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

def classify(title, location=""):
    """"" = подходит; иначе — короткая причина отказа (для отчёта «что отсеяли»)."""
    raw_t = title or ""
    t = raw_t.lower()
    # 1) не та роль — по заголовку
    for n in NEG_ROLE:
        if n in t: return f"роль: {n}"
    if re.search(r"\bpr\b", t): return "роль: pr"
    if re.search(r"\bsales\b", t): return "роль: sales"     # словом: Salesforce/wholesale не режем
    if re.search(r"\bagents?\b", t): return "роль: agent"   # словом: Agentic AI не режем
    if re.search(r"\bintern(ship)?\b", t): return "роль: intern"
    if "associate" in t and "associate director" not in t: return "роль: associate"
    # 2) это вообще маркетинг? — по заголовку
    if not any(k in t for k in KW): return "не маркетинг"
    # 3) формат/гео — по заголовку И локации вместе
    raw_blob = raw_t + " " + (location or "")
    blob = raw_blob.lower()
    for n in NEG_GEO_SUB:
        if n in blob: return f"гео: {n}"
    for w in NEG_GEO_WORD:
        if re.search(r"\b"+w+r"\b", blob): return f"гео: {w}"
    for st in US_STATE_FULL:
        if re.search(r"\b"+re.escape(st)+r"\b", blob): return f"гео: {st}"
    for tok in re.findall(r"\b[A-Z]{2}\b", raw_blob):
        if tok in US_STATE_CODE: return f"гео: {tok}"
    return ""

def is_marketing(title, location=""):
    return classify(title, location) == ""

def near_misses(jobs):
    """Вакансии с маркетинговым словом в тайтле, которые фильтр всё-таки отсеял, — с причиной.
       Пишутся в state/rejected_*.json, чтобы было видно, не режет ли фильтр лишнее."""
    out = []
    for j in jobs:
        t = (j.get("title") or "").lower()
        if not any(k in t for k in KW): continue
        why = classify(j.get("title"), j.get("location", ""))
        if why:
            out.append({"company": j.get("company", ""), "title": j.get("title"),
                        "location": j.get("location", ""), "why": why, "url": j.get("url")})
    return sorted(out, key=lambda x: (x["why"], x["company"] or ""))

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

def save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

def report(mk, seen, state_path, *, label, key="jid", suffix="", page_factory=None):
    """Общий финал любого радара.
       mk     — отфильтрованные маркетинговые вакансии этого прогона;
       seen   — словарь уже виденных (из state_path);
       label  — подпись источника в сообщениях, напр. "🌐 Браузер";
       key    — поле-идентификатор вакансии;
       page_factory — для JS-страниц: контекст-менеджер, отдающий страницу Playwright для описаний.
       Режимы: --all (все текущие), первый запуск (только сводка), обычный (только новые).
       Новые вакансии попадают в seen ТОЛЬКО после того, как Telegram их принял:
       упала отправка — в следующий прогон они придут снова, а не пропадут."""
    dump_all = "--all" in sys.argv
    first_run = not os.path.exists(state_path)
    new = [j for j in mk if j[key] not in seen]
    print(f"маркетинговых: {len(mk)} | новых: {len(new)}")

    def render(items, header):
        from scorer import score_jobs, fmt_job, rank, diag_line
        if page_factory:
            try:
                with page_factory() as page: score_jobs(items, page=page)
            except Exception as e:               # браузер для описаний не поднялся — оцениваем без него
                print("!! страница для описаний:", type(e).__name__, str(e)[:100])
                score_jobs(items)
        else:
            score_jobs(items)
        ranked = rank(items)
        # подходящие — карточкой с оценкой; остальное — одной строкой в конце, чтобы не тонуть в нём.
        # Без оценки (оценщик упал/лимит) — тоже карточкой: мы не знаем, плохая ли вакансия.
        def tail(j):
            sc = j.get("score")
            return bool(sc) and (bool(sc.get("stop")) or sc["hire"] == "red" or sc["fit"] <= 3)
        top = [j for j in ranked if not tail(j)]; rest = [j for j in ranked if tail(j)]
        parts = [header] + [fmt_job(j, suffix) for j in top]
        if rest:
            parts.append(f"— не подходят ({len(rest)}): стоп-индустрия / 🔴 ремоут / прохожу ≤ 3 —\n" + "\n".join(
                f"• {j.get('company','')}: {j['title']} {j.get('url','')}{suffix}" for j in rest))
        return "\n\n".join(parts) + diag_line()

    def mark(items):
        for j in items: seen[j[key]] = j["title"]
        save(state_path, seen)

    if dump_all:
        ok = send(render(mk, f"{label}: все текущие маркетинг-вакансии ({len(mk)}):") if mk
                  else f"{label}: маркетинговых вакансий сейчас нет.")
        mark(mk if ok else [j for j in mk if j not in new])
    elif first_run:
        send(f"{label}: радар включён, слежу за {len(mk)} вакансиями. Дальше только новые.")
        mark(mk)
    elif new:
        try:
            ok = send(render(new, f"{label}: новые вакансии ({len(new)}):"))
        except Exception as e:                   # что угодно сломалось при оценке/отправке
            print("!! отчёт не собрался:", type(e).__name__, str(e)[:200])
            ok = send(f"{label}: новые вакансии ({len(new)}), без оценок (сбой: {type(e).__name__}):\n\n"
                      + "\n\n".join(f"• {j.get('company','')}: {j['title']}\n{j.get('url','')}" for j in new))
        if ok: mark(new)
        else: print("!! Telegram не принял — новые НЕ помечены виденными, придут в следующий раз")
    else:
        print("Новых нет.")


# ---------- здоровье источников ----------
ALERT_AFTER = 3          # столько прогонов подряд источник «сломан» — шлём предупреждение (один раз)

class Health:
    """Что опросилось в этом прогоне: по каждой компании/запросу — сколько вакансий и какая ошибка.
       Пишется в state/health_*.json (коммитится ботом, видно в репо). Telegram:
       при первом запуске — полный список того, что сейчас не опрашивается;
       дальше — только когда источник ломается ALERT_AFTER прогонов подряд (и когда чинится).
       empty_is_bad=True — ноль вакансий тоже считается поломкой (браузер: страница без вакансий
       почти всегда значит, что скрапер её не понял)."""
    def __init__(self, path, label, empty_is_bad=False):
        self.path, self.label, self.empty_is_bad = path, label, empty_is_bad
        self.first = not os.path.exists(path)
        self.prev = load(path, {}).get("sources", {})
        self.cur = {}

    def mark(self, name, jobs=0, err="", note=""):
        bad = bool(err) or (self.empty_is_bad and not jobs)
        p = self.prev.get(name, {})
        today = time.strftime("%Y-%m-%d")
        self.cur[name] = {"jobs": jobs, "err": err or ("0 вакансий" if bad else ""),
                          "note": note,
                          "bad_streak": (p.get("bad_streak", 0) + 1) if bad else 0,
                          "last_ok": today if not bad else p.get("last_ok", ""),
                          "alerted": bool(bad and p.get("alerted"))}

    def bad(self):
        return {k: v for k, v in self.cur.items() if v["bad_streak"]}

    def finish(self, extra=None):
        bad = self.bad()
        if self.first:
            for v in bad.values(): v["alerted"] = True
        broke = {k: v for k, v in bad.items() if v["bad_streak"] >= ALERT_AFTER and not v["alerted"]}
        for v in broke.values(): v["alerted"] = True
        fixed = [k for k, v in self.cur.items() if not v["bad_streak"] and self.prev.get(k, {}).get("alerted")]
        save(self.path, {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                         "label": self.label, "total": len(self.cur), "broken": len(bad),
                         **(extra or {}), "sources": self.cur})
        line = lambda k, v: f"• {k} — {v['err']}" + (f" (ок был {v['last_ok']})" if v.get("last_ok") else "")
        if self.first:
            if bad:
                send(f"🩺 {self.label}: сейчас не опрашиваются {len(bad)} из {len(self.cur)}:\n"
                     + "\n".join(line(k, v) for k, v in sorted(bad.items())))
            return
        msg = []
        if broke:
            msg.append(f"🩺 {self.label}: перестали опрашиваться ({ALERT_AFTER}+ прогона подряд):\n"
                       + "\n".join(line(k, v) for k, v in sorted(broke.items())))
        if fixed:
            msg.append(f"✅ {self.label}: снова работают: " + ", ".join(sorted(fixed)))
        if msg: send("\n\n".join(msg))
