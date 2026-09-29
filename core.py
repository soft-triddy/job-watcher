# -*- coding: utf-8 -*-
"""
Общее ядро всех радаров: фильтр «это маркетинговая вакансия для меня», отправка в Telegram,
состояние (что уже видели) и единая логика отчёта «первый запуск / --all / только новые».
"""
import json, os, re, sys

import requests

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT  = os.environ.get("TG_CHAT")
TIMEOUT  = 20
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}

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
       Режимы: --all (все текущие), первый запуск (только сводка), обычный (только новые)."""
    dump_all = "--all" in sys.argv
    first_run = not os.path.exists(state_path)
    new = [j for j in mk if j[key] not in seen]
    for j in mk: seen[j[key]] = j["title"]
    save(state_path, seen)
    print(f"маркетинговых: {len(mk)} | новых: {len(new)}")

    def render(items, header):
        from scorer import score_jobs, fmt_job, rank, diag_line
        if page_factory:
            with page_factory() as page: score_jobs(items, page=page)
        else:
            score_jobs(items)
        return "\n\n".join([header] + [fmt_job(j, suffix) for j in rank(items)]) + diag_line()

    if dump_all:
        send(render(mk, f"{label}: все текущие маркетинг-вакансии ({len(mk)}):") if mk
             else f"{label}: маркетинговых вакансий сейчас нет.")
    elif first_run:
        send(f"{label}: радар включён, слежу за {len(mk)} вакансиями. Дальше только новые.")
    elif new:
        send(render(new, f"{label}: новые вакансии ({len(new)}):"))
    else:
        print("Новых нет.")
