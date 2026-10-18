from flask import Flask, request, jsonify
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from collections import defaultdict
import json
import os
import re

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "orders.json")

IST = ZoneInfo("Asia/Kolkata")

RATES = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.08"),
    "INR": Decimal("0.01158"),
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

REVENUE_TERMS = {
    "revenue",
    "sales",
    "sale",
    "sold",
    "income",
    "turnover",
    "earn",
    "earned",
    "earnings",
    "generated",
    "made",
    "make",
    "makes",
    "spent",
    "spend",
    "brought in",
    "collected",
    "gross sales",
    "gross revenue",
}

REFUND_TERMS = {
    "refund",
    "refunds",
    "refunded",
    "return",
    "returns",
    "returned",
}

NET_TERMS = {
    "net revenue",
    "net sales",
    "net earnings",
    "after refunds",
    "after refund",
    "less refunds",
    "minus refunds",
}


def normalize_question(question):
    return " ".join(str(question or "").lower().split())


def load_orders():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        rows = json.load(f)

    latest = {}

    for row in rows:
        oid = row["id"]
        old = latest.get(oid)

        if old is None or row["updated_at"] > old["updated_at"]:
            latest[oid] = row

    return list(latest.values())


ORDERS = load_orders()

PRODUCTS = sorted(
    {o["product"] for o in ORDERS},
    key=len,
    reverse=True,
)

REGIONS = sorted({o["region"] for o in ORDERS})


def order_ist_datetime(order):
    value = order["created_at"].replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(IST)


def order_ist_date(order):
    return order_ist_datetime(order).date()


def money_usd(order):
    amount = Decimal(str(order["amount"]))
    rate = RATES[order["currency"]]
    return amount * rate


def round_money(value):
    return float(
        value.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def contains_term(q, term):
    if " " in term:
        return term in q

    return re.search(
        rf"\b{re.escape(term)}\b",
        q,
    ) is not None


def has_any_term(q, terms):
    return any(contains_term(q, term) for term in terms)


def parse_one_date(text):
    text = text.strip()

    m = re.fullmatch(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
        text,
    )

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
        except ValueError:
            return None

    m = re.fullmatch(
        r"(20\d{2})[-/](\d{1,2})",
        text,
    )

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                1,
            )
        except ValueError:
            return None

    m = re.fullmatch(
        r"([a-z]+)\s+(20\d{2})",
        text,
        re.I,
    )

    if m:
        month = MONTHS.get(m.group(1).lower())

        if month:
            return date(
                int(m.group(2)),
                month,
                1,
            )

    return None


def month_bounds(year, month):
    start = date(year, month, 1)

    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    return start, end


def parse_date_range(q):
    # Exact date range:
    # from 2026-01-01 to 2026-03-31

    m = re.search(
        r"\b(?:from|between)\s+"
        r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\s+"
        r"(?:to|and|through)\s+"
        r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b",
        q,
    )

    if m:
        a = parse_one_date(m.group(1))
        b = parse_one_date(m.group(2))

        if a and b:
            return a, b + timedelta(days=1)

    # Month range:
    # March 2026 to May 2026

    m = re.search(
        r"\b(?:from|between)\s+"
        r"([a-z]+\s+20\d{2})\s+"
        r"(?:to|and|through)\s+"
        r"([a-z]+\s+20\d{2})\b",
        q,
    )

    if m:
        a = parse_one_date(m.group(1))
        b = parse_one_date(m.group(2))

        if a and b:
            _, b_end = month_bounds(
                b.year,
                b.month,
            )
            return a, b_end

    # Quarter:
    # Q1 2026

    m = re.search(
        r"\bq([1-4])\s*(20\d{2})\b",
        q,
    )

    if not m:
        m = re.search(
            r"\b(first|second|third|fourth)\s+quarter\s+(20\d{2})\b",
            q,
        )

        if m:
            quarter = {
                "first": 1,
                "second": 2,
                "third": 3,
                "fourth": 4,
            }[m.group(1)]

            year = int(m.group(2))
        else:
            quarter = None
            year = None
    else:
        quarter = int(m.group(1))
        year = int(m.group(2))

    if quarter:
        start_month = 1 + 3 * (quarter - 1)

        start, _ = month_bounds(
            year,
            start_month,
        )

        end_month = start_month + 2

        _, end = month_bounds(
            year,
            end_month,
        )

        return start, end

    # March 2026

    for name, month in MONTHS.items():
        m = re.search(
            rf"\b{name}\s+(20\d{{2}})\b",
            q,
        )

        if m:
            return month_bounds(
                int(m.group(1)),
                month,
            )

    # 2026-03

    m = re.search(
        r"\b(20\d{2})[-/](0?[1-9]|1[0-2])\b",
        q,
    )

    if m:
        return month_bounds(
            int(m.group(1)),
            int(m.group(2)),
        )

    # Month without an explicit year.

    for name, month in MONTHS.items():
        if re.search(
            rf"\b{name}\b",
            q,
        ):
            matching = [
                order_ist_date(o)
                for o in ORDERS
                if order_ist_date(o).month == month
            ]

            if matching:
                return (
                    min(matching),
                    max(matching) + timedelta(days=1),
                )

    # Whole year.

    m = re.search(
        r"\b(?:in|during|for)\s+(20\d{2})\b",
        q,
    )

    if m:
        year = int(m.group(1))

        return (
            date(year, 1, 1),
            date(year + 1, 1, 1),
        )

    return None


