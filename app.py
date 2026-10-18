from flask import Flask, request, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import re
import requests

app = Flask(__name__)

API_BASE = "https://exam.sanand.workers.dev/questionData"
EMAIL = os.environ.get("EXAM_EMAIL", "24f2005128@ds.study.iitm.ac.in")
QUIZ_SIGN = os.environ.get("QUIZ_SIGN", "")

RATES = {
    "USD": 1.0,
    "EUR": 1.08,
    "INR": 0.01158,
}

IST = ZoneInfo("Asia/Kolkata")
ORDERS = None


def load_orders():
    global ORDERS

    if ORDERS is not None:
        return ORDERS

    # Export endpoint returns NDJSON.
    url = API_BASE
    params = {
        "email": EMAIL,
        "quizSign": QUIZ_SIGN,
        "questionId": "q-ledger-agent-server",
        "path": "/export",
    }

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()

    orders = []

    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            import json
            orders.append(json.loads(line))
        except Exception:
            continue

    # Keep only the latest version of duplicate order IDs.
    latest = {}

    for order in orders:
        oid = order["id"]

        if oid not in latest:
            latest[oid] = order
        else:
            old = latest[oid]
            if order["updated_at"] > old["updated_at"]:
                latest[oid] = order

    ORDERS = list(latest.values())
    return ORDERS


def usd(order):
    return float(order["amount"]) * RATES.get(order["currency"], 1)


def local_date(order):
    dt = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
    return dt.astimezone(IST)


def parse_date_filter(question):
    q = question.lower()

    months = {
        "january": 1, "february": 2, "march": 3,
        "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9,
        "october": 10, "november": 11, "december": 12,
    }

    for name, month in months.items():
        m = re.search(rf"\b{name}\s+(20\d{{2}})\b", q)
        if m:
            return int(m.group(1)), month

    m = re.search(r"\b(20\d{2})[-/](\d{1,2})\b", q)
    if m:
        return int(m.group(1)), int(m.group(2))

    return None


def filter_orders(orders, question):
    q = question.lower()

    result = orders[:]

    # Revenue/refund status.
    if any(x in q for x in [
        "revenue", "sales", "sold", "income"
    ]):
        result = [o for o in result if o["status"] == "paid"]

    elif any(x in q for x in [
        "refund", "refunded"
    ]):
        result = [o for o in result if o["status"] == "refunded"]

    # Region.
    regions = [
        "north", "south", "east", "west", "central"
    ]

    for region in regions:
        if re.search(rf"\b{region}\b", q):
            result = [
                o for o in result
                if o["region"].lower() == region
            ]
            break

    # Product.
    products = sorted(
        set(o["product"] for o in orders),
        key=len,
        reverse=True
    )

    for product in products:
        if product.lower() in q:
            result = [
                o for o in result
                if o["product"].lower() == product.lower()
            ]
            break

    # Customer ID.
    m = re.search(r"\bC\d{4}\b", question, re.I)
    if m:
        customer = m.group(0).upper()
        result = [
            o for o in result
            if o["customer"].upper() == customer
        ]

    # Order ID.
    m = re.search(r"\bORD-\d+\b", question, re.I)
    if m:
        oid = m.group(0).upper()
        result = [
            o for o in result
            if o["id"].upper() == oid
        ]

    # Month/year.
    date_filter = parse_date_filter(question)

    if date_filter:
        year, month = date_filter
        result = [
            o for o in result
            if local_date(o).year == year
            and local_date(o).month == month
        ]

    return result


def answer_question(question):
    orders = load_orders()
    q = question.lower().strip()

    filtered = filter_orders(orders, question)

    # Total revenue / sales.
    if any(x in q for x in [
        "revenue", "sales", "income", "total value"
    ]):
        total = sum(usd(o) for o in filtered)
        return round(total, 2)

    # Refund total.
    if any(x in q for x in [
        "refund", "refunded"
    ]):
        total = sum(usd(o) for o in filtered)
        return round(total, 2)

    # Quantity / units.
    if any(x in q for x in [
        "quantity", "units", "how many units"
    ]):
        return sum(int(o["qty"]) for o in filtered)

    # Number of orders.
    if any(x in q for x in [
        "number of orders",
        "how many orders",
        "count of orders",
        "order count"
    ]):
        return len(filtered)

    # Average order value.
    if "average" in q and (
        "order" in q or "revenue" in q
    ):
        if not filtered:
            return 0
        return round(
            sum(usd(o) for o in filtered) / len(filtered),
            2
        )

    # Product/customer listing/count fallback.
    if "customer" in q and (
        "who" in q or "which" in q
    ):
        customers = sorted(set(o["customer"] for o in filtered))
        return customers

    if "product" in q and (
        "who" in q or "which" in q
    ):
        products = sorted(set(o["product"] for o in filtered))
        return products

    return round(sum(usd(o) for o in filtered), 2)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Acme Appliances Ledger Agent",
        "status": "ok"
    })


@app.route("/answer", methods=["POST"])
def answer():
    try:
        data = request.get_json(force=True)
        question = data.get("question", "")

        if not question:
            return jsonify({"answer": None}), 400

        result = answer_question(question)

        return jsonify({"answer": result})

    except Exception as e:
        return jsonify({
            "answer": None,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
