# -*- coding: utf-8 -*-
"""
Фильтр разового слепка ROLE=artem (Артём): работа с существующими клиентами в B2B-софте.
AM / Customer Success / Implementation & Solutions, удалёнка из Бишкека, любые часы, всё кроме Junior.

Два прохода:
  title_reason(title, location) — по заголовку и локации, ДО загрузки описания (дёшево);
  desc_reason(text)             — по полному описанию, ПОСЛЕ загрузки.
Обе функции возвращают "" (подходит) или короткую причину отказа — отказы пишутся в отдельный файл,
чтобы было видно, не режет ли фильтр лишнее.
tags(text, location) — пометки для сортировки шорт-листа (не фильтр).
"""
import re

I = re.I

# --- 1. заголовок: берём ---
TITLE_OK = re.compile(
    r"account\s+manag|account\s+director|key\s+account|strategic\s+account|enterprise\s+account\s+manag|"
    r"customer\s+success|client\s+success|customer\s+experience\s+manager|"
    r"renewals?\s+manager|customer\s+(growth|expansion|retention)\s+manager|"
    r"implementation\s+(manager|consultant|specialist|lead|director)|"
    r"onboarding\s+(manager|specialist|lead|consultant)|customer\s+onboarding|"
    r"solutions?\s+manager|customer\s+solutions|client\s+solutions", I)

# --- 1. заголовок: выкидываем ---
TITLE_NEG = [
    ("junior",        r"\bjunior\b|\bjr\.?\b|\bintern(ship)?\b|\btrainee\b|\bgraduate\b|entry[- ]level|\bstagiaire\b"),
    ("продажи",       r"\bsales\b|account\s+executive|\bAE\b|\bSDR\b|\bBDR\b|business\s+development|inside\s+sales|"
                      r"new\s+business|\bhunter\b"),
    ("поддержка/колл-центр", r"\bsupport\b|call\s*cent|contact\s*cent|\bagent\b|representative|\bservice\s+desk|helpdesk"),
    ("не та роль",    r"technical\s+account|partner|channel|alliance|engagement\s+manager|solutions?\s+(consultant|engineer|architect)|"
                      r"accounting|accountant|recruit|talent|engineer|marcom|\baso\b|organic"),
    ("агентство/медиа", r"advertis|adtech|media|publisher|\bad\s*ops\b|programmatic|performance\s+marketing|digital\s+marketing|"
                      r"\bseo\b|\bppc\b|paid\s+(search|social)|influencer|creative"),
    ("не софт-индустрия", r"insurance|pharma|medical|clinical|health\s*care|hospital|dental|ultrasound|biolog|chemical|"
                      r"mortgage|banking|wealth|retirement|benefits|freight|logistics|fleet|automotive|dealer|"
                      r"real\s+estate|construction|energy|infrastruktur|environmental|retail\s+store|fmcg|cpg|"
                      r"\bfield\b|territory|federal|government|cell\s+culture|\bdna\b|\bhme\b"),
    ("регион/язык",   r"\b(us|usa|uk)\b|united\s+states|canada|latam|latin|china|hong\s+kong|india|mumbai|\(de\)|/pt\b|\bim\b|german|deutsch|\bdach\b|french|fran[cç]ais|italian|italy|spanish|espa[nñ]ol|portuguese|brazil|"
                      r"dutch|benelux|nordic|swedish|danish|norwegian|finnish|polish|czech|japanese|japan|korean|"
                      r"mandarin|chinese|cantonese|arabic|\bksa\b|saudi|turkish|hebrew|bilingual|\(m/w/d\)|\(w/m/d\)"),
    ("формат",        r"part[- ]time|\bhybrid\b|on[- ]?site|in[- ]office"),
]
TITLE_NEG = [(k, re.compile(v, I)) for k, v in TITLE_NEG]

# --- 2. локация: только то, что точно закрыто для удалёнки из Бишкека ---
US_STATES = (r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|hawaii|idaho|"
             r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
             r"mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|"
             r"north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|"
             r"south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming")
LOC_NEG = [
    ("гео: hybrid/офис", re.compile(r"\bhybrid\b|on[- ]?site|in[- ]office|office[- ]based", I)),
    ("гео: США", re.compile(r"\b(us|usa|united states|u\.s\.)\b|\b(" + US_STATES + r")\b", I)),
    ("гео: Канада", re.compile(r"\bcanada\b", I)),
]
LOC_OPEN = re.compile(r"worldwide|anywhere|global|\bcis\b|central\s+asia|kyrgyz|emea|apac", I)

# --- 3. описание ---
# проверка «это софт» — только для агрегаторов (Himalayas, Хабр): компании из списков радара отобраны вручную
SOFTWARE = re.compile(r"\bSaaS\b|software|platform|\bAPIs?\b|\bB2B\b|cloud|subscription|\bARR\b|integration|"
                      r"\bAI\b|tech|digital\s+product|infrastructure|fintech|payments?|\bapp\b|"
                      r"\bIT\b|ПО\b|программ|платформ|облач|интеграц|сервис|продукт", I)
