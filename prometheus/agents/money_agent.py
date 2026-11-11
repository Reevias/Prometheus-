"""The money tracker: what was spent, by category, against budgets."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register

MONEY = None         # the app's Money


@register
class MoneyAgent(Agent):
    name = "money"
    description = ("The owner's spending (from bank and UPI emails, and what they tell you). Actions: add (amount, "
                   "what = where or what it was for), report (this month by category, against budgets), today, "
                   "excel (writes the spending sheet and shows it), scan (check email for new bank alerts now). "
                   "Budgets are set in Settings.")
    tools = {"read_files"}
    private_outputs = True          # money is personal: answers about it are written on the laptop
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        if MONEY is None:
            return StepResult("failed", "The money tracker isn't set up.")
        action = str(inputs.get("action") or "").strip().lower()
        if action == "add":
            try:
                amount = float(str(inputs.get("amount") or "").replace(",", "").replace("₹", "").strip())
            except ValueError:
                return StepResult("failed", "How much was it?")
            what = str(inputs.get("what") or inputs.get("merchant") or "something")
            MONEY.add(amount, what, said=what)
            return StepResult("done", f"Noted ₹{amount:,.0f} on {what}. " + MONEY.month_line())
        if action == "report":
            return StepResult("done", MONEY.report())
        if action == "today":
            return StepResult("done", MONEY.today_line() or "Nothing spent today that I know of.")
        if action == "excel":
            path = MONEY.write_excel(ctx.home / "Documents" / "Prometheus Money")
            ctx.store.add_output(ctx.task_id, str(path), "created")
            return StepResult("done", f"Your spending sheet is ready: {path.name}.", {"made": [str(path)]})
        if action == "scan":
            from prometheus.agents.email_agent import mailbox

            try:
                added = MONEY.scan_email(mailbox(), days=7)
            except Exception as e:
                return StepResult("failed", f"I couldn't read your email ({type(e).__name__}). Connect it in Settings.")
            return StepResult("done", f"Found {added} new bank or UPI alert(s). " + MONEY.month_line())
        return StepResult("failed", "I can: add, report, today, excel, scan.")
