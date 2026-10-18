from flask import Flask, request, jsonify
from datetime import datetime, date
from zoneinfo import ZoneInfo
import json
import os
import re
from collections import defaultdict

app = Flask(__name__)

RATES = {"USD": 1.0, "EUR": 1.08, "INR": 0.01158}
IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "orders.json")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def load_orders():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        rows = json.load(f)

    # Safety: keep latest row if duplicate order IDs exist.
    latest = {}
    for row in rows:
        oid = row["id"]
        old = latest.get(oid)
        if old is None or row["updated_at"] > old["updated_at"]:
            latest[oid] = row
    return list(latest.values())


ORDERS = load_orders()


def ist_dt(order):
    s = order["created_at"].replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(IST)


def usd_amount(order):
    return float(order["amount"]) * RATES[order["currency"]]


def parse_time_filter(q):
    # YYYY-MM
    m = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])\b", q)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Month YYYY
    for name, month in MONTHS.items():
        m = re.search(rf"\b{name}\s+(20\d{{2}})\b", q)
        if m:
            return int(m.group(1)), month

    return None


def detect_filters(q):
    result = ORDERS

    # Revenue = paid only. Refunds = refunded only. Void is neither.
    is_refund = any(x in q for x in ["refund", "refunded", "refunded amount", "refunds"])
    is_revenue = any(x in q for x in ["revenue", "sales", "sold", "income", "turnover", "spend", "spent"])

    if is_refund:
        result = [o for o in result if o["status"] == "refunded"]
    elif is_revenue:
        result = [o for o in result if o["status"] == "paid"]

    # Region.
    for region in {o["region"] for o in ORDERS}:
        if re.search(rf"\b{re.escape(region.lower())}\b", q):
            result = [o for o in result if o["region"].lower() == region.lower()]
            break

    # Customer ID.
    m = re.search(r"\bC\d{4}\b", q, re.I)
    if m:
        cid = m.group(0).upper()
        result = [o for o in result if o["customer"].upper() == cid]

    # Order ID.
    m = re.search(r"\bORD-\d+\b", q, re.I)
    if m:
        oid = m.group(0).upper()
        result = [o for o in result if o["id"].upper() == oid]

    # Product name, longest first so multiword products win.
    products = sorted({o["product"] for o in ORDERS}, key=len, reverse=True)
    for product in products:
        if product.lower() in q:
            result = [o for o in result if o["product"].lower() == product.lower()]
            break

    # Month/year.
    tf = parse_time_filter(q)
    if tf:
        year, month = tf
        result = [
            o for o in result
            if ist_dt(o).year == year and ist_dt(o).month == month
        ]

    return result


def wants_count(q):
    return any(x in q for x in [
        "how many", "number of", "count", "quantity of orders", "number of refunds"
    ])


def wants_quantity(q):
    return any(x in q for x in ["quantity", "units", "unit(s)", "how many units"])


def wants_average(q):
    return "average" in q or "avg" in q


def grouped_totals(rows, key, metric):
    d = defaultdict(float)
    for o in rows:
        k = o[key]
        d[k] += metric(o)
    return d


def answer_question(question):
    q = " ".join(str(question).lower().split())
    if not q:
        raise ValueError("question is required")

    rows = detect_filters(q)
    is_refund = any(x in q for x in ["refund", "refunded"])

    # Leaderboard / "which" questions.
    asks_which = any(x in q for x in ["which ", "what product", "what customer", "who "])
    asks_high = any(x in q for x in ["highest", "most", "top", "largest", "maximum", "max"])
    asks_low = any(x in q for x in ["lowest", "least", "bottom", "smallest", "minimum", "min"])

    # Which product/customer/region had the most/least revenue/refunds.
    if asks_which and (asks_high or asks_low):
        metric = usd_amount
        if "quantity" in q or "units" in q:
            metric = lambda o: int(o["qty"])
        elif is_refund:
            metric = usd_amount

        if "product" in q:
            groups = grouped_totals(rows, "product", metric)
        elif "customer" in q or "client" in q:
            groups = grouped_totals(rows, "customer", metric)
        elif "region" in q:
            groups = grouped_totals(rows, "region", metric)
        else:
            groups = {}

        if groups:
            fn = max if asks_high else min
            winner = fn(groups.items(), key=lambda x: x[1])[0]
            return winner

    # How many products/customers/orders/refunds.
    if wants_count(q):
        if "customer" in q and "order" not in q:
            return len({o["customer"] for o in rows})
        if "product" in q and "order" not in q:
            return len({o["product"] for o in rows})
        return len(rows)

    # Quantity / units sold or refunded.
    if wants_quantity(q):
        return sum(int(o["qty"]) for o in rows)

    # Average value.
    if wants_average(q):
        if not rows:
            return 0
        return round(sum(usd_amount(o) for o in rows) / len(rows), 2)

    # Sum money: revenue/refunds/spend/value/how much.
    if is_refund or is_revenue or any(x in q for x in ["how much", "total value", "total amount", "amount"]):
        return round(sum(usd_amount(o) for o in rows), 2)

    # Generic order total when the question clearly asks for an order count.
    if "order" in q and any(x in q for x in ["total", "number", "count"]):
        return len(rows)

    # Safe fallback for ledger total.
    return round(sum(usd_amount(o) for o in rows), 2)


@app.get("/")
def health():
    return jsonify({"service": "Acme Appliances Ledger Agent", "status": "ok"})


@app.post("/answer")
def answer():
    try:
        data = request.get_json(force=True) or {}
        result = answer_question(data.get("question", ""))
        return jsonify({"answer": result})
    except Exception as e:
        return jsonify({"answer": None, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
