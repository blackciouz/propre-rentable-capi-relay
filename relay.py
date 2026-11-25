"""Relais Chariow -> Meta API Conversions : envoie un événement Purchase pour chaque vente payée.

Chariow n'envoie pas Purchase à Meta. Ce service lit les ventes via l'API Chariow toutes les
POLL_SECONDS et envoie chaque vente « completed » (montant > 0) une seule fois, avec
event_id = ID de vente (Meta dédoublonne si Chariow l'envoie un jour aussi).
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

CHARIOW_KEY = os.environ["CHARIOW_API_KEY"]
META_TOKEN = os.environ.get("META_CAPI_TOKEN", "")
PIXEL_ID = os.environ.get("PIXEL_ID", "1620496815890485")
STORE_URL = os.environ.get("STORE_URL", "https://ozafrnzy.mychariow.shop")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
LOOKBACK_HOURS = float(os.environ.get("LOOKBACK_HOURS", "48"))
STATE_FILE = os.environ.get("STATE_FILE", "/data/sent.json")
TEST_EVENT_CODE = os.environ.get("META_TEST_EVENT_CODE", "")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1" or not META_TOKEN
UA = "propre-rentable-capi-relay/1.0 (+https://github.com/blackciouz)"
GRAPH = "https://graph.facebook.com/v21.0"

status = {"last_poll": None, "last_error": None, "sent_total": 0}


def log(*a):
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), *a, flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def save_state(sent):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE + ".tmp", "w") as f:
            json.dump(sorted(sent), f)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except OSError as e:
        log("state save failed:", e)


def sha(v):
    return hashlib.sha256(v.strip().lower().encode()).hexdigest() if v else None


def parse_ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def chariow_sales():
    """Ventes récentes (plus récentes d'abord), en s'arrêtant au-delà de LOOKBACK_HOURS."""
    cutoff = time.time() - LOOKBACK_HOURS * 3600
    url = "https://api.chariow.com/v1/sales?per_page=50"
    out = []
    for _ in range(20):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {CHARIOW_KEY}", "Accept": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        items = d.get("data") or []
        out.extend(items)
        if not items or parse_ts(items[-1]["created_at"]) < cutoff:
            break
        url = (d.get("pagination") or {}).get("next_page_url")
        if not url:
            break
    return [s for s in out if parse_ts(s["created_at"]) >= cutoff]


def user_data(s):
    c = s.get("customer") or {}
    ud = {}
    if c.get("email"):
        ud["em"] = [sha(c["email"])]
    phone = c.get("phone") or {}
    if phone.get("number"):
        digits = re.sub(r"\D", "", str(phone["number"]))
        dial = re.sub(r"\D", "", (phone.get("country") or {}).get("dial_code") or "")
        if dial and not digits.startswith(dial):
            digits = dial + digits.lstrip("0")
        ud["ph"] = [sha(digits)]
    if c.get("first_name"):
        ud["fn"] = [sha(c["first_name"])]
    if c.get("last_name"):
        ud["ln"] = [sha(c["last_name"])]
    cc = (phone.get("country") or {}).get("code")
    if cc:
        ud["country"] = [sha(cc)]
    if c.get("id"):
        ud["external_id"] = [sha(c["id"])]
    return ud


def event_for(s):
    tr = s.get("tracking") or {}
    ts = parse_ts(s.get("completed_at") or s["created_at"])
    return {
        "event_name": "Purchase",
        "event_time": max(ts, int(time.time()) - 6 * 86400),  # Meta refuse > 7 jours
        "event_id": s["id"],
        "action_source": "website",
        "event_source_url": f"{STORE_URL}/purchase/{s['id']}",
        "user_data": user_data(s),
        "custom_data": {
            "value": tr.get("value", 0),
            "currency": tr.get("currency", "USD"),
            "content_ids": [(s.get("product") or {}).get("id", "")],
            "content_type": "product",
            "order_id": s["id"],
        },
    }


def send(events):
    body = {"data": events, "access_token": META_TOKEN}
    if TEST_EVENT_CODE:
        body["test_event_code"] = TEST_EVENT_CODE
    req = urllib.request.Request(f"{GRAPH}/{PIXEL_ID}/events", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Meta {e.code}: {e.read().decode()[:500]}")


def poll(sent):
    sales = chariow_sales()
    todo = [s for s in sales
            if s.get("status") == "completed" and (s.get("amount") or {}).get("value", 0) > 0 and s["id"] not in sent]
    if not todo:
        return 0
    events = [event_for(s) for s in todo]
    if DRY_RUN:
        for e in events:
            log("DRY_RUN", e["event_id"], e["custom_data"]["value"], e["custom_data"]["currency"], sorted(e["user_data"]))
        return 0
    res = send(events)
    log("sent", [e["event_id"] for e in events], "->", res)
    for s in todo:
        sent.add(s["id"])
    save_state(sent)
    return len(todo)


def loop():
    sent = load_state()
    log(f"relay started pixel={PIXEL_ID} dry_run={DRY_RUN} test_code={'yes' if TEST_EVENT_CODE else 'no'} known={len(sent)}")
    while True:
        try:
            status["sent_total"] += poll(sent)
            status["last_error"] = None
        except Exception as e:  # noqa: BLE001 — on journalise et on réessaie au prochain tour
            status["last_error"] = str(e)[:300]
            log("error:", e)
        status["last_poll"] = datetime.now(timezone.utc).isoformat()
        time.sleep(POLL_SECONDS)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(status).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(poll(load_state()))
        sys.exit(0)
    threading.Thread(target=loop, daemon=True).start()
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health).serve_forever()