def detect_dimensions(q):
    result = ORDERS

    # Region.
    for region in REGIONS:
        if re.search(
            rf"\b{re.escape(region.lower())}\b",
            q,
        ):
            result = [
                o
                for o in result
                if o["region"].lower() == region.lower()
            ]
            break

    # Customer ID.
    m = re.search(
        r"\bC\d{4}\b",
        q,
        re.I,
    )

    if m:
        customer = m.group(0).upper()

        result = [
            o
            for o in result
            if o["customer"].upper() == customer
        ]

    # Order ID.
    m = re.search(
        r"\bORD-\d+\b",
        q,
        re.I,
    )

    if m:
        oid = m.group(0).upper()

        result = [
            o
            for o in result
            if o["id"].upper() == oid
        ]

    # Product.
    for product in PRODUCTS:
        if product.lower() in q:
            result = [
                o
                for o in result
                if o["product"].lower() == product.lower()
            ]
            break

    # Date.
    date_range = parse_date_range(q)

    if date_range:
        start, end = date_range

        result = [
            o
            for o in result
            if start <= order_ist_date(o) < end
        ]

    return result


def status_kind(q):
    if has_any_term(q, REFUND_TERMS):
        return "refunded"

    if has_any_term(
        q,
        {
            "void",
            "voided",
            "cancelled",
            "canceled",
        },
    ):
        return "void"

    if has_any_term(q, REVENUE_TERMS):
        return "paid"

    return None


def wants_count(q):
    return any(
        phrase in q
        for phrase in [
            "how many",
            "number of",
            "count of",
            "count the",
            "order count",
            "customer count",
            "product count",
        ]
    )


def wants_quantity(q):
    return any(
        phrase in q
        for phrase in [
            "quantity",
            "units",
            "unit count",
            "how many units",
            "how many were sold",
            "how many sold",
        ]
    )


def wants_average(q):
    return (
        "average" in q
        or re.search(r"\bavg\b", q) is not None
    )


def asks_high(q):
    return any(
        term in q
        for term in [
            "highest",
            "most",
            "top",
            "largest",
            "maximum",
            "max",
        ]
    )


def asks_low(q):
    return any(
        term in q
        for term in [
            "lowest",
            "least",
            "bottom",
            "smallest",
            "minimum",
            "min",
        ]
    )


def requested_group(q):
    if "product" in q:
        return "product"

    if "customer" in q or "client" in q:
        return "customer"

    if "region" in q:
        return "region"

    return None


def metric_mode(q, status):
    if wants_quantity(q):
        return "quantity"

    if (
        "order" in q
        and any(
            term in q
            for term in [
                "most",
                "highest",
                "lowest",
                "least",
            ]
        )
    ):
        return "orders"

    # "Which product had the most refunds?"
    # means refund count unless amount/value is explicitly requested.
    if (
        status == "refunded"
        and any(
            term in q
            for term in [
                "most refunds",
                "highest refunds",
                "most returned",
                "most returns",
                "refunded orders",
            ]
        )
        and not any(
            term in q
            for term in [
                "amount",
                "value",
                "money",
                "usd",
                "dollar",
            ]
        )
    ):
        return "orders"

    # "Which product sold the most?"
    # means units unless revenue is explicitly mentioned.
    if (
        status == "paid"
        and re.search(
            r"\bsold the most\b|\bmost sold\b",
            q,
        )
    ):
        if not has_any_term(
            q,
            REVENUE_TERMS - {"sold"},
        ):
            return "quantity"

    return "money"


