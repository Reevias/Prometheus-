import json
import time

from prometheus import phone
from tests.conftest import login

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def test_installable_app(app_client):
    client, _ = app_client()
    m = client.get("/manifest.webmanifest").json()
    assert m["display"] == "standalone" and any(i["sizes"] == "512x512" for i in m["icons"])
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "showNotification" in sw.text
    login(client)
    page = client.get("/").text
    assert 'rel="manifest"' in page and "phone.js" in page and "Hold to talk" in page and 'name="file"' in page


def test_push_subscribe_and_send(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    key = client.get("/push/key").text
    assert len(key) > 80                                            # a P-256 public key for the browser
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post("/push/subscribe", content=json.dumps(sub), headers=h).status_code == 200
    assert client.post("/push/subscribe", content=json.dumps({"endpoint": "nope"}), headers=h).status_code == 400
    sent = []
    assert phone.push(services.store, "Prometheus", "Call mom", sender=lambda s, p: sent.append(p) or 201) == 1
    assert json.loads(sent[0])["body"] == "Call mom"
    phone.push(services.store, "x", "y", sender=lambda s, p: 410)       # an uninstalled app is forgotten
    assert not services.store._rows("SELECT * FROM push_subs")


def test_upload_from_the_phone(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    chat = services.store.create_chat("c")
    asked = []
    services.start_task = lambda c, t, read_only=False: asked.append(t)
    r = client.post(f"/chats/{chat}/upload", files={"file": ("receipt.jpg", PNG, "image/jpeg")}, data={"text": ""},
                    headers=h)
    assert r.status_code == 200 and "receipt" in asked[0] and "homework" in asked[0]
    saved = list((services.home / phone.UPLOADS / "Photos").glob("*receipt.jpg"))
    assert saved and saved[0].read_bytes() == PNG
    client.post(f"/chats/{chat}/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=h)
    assert "not .exe" in services.store.messages(chat)[-1].text


def test_bank_sms(app_client):
    client, services = app_client()
    services.settings.webhook_token = "hook-secret-123"
    msg = {"from": "VM-HDFCBK", "text": "Rs.250.00 debited from a/c XX1234 to VPA zomato@paytm on 11-11-26. UPI Ref 9."}
    assert client.post("/hooks/wrong/sms", json=msg).status_code == 404
    r = client.post("/hooks/hook-secret-123/sms", json=msg)
    assert "Added ₹250" in r.text
    assert "Already" in client.post("/hooks/hook-secret-123/sms", json=msg).text
    assert "Not a bank" in client.post("/hooks/hook-secret-123/sms", json={"from": "Ravi", "text": "see you at 5"}).text


def test_cloud_hands_laptop_jobs_over(app_client):
    client, services = app_client()
    services.settings.role = "cloud"
    from prometheus.orchestrator.planner import Plan
    from prometheus.sync import token

    plan = Plan([{"id": 1, "agent": "system", "goal": "open", "inputs": {"action": "app_open", "target": "spotify"},
                  "depends_on": []}])
    chat = services.store.create_chat("From your phone")
    out = services.orchestrator.before_steps(chat, "open spotify on my laptop", plan)
    assert "laptop" in out
    key = token()
    jobs = client.post("/api/laptop-jobs", headers={"Authorization": f"Bearer {key}"}).json()["jobs"]
    assert jobs[0]["text"] == "open spotify on my laptop" and jobs[0]["chat_title"] == "From your phone"
    assert client.post("/api/laptop-jobs", headers={"Authorization": f"Bearer {key}"}).json()["jobs"] == []
    research = Plan([{"id": 1, "agent": "research", "goal": "r", "inputs": {}, "depends_on": []}])
    assert services.orchestrator.before_steps(chat, "research solar", research) == ""      # the cloud does that
    services.settings.role = "laptop"
