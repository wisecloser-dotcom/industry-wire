"""Breaking-news watcher for Industry Wire.

Runs in GitHub Actions every 15 minutes. Reads public RSS headline feeds,
scores new headlines with simple keyword rules, sends a push alert through
ntfy for anything that looks breaking, and adds it to feed.json.

  python3 scripts/breaking.py detect          -> writes /tmp/alerts.json, sends alerts
  python3 scripts/breaking.py apply FILE      -> inserts alerts from FILE into feed.json

Standard library only.
"""
import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "scripts", ".state.json")
FEED = os.path.join(ROOT, "feed.json")
ALERTS_OUT = "/tmp/alerts.json"
SITE = "https://wisecloser-dotcom.github.io/industry-wire/"

FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNA", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6311"),
    ("CNA", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
    ("Straits Times", "https://www.straitstimes.com/news/business/rss.xml"),
    ("Business Today MY", "https://www.businesstoday.com.my/feed"),
    ("Bernama", "https://www.bernama.com/en/rssfeed.php"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Fierce Biotech", "https://www.fiercebiotech.com/rss/xml"),
    ("OilPrice", "https://oilprice.com/rss/main"),
]

MAX_AGE = timedelta(hours=2)     # older headlines are not "breaking"
THRESHOLD = 5                    # minimum score to alert
MAX_PER_RUN = 3
MAX_PER_DAY = 8

# --- scoring rules ----------------------------------------------------------
def rx(p):
    return re.compile(p, re.I)

CENTRAL_BANK = rx(r"\b(fed|federal reserve|fomc|ecb|bank of england|boe|bank of japan|boj|pboc|mas|monetary authority of singapore|bank negara|bnm)\b")
RATE_MOVE = rx(r"\b(rates?|hikes?|cuts?|raises?|lowers?|holds?|keeps?|emergency|basis points?|bps|opr)\b")
DEAL = rx(r"\b(to (buy|acquire)|agrees? to (buy|acquire)|acquires?|acquisition|merger|merge|takeover|buyout|bid for)\b")
MONEY = rx(r"(?:US)?\$\s?(\d+(?:\.\d+)?)\s?(trillion|tn|billion|bn|b)\b")
LEADER = rx(r"\b(ceo|chief executive|chairman|chairwoman|chair|founder)\b")
LEADER_MOVE = rx(r"\b(resigns?|resignation|steps? down|stepping down|ousted|fired|departs?|exits?|quits?|reinstate[sd]?|replaced|dies|died)\b")
DISTRESS = rx(r"\b(bankruptcy|chapter 11|defaults?|defaulted|insolven\w*|collapse[sd]?|bailout|bank run|receivership|liquidation)\b")
MOVE = rx(r"\b(plunges?|plummets?|crash(es|ed)?|tumbles?|soars?|surges?|jumps?|spikes?|slumps?|sinks?|rout|sell-?off|record (high|low)|all-time high|first time|tops|circuit breaker|halt(s|ed)? trading|worst (day|week)|best (day|week))\b")
MARKET = rx(r"\b(stocks?|shares|dow|s&p|nasdaq|markets?|oil|brent|crude|bitcoin|crypto|yields?|treasur(y|ies)|bonds?|dollar|ringgit|sgd|yen|yuan|sti|klci|nikkei|hang seng|gold)\b")
LEVEL = rx(r"\b(below|above|tops|breaks|past|under)\s+(us)?\$?\d")
PCT = rx(r"(\d+(?:\.\d+)?)\s?(%|per ?cent)")
POLICY = rx(r"\b(tariffs?|sanctions?|export (ban|controls?)|antitrust|sues|lawsuit|probe|investigation|fined?|ban(s|ned)?|nationali[sz]e)\b")
SHOCK = rx(r"\b(attacks?|strikes?|missiles?|drones?|blockade|war|ceasefire|explosion|fire)\b")
SUPPLY = rx(r"\b(oil|tankers?|refinery|refineries|hormuz|red sea|shipping|pipelines?|ports?|lng|gas field)\b")
FDA = rx(r"\bfda\b")
FDA_MOVE = rx(r"\b(approves?|approval|rejects?|clears?|halts?|recalls?)\b")
EXPLICIT = rx(r"\b(breaking|just in|urgent|flash)\b")
MEGACAP = rx(r"\b(nvidia|apple|microsoft|alphabet|google|amazon|meta|tesla|openai|anthropic|broadcom|tsmc|samsung|jpmorgan|goldman|morgan stanley|citi(group)?|bank of america|berkshire|blackrock|exxon|chevron|aramco|opec|boeing|airbus|eli lilly|lilly|novo nordisk|pfizer|dbs|ocbc|uob|maybank|cimb|singtel|petronas|grab|sea ltd|temasek|gic|khazanah|tenaga|walmart|netflix|disney)\b")
NOISE = rx(r"(\?|\bopinion\b|\banalysis\b|\bpodcast\b|\bvideo\b|^watch\b|\bhow to\b|\bwhat to know\b|\bexplainer\b|\bhere'?s\b|\bshould you\b|\bcolumn\b|\breview\b|\bquiz\b|\btips\b)")


def score(title):
    t = title
    s = 0
    if CENTRAL_BANK.search(t) and RATE_MOVE.search(t):
        s += 5
    if DEAL.search(t):
        s += 2
    for m in MONEY.finditer(t):
        amt = float(m.group(1))
        unit = m.group(2).lower()
        if unit in ("trillion", "tn") or amt >= 10:
            s += 3
            break
    if LEADER.search(t) and LEADER_MOVE.search(t):
        s += 3
    if DISTRESS.search(t):
        s += 5
    if MOVE.search(t) and MARKET.search(t):
        s += 3
        for m in PCT.finditer(t):
            if float(m.group(1)) >= 3:
                s += 2
                break
        if LEVEL.search(t):
            s += 2
    if POLICY.search(t):
        s += 2
    if SHOCK.search(t) and SUPPLY.search(t):
        s += 3
    if FDA.search(t) and FDA_MOVE.search(t):
        s += 2
    if EXPLICIT.search(t):
        s += 3
    if MEGACAP.search(t):
        s += 2
    if NOISE.search(t):
        s -= 4
    return s


SECTOR_RULES = [
    ("sgmy", rx(r"\b(singapore|malaysia|malaysian|ringgit|sgx|bursa|klci|sti|mas|monetary authority of singapore|bank negara|bnm|petronas|temasek|gic|khazanah|dbs|ocbc|uob|maybank|cimb|singtel|grab|kuala lumpur|johor)\b")),
    ("banking", rx(r"\b(fed|federal reserve|fomc|ecb|bank of england|bank of japan|pboc|central bank|interest rates?|rate (hike|cut)|treasur(y|ies)|bond yields?|banks?|banking|jpmorgan|goldman|morgan stanley|citi(group)?|wells fargo|hsbc|ubs)\b")),
    ("fintech", rx(r"\b(bitcoin|crypto\w*|ether(eum)?|stablecoins?|blockchain|fintech|payments?|visa|mastercard|paypal|stripe|coinbase|binance)\b")),
    ("biotech", rx(r"\b(fda|drugs?|pharma\w*|biotech\w*|vaccines?|clinical|trial|lilly|pfizer|novo nordisk|merck|moderna|astrazeneca|sanofi|gsk|roche|novartis)\b")),
    ("energy", rx(r"\b(oil|brent|crude|opec|gas|lng|refiner(y|ies)|hormuz|energy|exxon|chevron|aramco|shell|bp)\b")),
    ("autos", rx(r"\b(tesla|byd|ev|evs|electric vehicles?|automakers?|carmakers?|toyota|volkswagen|gm|general motors|ford|stellantis|nissan|honda|hyundai)\b")),
    ("aero", rx(r"\b(boeing|airbus|spacex|defen[cs]e|airlines?|aircraft|aerospace|lockheed|raytheon|rtx|northrop)\b")),
    ("media", rx(r"\b(netflix|disney|paramount|warner|comcast|nbcuniversal|streaming|hollywood|box office|spotify)\b")),
    ("retail", rx(r"\b(retail\w*|walmart|target|costco|amazon|shoppers?|consumers?|black friday|cyber monday|holiday sales)\b")),
    ("property", rx(r"\b(mortgages?|housing|home sales|real estate|property|reits?|homebuilders?)\b")),
    ("markets", rx(r"\b(stocks|stock market|wall street|dow|s&p|nasdaq|global markets|equities)\b")),
    ("tech", rx(r"\b(nvidia|apple|microsoft|alphabet|google|meta|openai|anthropic|ai|chips?|semiconductors?|tsmc|samsung|broadcom|intel|amd|software|cyber\w*|crowdstrike|zscaler|palo alto)\b")),
]


def sector_for(title):
    for key, pat in SECTOR_RULES:
        if pat.search(title):
            return key
    return "markets"


# --- feed reading -------------------------------------------------------------
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (IndustryWire headline watcher)"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read()


def text(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or "").strip():
            return x.text.strip()
    return ""


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        items.append({
            "title": text(it, "title"),
            "link": text(it, "link", "guid"),
            "date": parse_date(text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
        })
    for e in root.iter(atom + "entry"):
        link = e.find(atom + "link")
        items.append({
            "title": text(e, atom + "title"),
            "link": link.get("href", "") if link is not None else "",
            "date": parse_date(text(e, atom + "updated", atom + "published")),
        })
    return [i for i in items if i["title"] and i["link"].startswith("http")]


def clean_title(t):
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+[-|]\s+(CNBC|BBC News|CNA|The Straits Times|Bernama)$", "", t)
    return t


def words(t):
    stop = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "as", "at", "by", "with", "from", "after", "its", "is", "be", "over"}
    return {w for w in re.findall(r"[a-z0-9$]+", t.lower()) if w not in stop and len(w) > 1}


def similar(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.4


def key_of(item):
    return hashlib.sha1((item["link"] or item["title"]).encode("utf-8")).hexdigest()[:16]


# --- state ------------------------------------------------------------------------
def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f)


# --- alerts -------------------------------------------------------------------------
def notify(alert):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set; skipping phone alert")
        return
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic,
        data=alert["title"].encode("utf-8"),
        method="POST",
        headers={
            "Title": "Breaking: " + alert["source"],
            "Click": alert["link"],
            "Priority": "high",
            "Actions": "view, Open Industry Wire, " + SITE,
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        print("alert sent:", alert["title"])
    except Exception as e:  # an alert failure should not stop the site update
        print("alert failed:", e)


def detect():
    now = datetime.now(timezone.utc)
    state = load_state()
    first_run = state is None
    state = state or {"seen": {}, "alerts": []}

    fresh = []
    for source, url in FEEDS:
        try:
            items = parse_feed(fetch(url))
            print(f"ok   {source}: {len(items)} items")
        except Exception as e:
            print(f"skip {source}: {e}")
            continue
        for it in items:
            k = key_of(it)
            if k in state["seen"]:
                continue
            state["seen"][k] = now.isoformat()
            if it["date"] and now - it["date"] > MAX_AGE:
                continue
            it["title"] = clean_title(it["title"])
            it["source"] = source
            fresh.append(it)

    # forget old entries
    cutoff = now - timedelta(days=3)
    state["seen"] = {k: v for k, v in state["seen"].items() if datetime.fromisoformat(v) > cutoff}
    day_ago = now - timedelta(hours=24)
    state["alerts"] = [a for a in state["alerts"] if datetime.fromisoformat(a["ts"]) > day_ago]

    if first_run:
        print(f"first run: remembered {len(fresh)} current headlines, no alerts sent")
        save_state(state)
        json.dump([], open(ALERTS_OUT, "w"))
        return

    existing = []
    try:
        existing = [s["h"] for s in json.load(open(FEED)).get("stories", [])]
    except (OSError, ValueError):
        pass

    candidates = sorted(((score(i["title"]), i) for i in fresh), key=lambda x: -x[0])
    chosen = []
    for sc, it in candidates:
        if sc < THRESHOLD:
            break
        if len(chosen) >= MAX_PER_RUN or len(state["alerts"]) + len(chosen) >= MAX_PER_DAY:
            break
        recent = [a["title"] for a in state["alerts"]] + [c["title"] for c in chosen] + existing
        if any(similar(it["title"], r) for r in recent):
            continue
        it["sector"] = sector_for(it["title"])
        it["score"] = sc
        chosen.append(it)

    for c in chosen:
        print(f"BREAKING ({c['score']}) [{c['sector']}] {c['title']}")
        notify(c)
        state["alerts"].append({"title": c["title"], "ts": now.isoformat()})
    if not chosen:
        print(f"no breaking news ({len(fresh)} new headlines checked)")

    save_state(state)
    out = [{"title": c["title"], "link": c["link"], "source": c["source"], "sector": c["sector"],
            "date": (c["date"] or now).isoformat()} for c in chosen]
    json.dump(out, open(ALERTS_OUT, "w"))


def apply(path):
    alerts = json.load(open(path))
    if not alerts:
        return
    feed = json.load(open(FEED))
    keys = {s["key"] for s in feed.get("sectors", [])}
    stories = feed.get("stories", [])
    for a in reversed(alerts):
        sector = a["sector"] if a["sector"] in keys else "markets"
        if any(similar(a["title"], s.get("h", "")) for s in stories):
            continue
        for s in stories:
            if s.get("s") == sector and s.get("lead"):
                s["lead"] = False
        d = datetime.fromisoformat(a["date"])
        story = {
            "s": sector, "tag": "Breaking", "lead": True, "new": True, "auto": True,
            "h": a["title"], "sum": "", "why": "",
            "src": [[a["source"], a["link"], f"{d.day} {d.strftime('%b')}"]],
        }
        idx = next((i for i, s in enumerate(stories) if s.get("s") == sector), len(stories))
        stories.insert(idx, story)
    feed["stories"] = stories
    feed["edition"]["updated"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with open(FEED, "w") as f:
        json.dump(feed, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "apply":
        apply(sys.argv[2] if len(sys.argv) > 2 else ALERTS_OUT)
    else:
        detect()
