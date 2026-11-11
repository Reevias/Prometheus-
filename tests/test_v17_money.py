import time

import pytest

from prometheus.money import Money, category, parse_alert
from prometheus.quick import understand
from tests.conftest import login

NOW = time.mktime((2026, 11, 11, 20, 0, 0, 0, 0, -1))


@pytest.mark.parametrize("subject, body, amount, kind, cat", [
    ("Alert", "Rs.450.00 has been debited from account XX1234 to VPA swiggy@icici on 11-11-26. UPI Ref 1.", 450,
     "spent", "Food"),
    ("Txn", "INR 1,299.00 spent on your HDFC Bank Credit Card XX5678 at AMAZON PAY INDIA on 2026-11-10.", 1299,
     "spent", "Shopping"),
    ("You paid ₹250 to Ravi Kumar", "₹250 sent to Ravi Kumar via UPI. Ref 4521", 250, "spent", "Transfers"),
    ("UPI", "Your a/c XX9999 is debited for Rs.60.00 on 11-11-26 and credited to UBER INDIA (UPI Ref No 1).", 60,
     "spent", "Travel"),
    ("Credit", "Rs 5,000.00 credited to your A/c XX1234 by NEFT from DAD.", 5000, "received", None),
])
def test_bank_alerts(subject, body, amount, kind, cat):
    got = parse_alert(subject, body)
    assert got["amount"] == amount and got["kind"] == kind
    if cat:
        assert category(got["merchant"], "upi" if got["upi"] else "") == cat


@pytest.mark.parametrize("body", ["OTP for transaction of Rs 500 is 123456.", "Get up to Rs 5000 cashback today!"])
def test_not_money(body):
    assert parse_alert("Bank", body) is None


class Box:
    def __init__(self, msgs):
        self.msgs = msgs

    def search(self, text="", days=0, limit=10, **kw):
        return [m for m in self.msgs if text in m["snippet"].lower()]


def test_scan_budgets_excel_and_wrapup(make_ctx, tmp_path):
    store = make_ctx().store
    settings = type("S", (), {"money_budgets": {"Food": 500}})()
    m = Money(store, settings, clock=lambda: NOW)
    box = Box([{"id": "1", "from": "alerts@hdfcbank.net", "subject": "Alert", "date": "2026-11-11 13:00",
                "snippet": "Rs.450.00 debited from a/c XX1 to VPA swiggy@icici. UPI Ref 1"},
               {"id": "2", "from": "friend@gmail.com", "subject": "lunch", "date": "2026-11-11 13:00",
                "snippet": "I spent Rs 900 debited lol"}])                       # not from a bank: ignored
    assert m.scan_email(box) == 1 and m.scan_email(box) == 0                   # each alert once
    assert m.budget_alerts() == ["Food: ₹450 of your ₹500 budget this month (80% used)."]
    assert m.budget_alerts() == []                                              # once a month
    assert "Noted: ₹120 on chai" in m.tell("spent 120 on chai")
    assert m.budget_alerts() == ["Food: ₹570 of your ₹500 budget this month (over)."]
    assert "₹570 today" in m.today_line() and "Food" in m.report()
    path = m.write_excel(tmp_path)
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert wb["All"].max_row == 3 and wb["By month"]["A2"].value == "2026-11"


def test_quick_phrases():
    step = understand("I spent 200 on food").steps[0]
    assert step["agent"] == "money" and step["inputs"]["amount"] == "200" and step["inputs"]["what"] == "food"
    assert understand("how much did I spend today").steps[0]["inputs"]["action"] == "today"


def test_settings_budgets(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    client.post("/settings/money", data={"on": "1", "budget_0": "3000"}, headers=h)
    assert services.settings.money_budgets == {"Food": 3000.0}
