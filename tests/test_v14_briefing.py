import time

import httpx

import prometheus.agents.email_agent as email_agent
import prometheus.briefing as briefing
import prometheus.connectors.calendar_ics as cal
from prometheus.briefing import BriefingClock, build, short_version
from tests.test_v14_connections import ICS, box


def weather_api(request):
    if "geocoding" in request.url.host:
        return httpx.Response(200, json={"results": [{"name": "Delhi", "latitude": 28.6, "longitude": 77.2}]})
    return httpx.Response(200, json={"daily": {"temperature_2m_min": [21.2, 20], "temperature_2m_max": [30.6, 29],
                                               "weather_code": [61, 0], "precipitation_probability_max": [60, 0]}})


def setup(app_client, monkeypatch):
    from datetime import datetime

    client, services = app_client()
    services.settings.weather_place = "Delhi"
    services.settings.email_address = "me@gmail.com"
    services.settings.email_alert_senders = ["ravi"]
    monkeypatch.setattr(email_agent, "MAILBOX_FACTORY", box)
    today = datetime.now().strftime("%Y%m%d")
    ics = ICS.replace("20261112", today)          # the birthday is today
    monkeypatch.setenv("CALENDAR_LINKS", "https://calendar.google.com/x.ics")
    monkeypatch.setattr(cal, "fetch", lambda url, http=None: cal.parse_ics(ics))
    real = httpx.Client
    monkeypatch.setattr(briefing.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(weather_api)))
    return client, services


def test_morning_briefing_has_weather_calendar_and_important_mail(app_client, monkeypatch):
    client, services = setup(app_client, monkeypatch)
    text = build(services, "morning")
    assert "Delhi: 21–31°C, light rain (60% chance of rain)" in text
    assert "all day Mom, birthday (Home)" in text
    assert "2 unread" in text and text.index("Ravi") < text.index("Bank")        # chosen senders first
    assert "What I did today" not in text
    short = short_version(text)
    assert "Delhi" in short and "unread" in short


def test_evening_wrap_up_tells_what_was_done(app_client, monkeypatch):
    client, services = setup(app_client, monkeypatch)
    chat = services.store.create_chat("c")
    t = services.store.create_task(chat, "open spotify")
    services.store.set_task_status(t, "done")
    t2 = services.store.create_task(chat, "find the invoice")
    services.store.set_task_status(t2, "failed")
    text = build(services, "evening")
    assert "Tomorrow's weather" in text and "2 requests, 1 done, 1 didn't work out" in text
    assert "didn't work: find the invoice" in text


def test_sent_once_a_day_in_its_window_and_kept_private(app_client, monkeypatch):
    client, services = setup(app_client, monkeypatch)
    told = []
    eight = briefing._at(time.time(), "08:00")
    clock = BriefingClock(services, notify=told.append, clock=lambda: eight + 600)
    services.settings.briefing_evening_on = False
    assert clock.tick() == ["morning"] and clock.tick() == []                    # once
    msg = services.store.messages(clock.chat())[-1]
    assert msg.private and "Good morning" in msg.text and told[0].startswith("Morning briefing")
    late = BriefingClock(services, clock=lambda: eight - 3600)
    assert late.tick() == []                                                      # not before its time


def test_briefing_settings(app_client):
    from tests.conftest import login

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    r = client.post("/settings/briefing", data={"morning": "07:30", "evening": "22:00", "morning_on": "1",
                                                "place": "Pune", "senders": "bank.com, Mom"}, headers=h)
    s = services.settings
    assert r.status_code == 200 and s.briefing_morning == "07:30" and not s.briefing_evening_on
    assert s.weather_place == "Pune" and s.email_alert_senders == ["bank.com", "Mom"]
    assert client.post("/settings/briefing", data={"morning": "25:00", "evening": "22:00"}, headers=h).status_code == 400
