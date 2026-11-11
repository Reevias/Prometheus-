"""Instant answers: common commands understood on the laptop, with no AI call (about a second instead of several).

Time, date and day; opening apps and sites; timers, alarms and reminders; volume, mute, battery and the weather.
Anything else (or anything unclear) goes to the AI as before. English, Hinglish ("youtube kholo", "kitne baje hain")
and a few Hindi words are understood.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

WEATHER = None        # set by the app: callable(day_offset) -> "Delhi: 24–31°C, light rain" or ""
LAST_VOLUME_KEY = "last_volume"

FILLER = re.compile(r"^(?:(?:hey|hi|hello|ok|okay|please|can you|could you|would you|will you|kindly|jarvis|"
                    r"prometheus|just|now|tell me|let me know)[\s,]+)+", re.I)
TAIL = re.compile(r"[\s,]*(?:please|for me|right now|now|sir|bro|yaar|na|jaldi)?[\s.!?]*$", re.I)

TIME = re.compile(r"\b(what(?:'s| is)? the time|what time is it|time is it|current time|time (?:right )?now|"
                  r"tell me the time|the time please|kitne baje|kitna baja|time kya (?:hai|ho raha)|samay kya|"
                  r"टाइम|समय|कितने बजे)\b|^(?:the )?time\??$", re.I)
ENDS_OK = re.compile(r"^(?:\s*(?:now|right now|today|here|please|is it|hai|hain|ho raha hai|currently)\b)*\W*$", re.I)
DATE = re.compile(r"\b(what(?:'s| is)? (?:the |today'?s )?date|today'?s date|what (?:day|date) is (?:it|today)|"
                  r"which day is (?:it|today)|what day(?: of the week)? is it|aaj (?:kya|kaun sa|konsa) (?:din|date|"
                  r"tareekh|tarikh)|aaj ki (?:date|tareekh|tarikh)|आज.*(?:तारीख|दिन))", re.I)
OPEN = re.compile(r"^(?:open|launch|start|run|go to|show me)\s+(?:the\s+|my\s+|up\s+)?(.+?)(?:\s+(?:app|website|"
                  r"site|application))?$|^(.+?)\s+(?:kholo|khol do|chalao|chala do|open karo|open kar do)$", re.I)
NOT_APP = re.compile(r"\b(file|folder|document|pdf|photo|picture|email|mail from|chat|tab|link|repo|repository|"
                     r"settings? page|notes? about|downloads folder|my files)\b|[/\\]", re.I)
TIMER = re.compile(r"^(?:set\s+(?:a\s+|an\s+)?)?(timer|alarm)\b(?:\s+(?:for|at|in))?\s*(.*)$|"
                   r"^(.+?)\s+(?:ka\s+)?(timer|alarm)(?:\s+(?:laga do|lagao|set karo|set kar do))?$", re.I)
REMIND = re.compile(r"^remind me\s+(?:(?:to|about)\s+(.+?)\s+((?:at|in|on|tomorrow|tonight|every|after)\b.*)|"
                    r"((?:at|in|on|tomorrow|tonight|every|after)\b.+?)\s+(?:to|about)\s+(.+))$", re.I)
VOLUME = re.compile(r"\b(?:volume|sound)\s+(?:to\s+|at\s+)?(\d{1,3})\b|\b(?:volume|sound)\s+(up|down|badhao|"
                    r"kam karo|kam|zyada)\b|\bturn (up|down) the (?:volume|sound)\b|\b(louder|quieter|softer)\b|"
                    r"^(mute|unmute)(?: (?:the )?(?:sound|volume|laptop))?$|^(?:awaaz|aawaz) (badhao|kam karo)$",
                    re.I)
BATTERY = re.compile(r"\b(battery|charge)\b.*\b(level|left|percent|percentage|status|kitni|how much|kitna)\b|"
                     r"\bhow much (?:battery|charge)\b|^battery\??$|\bbattery kitni\b", re.I)
WEATHER_Q = re.compile(r"\b(weather|temperature|forecast|mausam|will it rain|is it going to rain|baarish)\b", re.I)
QUESTION = re.compile(r"^(what|whats|what's|when|where|which|who|whom|whose|why|how|is|are|am|was|were|do|does|did|"
                      r"can|could|should|would|will|shall|have|has|tell me|kya|kab|kahan|kaun|kaise|kitna|kitne)\b",
                      re.I)


READER_COMMAND = re.compile(r"^\W*(?:please\s+)?(pause|stop reading|stop|continue|resume|carry on|go on|keep reading|"
                            r"go back|back|repeat that|read (?:that )?again|skip|next|explain (?:that|this)(?: part)?"
                            r"(?: again)?|what does that mean|read faster|faster|read slower|slower|slow down)"
                            r"(?:\s+(?:please|now|reading|it|sir))*\W*$",
                            re.I)
READER_ACTION = {"pause": "pause", "stop reading": "stop", "stop": "stop", "continue": "continue", "resume": "continue",
                 "carry on": "continue", "go on": "continue", "keep reading": "continue", "go back": "back",
                 "back": "back", "repeat that": "back", "read again": "back", "read that again": "back",
                 "skip": "skip", "next": "skip", "read faster": "faster", "faster": "faster", "read slower": "slower",
                 "slower": "slower", "slow down": "slower", "what does that mean": "explain"}


@dataclass
class Quick:
    answer: str = ""                                  # said straight away (no steps)
    steps: list[dict] = field(default_factory=list)   # agent steps to run without the planner
    action: bool = False                              # it changes something (said back first, by voice)


_NUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                   "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUM.update({"thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "a": 1, "an": 1,
             "twenty five": 25, "half an": 0.5, "half a": 0.5})
SPOKEN_AMOUNT = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _NUM), key=len, reverse=True)) +
                           r")\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.I)


def spoken_numbers(text: str) -> str:
    """"ten minutes" → "10 minutes": speech-to-text often writes amounts of time as words."""
    def num(m):
        n = _NUM[m.group(1).lower()]
        return f"{n:g} {m.group(2)}"
    return SPOKEN_AMOUNT.sub(num, text)


def clean(text: str) -> str:
    t = " ".join((text or "").split())
    t = spoken_numbers(t)
    t = FILLER.sub("", t)
    t = TAIL.sub("", t)
    return t.strip(" ,.!?")


def is_question(text: str) -> bool:
    t = clean(text)
    return t.endswith("?") or (text or "").strip().endswith("?") or bool(QUESTION.match(t))


def _clock(ts: float) -> str:
    lt = time.localtime(ts)
    hour = lt.tm_hour % 12 or 12
    return f"{hour}:{lt.tm_min:02d} {'AM' if lt.tm_hour < 12 else 'PM'}"


def _day(ts: float) -> str:
    lt = time.localtime(ts)
    return f"{time.strftime('%A', lt)}, {lt.tm_mday} {time.strftime('%B %Y', lt)}"


def now_line(ts: float | None = None) -> str:
    """For the planner: the AI otherwise has no idea what day or time it is."""
    ts = time.time() if ts is None else ts
    zone = time.strftime("%Z", time.localtime(ts)) or "laptop time"
    return f"Now: {_day(ts)}, {_clock(ts)} ({zone})."


def _step(agent: str, goal: str, **inputs) -> dict:
    return {"id": 1, "agent": agent, "goal": goal, "skill": "", "inputs": inputs, "depends_on": []}


def understand(text: str, store=None, now: float | None = None) -> Quick | None:
    """An instant answer or plan for a common command, or None to let the AI handle it."""
    now = time.time() if now is None else now
    t = clean(text)
    low = t.lower()
    if not t or len(t) > 120:
        return None
    m = TIME.search(low)
    if m and ENDS_OK.match(low[m.end():]):              # "what is the time complexity…" isn't a clock question
        return Quick(answer=f"It's {_clock(now)}.")
    if DATE.search(low):
        return Quick(answer=f"Today is {_day(now)}.")
    if re.search(r"\b(on|in|from) my (phone|mobile)\b", low):
        rest = re.sub(r"\s*\b(on|in|from) my (phone|mobile)\b", "", low).strip(" ,.")
        m = re.match(r"^(?:open|launch|start)\s+(.+)$", rest)
        if m and len(m.group(1).split()) <= 4:
            return Quick(steps=[_step("phone", f"Open {m.group(1)} on the phone", action="open_app",
                                      app=m.group(1))], action=True)
        m = re.match(r"^(?:turn|switch)\s+(on|off)\s+(?:the\s+)?(wifi|wi-fi|bluetooth|dnd|do not disturb|"
                     r"mobile data|data)$|^(?:turn|switch)\s+(?:the\s+)?(wifi|wi-fi|bluetooth|dnd|do not disturb|"
                     r"mobile data|data)\s+(on|off)$", rest)
        if m:
            onoff = m.group(1) or m.group(4)
            what = (m.group(2) or m.group(3)).replace("wi-fi", "wifi")
            return Quick(steps=[_step("phone", f"Phone {what} {onoff}", action="setting", what=what, value=onoff)],
                         action=True)
        m = re.match(r"^(?:set\s+(?:an?\s+)?)?alarm\s+(?:for|at)\s+(.+)$", rest)
        if m:
            return Quick(steps=[_step("phone", "Phone alarm", action="alarm", time=m.group(1))], action=True)
        if re.search(r"\bbattery\b", rest):
            return Quick(steps=[_step("phone", "Phone battery", action="battery")])
        if re.search(r"\bnotifications?\b", rest):
            return Quick(steps=[_step("phone", "Phone notifications", action="notifications")])
        if re.search(r"\bwhat(?:'s| is)\b.*\bscreen\b|\bscreen\b", rest):
            return Quick(steps=[_step("phone", "Look at the phone", action="look", question=t)])
    m = REMIND.match(t)
    if m:
        what, when = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        if _when_ok(when, now):
            return Quick(steps=[_step("reminders", f"Remind: {what}", action="set", text=what.strip(), when=when)],
                         action=True)
    m = TIMER.match(t)
    if m:
        kind = (m.group(1) or m.group(4) or "timer").lower()
        when = m.group(2) if m.group(1) else m.group(3)
        when = (when or "").strip()
        if kind == "timer" and re.fullmatch(r"\d+(?:\.\d+)?\s*\w+", when or ""):
            when = "in " + when
        if when and _when_ok(when, now):
            return Quick(steps=[_step("reminders", f"{kind.title()} {when}", action="set", text=kind.title(),
                                      when=when)], action=True)
    m = VOLUME.search(low)
    if m:
        level, way = m.group(1), next((g for g in m.groups()[1:] if g), "")
        if way in ("mute", "unmute"):
            return Quick(steps=[_step("system", "Mute or unmute", action="mute")], action=True)
        if level is None:
            last = int(float(store.kv_get(LAST_VOLUME_KEY, 50) or 50)) if store is not None else 50
            up = way in ("up", "louder", "badhao", "zyada")
            level = max(0, min(100, last + (20 if up else -20)))
        level = max(0, min(100, int(level)))
        if store is not None:
            store.kv_set(LAST_VOLUME_KEY, level)
        return Quick(steps=[_step("system", f"Volume to {level}%", action="volume", level=level)], action=True)
    if BATTERY.search(low):
        return Quick(steps=[_step("system", "Battery level", action="battery")])
    if WEATHER_Q.search(low) and WEATHER is not None and not re.search(r"\b(in|at|for)\s+(?!the\b|today\b|"
                                                                       r"tomorrow\b|tonight\b)[a-z]", low):
        tomorrow = bool(re.search(r"\b(tomorrow|kal)\b", low))
        try:
            said = WEATHER(1 if tomorrow else 0)
        except Exception:
            said = ""
        if said:
            return Quick(answer=("Tomorrow: " if tomorrow else "Today: ") + said + ".")
    m = re.search(r"\b(?:what|which) (?:was |is )?(?:that |the )?(link|url|text|thing|number|address)s? (?:that )?i "
                  r"(?:just )?(?:copied|copy)\b(.*)|\bmy (?:last|recent) (?:copied|clipboard)\b(.*)|"
                  r"\bwhat did i (?:copy|screenshot)\b(.*)", low)
    if m:
        kind = "link" if m.group(1) in ("link", "url") else ""
        rest = next((g for g in m.groups()[1:] if g), "")
        return Quick(steps=[_step("clipboard", "What was copied", kind=kind, when=rest.strip())])
    if re.search(r"^(?:stop|end|turn off|exit|cancel|quit) (?:the )?focus(?: mode)?$|^focus (?:mode )?off$", low):
        return Quick(steps=[_step("focus", "Stop focus", action="stop")], action=True)
    m = re.match(r"^(?:start |turn on )?focus(?: mode)?(?: on)?(?:\s+(?:for|until|till)?\s*(.*))?$|"
                 r"^(?:i want to|let me|help me) focus(?:\s+(?:for|until|till)\s*(.*))?$", low)
    if m:
        rest = (m.group(1) or m.group(2) or "").strip()
        until = re.search(r"\b(?:until|till)\b", low)
        return Quick(steps=[_step("focus", "Start focus", action="start",
                                  length=("until " + rest) if until and rest else rest)], action=True)
    if re.search(r"\b(check|how is|how's)\b.*\b(laptop|computer|pc)(?:'s)? health\b|\bhealth check\b|"
                 r"\bis my (?:laptop|pc|computer) (?:ok|okay|fine|healthy)\b", low):
        return Quick(steps=[_step("health", "Laptop health check")])
    m = re.search(r"\bcheck (?:my |this )?(?:homework|answer|working|solution|maths|math|sum)s?\b(.*)", low)
    if m:
        rest = m.group(1)
        path = re.search(r"([A-Za-z]:\\[^\s]+|[\w./\\-]+\.(?:jpg|jpeg|png|webp|heic))", t)
        return Quick(steps=[_step("homework", "Check my homework", path=path.group(1) if path else "latest")])
    from prometheus.assignments import TELL as ASSIGN_TELL

    am = ASSIGN_TELL.match(t)
    if am and re.search(r"\b(assignment|homework|project|report|essay|lab|practical|worksheet|presentation|"
                        r"submission|quiz|test|record|file)\b", am.group(1), re.I):
        from prometheus.assignments import parse_due

        if parse_due(am.group(2), now) is not None:
            return Quick(steps=[_step("assignments", f"Track {am.group(1)}", action="add", title=am.group(1),
                                      due=am.group(2))], action=True)
    if re.search(r"\bwhat(?:'s| is)? due\b|\bmy (?:assignments|deadlines)\b|\bany deadlines\b|"
                 r"\b(?:assignments?|deadlines?|homework)\b.{0,20}\bdue\b", low):
        return Quick(steps=[_step("assignments", "What's due", action="list")])
    if re.search(r"\b(add|install|save|learn|get)\b.*\bthis (?:github )?(?:repo|repository|project)\b", low):
        return Quick(steps=[_step("screen", "Add the repo on screen", action="add_repo")], action=True)
    m = re.match(r"^(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+on (?:my |the )?screen)?$", low)
    if m and len(m.group(1).split()) <= 8:
        return Quick(steps=[_step("screen", f"Click {m.group(1)}", action="click", target=m.group(1))], action=True)
    if re.search(r"\bwhat(?:'s| is) on (?:my|the|this) screen\b|\blook at (?:my|the|this) screen\b|"
                 r"\bwhat (?:is|does) this\b|\bexplain this\b|\bthis error\b|\bwhat am i (?:looking|pointing) at\b|"
                 r"\b(?:on|in) (?:my|the) screen\b|\bwhat(?:'s| is) (?:this|that) (?:here|thing|button|icon|window)\b|"
                 r"\bread (?:this|what's here)\b|\bhow do i (?:fix|use) this\b", low) and (
            "screen" in low or (":" not in t and "\n" not in t and len(t.split()) <= 10)):   # "explain this: <text>" isn't
        return Quick(steps=[_step("screen", "Look at the screen", action="look", question=t)])
    from prometheus import money

    if money.TELL.match(t):
        m = money.TELL.match(t)
        what = (m.group(2) or "").strip() or "something"
        return Quick(steps=[_step("money", f"Spent {m.group(1)} on {what}", action="add", amount=m.group(1),
                                  what=what)], action=True)
    if re.search(r"\bhow much (?:did i|have i) (?:spend|spent)\b|\bmy spending\b|\bkitna kharch", low):
        today = bool(re.search(r"\btoday\b|\baaj\b", low))
        return Quick(steps=[_step("money", "Spending", action="today" if today else "report")])
    m = re.fullmatch(r"(?:quiz|test) me(?: on| about)?\s*(.*?)(?: please)?|(?:start|begin) (?:a |the )?quiz(?: on)?\s*"
                     r"(.*)|revise(?: my)? flashcards(?: on)?\s*(.*)", low)
    if m:
        deck = next((g for g in m.groups() if g), "").strip()
        return Quick(steps=[_step("study", f"Quiz {deck or 'everything due'}", action="quiz", deck=deck)])
    from prometheus.voice import reader as _reader

    if _reader.CURRENT is not None and _reader.CURRENT.active():
        m = READER_COMMAND.match(low)
        if m and len(low.split()) <= 6:
            word = m.group(1).lower()
            act = "explain" if word.startswith("explain") else READER_ACTION.get(word, "")
            if act:
                return Quick(steps=[_step("reader", f"Reading: {act}", action=act)])
    if re.fullmatch(r"(undo|undo that|undo it|undo the last (?:thing|action|step)|reverse that|take that back|"
                    r"wapas karo|undo karo)", low):
        return Quick(steps=[_step("undo", "Undo the last action", action="undo")], action=True)
    m = OPEN.match(t)
    if m:
        target = (m.group(1) or m.group(2) or "").strip(" .")
        if target and len(target.split()) <= 4 and not NOT_APP.search(target):
            return Quick(steps=[_step("system", f"Open {target}", action="app_open", target=target)], action=True)
    return None


def _when_ok(when: str, now: float) -> bool:
    from prometheus.reminders import WhenError, parse_when

    try:
        parse_when(when, now)
        return True
    except WhenError:
        return False
