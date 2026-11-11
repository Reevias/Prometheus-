"""Money tracker: what the owner spends, from bank and UPI emails and from what they tell Prometheus.

Debit alerts ("Rs.450.00 debited from A/c XX1234 … to SWIGGY", "INR 1,200 spent on your card at AMAZON",
"₹250 sent to Ravi via UPI") are read every half hour from the connected email, sorted into categories, kept in
memory.db and written to an Excel file (Documents/Prometheus Money). Budgets per category warn at 80% and 100%.
The evening wrap-up shows today's and this month's spending. All of it stays on the laptop; no AI is used.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

SCHEMA = ("CREATE TABLE IF NOT EXISTS spending (id INTEGER PRIMARY KEY, created REAL NOT NULL, amount REAL NOT NULL, "
          "kind TEXT NOT NULL, merchant TEXT NOT NULL, category TEXT NOT NULL, source TEXT NOT NULL, "
          "ref TEXT NOT NULL UNIQUE)")
SCAN_EVERY = 1800
AMOUNT = r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"
DEBIT = re.compile(rf"{AMOUNT}[^.\n]{{0,80}}?\b(debited|spent|paid|sent|withdrawn|deducted|charged|purchase)\b|"
                   rf"\b(debited|spent|paid|sent|withdrawn|deducted|charged|purchase(?:d)?)\b[^.\n]{{0,40}}?{AMOUNT}", re.I)
CREDIT = re.compile(rf"{AMOUNT}[^.\n]{{0,60}}?\b(credited|received|refund(?:ed)?)\b|"
                    rf"\b(credited|received|refund(?:ed)?)\b[^.\n]{{0,40}}?{AMOUNT}", re.I)
MERCHANT = re.compile(r"\b(?:at|to|towards|for|merchant|info[:\s]|vpa)\s*[:\-]?\s*([A-Za-z0-9@&._' -]{2,40}?)"
                      r"(?=\s+(?:on|via|ref|upi|using|from|dated|avl|avail|bal|a/c|txn|is|was)\b|\s*\(|[.,;\n]|$)", re.I)
NOT_MONEY = re.compile(r"\b(otp|one time password|offer|cashback offer|get up to|win|loan offer|pre-approved|"
                       r"statement is ready|e-?statement)\b", re.I)
BANKISH = re.compile(r"bank|hdfc|icici|sbi|axis|kotak|paytm|phonepe|gpay|google pay|upi|card|yesbank|idfc|indusind|"
                     r"federal|canara|pnb|bob|amazon pay|alerts?@", re.I)
CATEGORIES = [
    ("Food", r"swiggy|zomato|restaurant|cafe|café|dominos|domino's|pizza|mcdonald|kfc|burger|starbucks|chai|canteen|"
             r"mess|dhaba|bakery|eatsure|food"),
    ("Groceries", r"blinkit|zepto|bigbasket|big basket|dmart|grofers|instamart|jiomart|grocery|kirana|reliance fresh|"
                  r"more retail|milk"),
    ("Travel", r"uber|ola|rapido|irctc|metro|redbus|makemytrip|goibibo|indigo|air ?india|fuel|petrol|diesel|hp ?cl|"
               r"bpcl|indian oil|fastag|parking|cab|auto|bus"),
    ("Shopping", r"amazon|flipkart|myntra|meesho|ajio|nykaa|croma|reliance digital|decathlon|shop|store|mall"),
    ("Bills and recharge", r"airtel|jio|vodafone|\bvi\b|bsnl|electricity|bescom|tata power|adani|water|gas|broadband|"
                           r"recharge|bill|act fibernet|dth|tata play|rent"),
    ("Entertainment", r"netflix|spotify|hotstar|prime video|bookmyshow|pvr|inox|youtube|steam|playstation|xbox|"
                      r"game|movie|apple\.com|google play"),
    ("Education", r"fees?|college|school|university|udemy|coursera|unacademy|byju|book|stationery|exam|tuition|"
                  r"library|xerox|print"),
    ("Health", r"pharmacy|apollo|medplus|1mg|netmeds|pharmeasy|hospital|clinic|doctor|lab|diagnostic|gym|fitness"),
]
TELL = re.compile(r"^(?:i\s+)?(?:spent|paid|gave|bought\s+.+?\s+for)\s+(?:rs\.?|₹|inr|rupees)?\s*([0-9][0-9,]*(?:\.\d+)?)"
                  r"\s*(?:rs|rupees|₹)?\s*(?:on|for|at|to)?\s*(.*)$", re.I)


def category(merchant: str, said: str = "") -> str:
    text = f"{merchant} {said}".lower()
    for name, pattern in CATEGORIES:
        if re.search(pattern, text):
            return name
    if "@" in merchant or re.search(r"\bupi\b|\bsent to\b|\bneft\b|\bimps\b", said, re.I):
        return "Transfers"
    return "Other"


def _amount(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse_alert(subject: str, body: str) -> dict | None:
    """{'amount', 'kind', 'merchant'} from a bank or UPI alert, or None if it isn't one."""
    text = " ".join(f"{subject}. {body}".split())[:3000]
    if NOT_MONEY.search(text) and not re.search(r"\bdebited\b|\bspent\b", text, re.I):
        return None
    m = DEBIT.search(text)
    kind = "spent"
    if not m:
        m = CREDIT.search(text)
        kind = "received"
        if not m:
            return None
    raw = next(g for g in m.groups() if g and re.match(r"[0-9]", g))
    amount = _amount(raw)
    if amount <= 0 or amount > 10_000_000:
        return None
    after = text[m.start():]
    merchant = ""
    for found in MERCHANT.finditer(after):
        cand = re.sub(r"^(?:vpa|upi|merchant)\s+", "", found.group(1).strip(" .-:"), flags=re.I)
        if re.match(r"^(rs\.?|inr|₹|\d)", cand, re.I) or re.fullmatch(
                r"(your|a/c|account|card|ac|you|the|xx\w*|\d+|your a/c\b.*|a/c\b.*)", cand.lower()):
            continue
        merchant = cand
        break
    if not merchant and kind == "received":
        found = re.search(r"\bfrom\s+([A-Za-z][A-Za-z .&']{1,30}?)(?=[.,;]|\s+(?:on|via|ref)\b|$)", after, re.I)
        merchant = found.group(1).strip() if found else ""
    return {"amount": amount, "kind": kind, "merchant": merchant[:40] or "Unknown", "upi": bool(
        re.search(r"\bupi\b|\bvpa\b|sent to", text, re.I))}