def group_value(rows, key, mode):
    grouped = defaultdict(Decimal)

    for row in rows:
        group = row[key]

        if mode == "quantity":
            grouped[group] += Decimal(
                str(row["qty"])
            )

        elif mode == "orders":
            grouped[group] += Decimal("1")

        else:
            grouped[group] += money_usd(row)

    return grouped


def asks_list(q):
    return (
        (
            "what products" in q
            or "which products" in q
        )
        or (
            "what customers" in q
            or "which customers" in q
        )
    ) and not (
        asks_high(q)
        or asks_low(q)
    )


def is_net_query(q):
    return any(
        term in q
        for term in NET_TERMS
    )


def base_amount(rows):
    return sum(
        (
            money_usd(o)
            for o in rows
        ),
        Decimal("0"),
    )


def answer_question(question):
    q = normalize_question(question)

    if not q:
        raise ValueError(
            "question is required"
        )

    rows = detect_dimensions(q)
    status = status_kind(q)

    # Net revenue = paid revenue minus refunds.
    if is_net_query(q):
        paid = [
            o
            for o in rows
            if o["status"] == "paid"
        ]

        refunded = [
            o
            for o in rows
            if o["status"] == "refunded"
        ]

        return round_money(
            base_amount(paid)
            - base_amount(refunded)
        )

    # Status filtering.
    if status == "paid":
        rows = [
            o
            for o in rows
            if o["status"] == "paid"
        ]

    elif status == "refunded":
        rows = [
            o
            for o in rows
            if o["status"] == "refunded"
        ]

    elif status == "void":
        rows = [
            o
            for o in rows
            if o["status"] == "void"
        ]

    group = requested_group(q)

    high = asks_high(q)
    low = asks_low(q)

    # Which product/customer/region had the most/least?
    if group and (high or low):
        mode = metric_mode(
            q,
            status,
        )

        grouped = group_value(
            rows,
            group,
            mode,
        )

        if not grouped:
            return None

        chooser = (
            max
            if high
            else min
        )

        return chooser(
            grouped.items(),
            key=lambda item: item[1],
        )[0]

    # Plural listing requests.
    if asks_list(q):
        if "product" in q:
            return sorted(
                {
                    o["product"]
                    for o in rows
                }
            )

        if (
            "customer" in q
            or "client" in q
        ):
            return sorted(
                {
                    o["customer"]
                    for o in rows
                }
            )

    # Units / quantity.
    if wants_quantity(q):
        return sum(
            int(o["qty"])
            for o in rows
        )

    # Counts.
    if wants_count(q):
        if (
            "customer" in q
            and "order" not in q
        ):
            return len(
                {
                    o["customer"]
                    for o in rows
                }
            )

        if (
            "product" in q
            and "order" not in q
        ):
            return len(
                {
                    o["product"]
                    for o in rows
                }
            )

        return len(rows)

    # Average.
    if wants_average(q):
        if not rows:
            return 0

        return round_money(
            base_amount(rows)
            / Decimal(len(rows))
        )

    # Monetary totals.
    if (
        status
        in {
            "paid",
            "refunded",
            "void",
        }
        or any(
            phrase in q
            for phrase in [
                "how much",
                "total value",
                "total amount",
                "amount",
                "monetary value",
                "in usd",
                "us dollars",
            ]
        )
    ):
        return round_money(
            base_amount(rows)
        )

    # Safe fallback.
    return round_money(
        base_amount(rows)
    )


@app.get("/")
def health():
    return jsonify(
        {
            "service":
                "Acme Appliances Ledger Agent",
            "status": "ok",
        }
    )


@app.post("/answer")
def answer():
    try:
        data = (
            request.get_json(
                force=True
            )
            or {}
        )

        result = answer_question(
            data.get(
                "question",
                "",
            )
        )

        return jsonify(
            {
                "answer": result
            }
        )

    except Exception as exc:
        return jsonify(
            {
                "answer": None,
                "error": str(exc),
            }
        ), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "5000",
            )
        ),
    )