AGGREGATORS = {"Himalayas", "Хабр"}
DESC_NEG = [
    ("колл-центр", r"call[- ]cent(er|re)|contact[- ]cent(er|re)|inbound\s+calls|outbound\s+calls|"
                   r"cold[- ]call|\d+\+?\s+calls\s+(a|per)\s+day|dialer"),
    ("офис/hybrid", r"hybrid\s+(role|work|position|model|schedule|setup)|\d\s+days?\s+(a|per)\s+week\s+(in|at)\s+(the|our)\s+office|"
                    r"office[- ]based|must\s+(be\s+able\s+to\s+)?commute|relocat\w+\s+(is\s+)?required"),
    ("разрешение на работу", r"(authori[sz]ed|eligib\w+|legally\s+(able|permitted)|right)\s+to\s+work\s+in\s+(the\s+)?"
                    r"(us\b|u\.s\.|united\s+states|uk\b|united\s+kingdom|canada|eu\b|european\s+union|germany|australia)|"
                    r"must\s+(be\s+)?(reside|located|based|live)\s+in\s+(the\s+)?(us\b|u\.s\.|united\s+states|uk\b|canada|"
                    r"eu\b|europe|germany|australia|"+ US_STATES + r")|"
                    r"\bus\s+(citizen|residents?)\b|security\s+clearance|w-?2\s+employee"),
]
DESC_NEG = [(k, re.compile(v, I)) for k, v in DESC_NEG]
# обязательный другой язык (не английский/русский), если рядом нет «plus / nice to have»
LANG_REQ = re.compile(r"(fluen\w*|native|business[- ]level|proficien\w*|c1|c2)\s+(in\s+)?(german|french|italian|"
                      r"spanish|portuguese|dutch|swedish|danish|norwegian|finnish|polish|czech|japanese|korean|"
                      r"mandarin|chinese|arabic|turkish|hebrew)", I)
NICE = re.compile(r"plus|bonus|advantage|nice|preferred|desirable|asset", I)
# описание целиком на другом языке (испанский, немецкий, французский, португальский, итальянский)
FOREIGN = re.compile(r" (y|que|para|con|los|las|und|die|der|mit|für|et|les|pour|avec|des|com|dos|della|per|nel) ", I)
ENGLISH = re.compile(r" (the|and|to|with|for|you|our|of) ", I)
NEW_BIZ = re.compile(r"new\s+business|net[- ]new|new\s+logos?|prospecting|outbound|pipeline\s+generation|hunter", I)
POST_SALE = re.compile(r"renewal|retention|churn|\bNRR\b|net\s+revenue\s+retention|upsell|up-sell|cross-sell|"
                       r"expansion|existing\s+(customers|clients|accounts)|book\s+of\s+business|portfolio|adoption", I)

def title_reason(title, location="", source=""):
    t = title or ""
    if not TITLE_OK.search(t): return "заголовок: не наша роль"
    for k, rx in TITLE_NEG:
        if rx.search(t): return f"заголовок: {k}"
    loc = location or ""
    # Himalayas: locationRestrictions — жёсткое требование «жить в этих странах»
    if source == "Himalayas" and not re.search(r"worldwide|kyrgyz", loc, I): return "гео: только " + loc.split(" · ")[0][:40]
    if not LOC_OPEN.search(loc):
        for k, rx in LOC_NEG:
            if rx.search(loc): return k
    return ""

def desc_reason(text, source=""):
    d = text or ""
    if len(d) < 300: return ""                     # описания нет — не судим, оставляем с пометкой
    if source in AGGREGATORS and not SOFTWARE.search(d): return "описание: не софт"
    if len(FOREIGN.findall(d)) > max(15, len(ENGLISH.findall(d))): return "описание: не на английском"
    for k, rx in DESC_NEG:
        if rx.search(d): return f"описание: {k}"
    for m in LANG_REQ.finditer(d):
        around = d[max(0, m.start() - 80): m.end() + 80]
        if not NICE.search(around): return f"описание: нужен {m.group(3).lower()}"
    nb, ps = len(NEW_BIZ.findall(d)), len(POST_SALE.findall(d))
    if nb >= 3 and nb > ps: return "описание: охота за новыми клиентами"
    return ""

def tags(text, location=""):
    d, loc = text or "", location or ""
    out = []
    if re.search(r"worldwide|anywhere|\bcis\b|central\s+asia|kyrgyz", loc, I) or \
       re.search(r"work\s+from\s+anywhere|remote[- ]first|fully\s+remote|100%\s+remote|remote\s+worldwide|"
                 r"(central\s+asia|\bcis\b|kyrgyz)", d, I):
        out.append("remote-anywhere")
    elif re.search(r"\bremote\b", loc + " " + d, I): out.append("remote, гео уточнить")
    else: out.append("удалёнка не указана")
    if re.search(r"e-?commerce|shopify|merchants?|\bD2C\b|\bDTC\b|online\s+stores?", d, I): out.append("e-commerce")
    if len(POST_SALE.findall(d)) >= 3: out.append("post-sale")
    if re.search(r"\bQBRs?\b|business\s+reviews?", d, I): out.append("QBR")
    if re.search(r"\bAPIs?\b|integrations?|technical|SQL|webhook", d, I): out.append("tech")
    if re.search(r"quota|commission|\bOTE\b|variable\s+(pay|comp)", d, I): out.append("квота")
    if re.search(r"russian|русск", d, I): out.append("RU")
    if len(d) < 300: out.append("без описания")
    return out