class Money:
    def __init__(self, store, settings=None, clock=time.time):
        self.store, self.settings, self.clock = store, settings, clock
        with store._lock:
            store._db.execute(SCHEMA)
            store._db.commit()

    def add(self, amount: float, merchant: str, kind: str = "spent", source: str = "told", ref: str = "",
            when: float | None = None, said: str = "") -> int | None:
        ref = ref or f"{source}:{self.clock()}:{amount}:{merchant}"
        cat = category(merchant, said)
        try:
            return self.store._write("INSERT INTO spending (created, amount, kind, merchant, category, source, ref) "
                                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (when or self.clock(), round(amount, 2), kind,
                                                                     merchant[:40], cat, source, ref))
        except Exception:                     # the same alert again
            return None

    def tell(self, text: str) -> str | None:
        """'spent 200 on food', 'paid 450 for the cab' → saved; None if it isn't that."""
        m = TELL.match(" ".join((text or "").split()).strip(" .!"))
        if not m:
            return None
        amount, what = _amount(m.group(1)), (m.group(2) or "").strip() or "something"
        self.add(amount, what, said=what)
        cat = category(what, what)
        return f"Noted: ₹{amount:,.0f} on {what} ({cat}). " + self.month_line()

    def scan_email(self, mailbox, days: int = 2) -> int:
        """Read new bank and UPI alerts from the mailbox. Returns how many were added."""
        added = 0
        seen_ids = set()
        for word in ("debited", "spent", "credited", "sent"):
            try:
                found = mailbox.search(text=word, days=days, limit=40)
            except Exception:
                continue
            for msg in found:
                import email.utils

                sender = email.utils.parseaddr(msg.get("from", ""))[1].lower()
                if msg["id"] in seen_ids or not BANKISH.search(sender.split("@")[-1]):   # the real sending domain
                    continue
                seen_ids.add(msg["id"])
                got = parse_alert(msg.get("subject", ""), msg.get("snippet", ""))
                if not got:
                    continue
                when = None
                try:
                    when = datetime.strptime(msg.get("date", ""), "%Y-%m-%d %H:%M").timestamp()
                except ValueError:
                    pass
                ref = f"email:{msg.get('from', '')}:{msg['id']}"
                if self.add(got["amount"], got["merchant"], got["kind"], "email", ref, when,
                            said="upi" if got.get("upi") else ""):
                    added += 1
        self.store.kv_set("money_scanned", self.clock())
        return added

    # -- what was spent ------------------------------------------------------------------
    def _since(self, start: float, kind: str = "spent") -> list[dict]:
        return [dict(r) for r in self.store._rows("SELECT * FROM spending WHERE created >= ? AND kind = ? ORDER BY "
                                                  "created", (start, kind))]

    @staticmethod
    def month_start(now: float) -> float:
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))

    @staticmethod
    def day_start(now: float) -> float:
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    def by_category(self, start: float) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in self._since(start):
            out[r["category"]] = out.get(r["category"], 0) + r["amount"]
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def month_line(self) -> str:
        total = sum(self.by_category(self.month_start(self.clock())).values())
        return f"This month so far: ₹{total:,.0f}."

    def today_line(self) -> str:
        now = self.clock()
        today = sum(r["amount"] for r in self._since(self.day_start(now)))
        cats = self.by_category(self.month_start(now))
        if not today and not cats:
            return ""
        top = ", ".join(f"{k} ₹{v:,.0f}" for k, v in list(cats.items())[:3])
        return f"₹{today:,.0f} today; ₹{sum(cats.values()):,.0f} this month" + (f" ({top})" if top else "")

    def report(self) -> str:
        now = self.clock()
        cats = self.by_category(self.month_start(now))
        if not cats:
            return "Nothing recorded this month yet."
        budgets = getattr(self.settings, "money_budgets", {}) or {}
        lines = [f"This month: ₹{sum(cats.values()):,.0f}"]
        for k, v in cats.items():
            b = budgets.get(k)
            lines.append(f"- {k}: ₹{v:,.0f}" + (f" of ₹{b:,.0f} ({round(100 * v / b)}%)" if b else ""))
        return "\n".join(lines)

    def budget_alerts(self) -> list[str]:
        """Each category's 80% and 100% warning, once a month each."""
        budgets = getattr(self.settings, "money_budgets", {}) or {}
        now = self.clock()
        month = time.strftime("%Y-%m", time.localtime(now))
        out = []
        for cat, spent in self.by_category(self.month_start(now)).items():
            limit = budgets.get(cat)
            if not limit:
                continue
            for level in (100, 80):
                key = f"money_alert:{month}:{cat}:{level}"
                if spent >= limit * level / 100:
                    if not self.store.kv_get(key):
                        self.store.kv_set(key, "1")
                        if level == 80 and self.store.kv_get(f"money_alert:{month}:{cat}:100"):
                            break
                        out.append(f"{cat}: ₹{spent:,.0f} of your ₹{limit:,.0f} budget this month "
                                   f"({'over' if level == 100 else '80% used'}).")
                    break
        return out

    def write_excel(self, folder: Path) -> Path:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        folder.mkdir(parents=True, exist_ok=True)
        year = time.localtime(self.clock()).tm_year
        path = folder / f"Spending {year}.xlsx"
        start = time.mktime((year, 1, 1, 0, 0, 0, 0, 0, -1))
        rows = [dict(r) for r in self.store._rows("SELECT * FROM spending WHERE created >= ? ORDER BY created",
                                                  (start,))]
        wb = Workbook()
        ws = wb.active
        ws.title = "All"
        ws.append(["Date", "Amount (₹)", "Spent / received", "Where", "Category", "From"])
        for r in rows:
            ws.append([time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created"])), r["amount"], r["kind"],
                       r["merchant"], r["category"], r["source"]])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
        for col, width in zip("ABCDEF", (18, 12, 14, 28, 18, 8)):
            ws.column_dimensions[col].width = width
        summary = wb.create_sheet("By month")
        months: dict[str, dict[str, float]] = {}
        for r in rows:
            if r["kind"] != "spent":
                continue
            m = time.strftime("%Y-%m", time.localtime(r["created"]))
            months.setdefault(m, {})
            months[m][r["category"]] = months[m].get(r["category"], 0) + r["amount"]
        cats = sorted({c for m in months.values() for c in m})
        summary.append(["Month"] + cats + ["Total"])
        for m in sorted(months):
            summary.append([m] + [round(months[m].get(c, 0), 2) for c in cats] + [round(sum(months[m].values()), 2)])
        for cell in summary[1]:
            cell.font = Font(bold=True)
        wb.save(path)
        return path
