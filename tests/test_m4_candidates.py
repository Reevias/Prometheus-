"""M4.3 Candidates tab: repos waiting for the owner's yes, with licence and safety in plain words."""
from __future__ import annotations

import time
from pathlib import Path

import httpx
from conftest import login

from prometheus.config import data_dir
from prometheus.repo_of_day import RepoOfTheDay
from prometheus.repos.profile import RepoProfile, save

PAGE = "".join(
    f'<article class="Box-row"><h2 class="h3"><a href="/{r}">x</a></h2><p class="col-9 color-fg-muted my-1">{d}</p>'
    f'<span itemprop="programmingLanguage">Python</span><a href="https://github.com/{r}/stargazers"><svg></svg> 1,200</a>'
    f' {t} stars today</article>'
    for r, d, t in [("good/llm-notes", "Turn your notes into a searchable second brain", 500),
                    ("other/ui-kit", "An open source design system", 100)])


def _rod(services):
    def handler(request):
        if request.url.host == "github.com":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(200, json={"content": ""})
    return RepoOfTheDay(services, http=httpx.Client(transport=httpx.MockTransport(handler)))


def _profile(repo="owner/tool", licence="MIT", verdict="clean", findings=(), script="print('hi')"):
    """A repo profile and one script saved where M4.1 puts them (<data>/quarantine/...)."""
    folder = data_dir() / "quarantine" / (repo.replace("/", "__") + "__abc1234")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run.py").write_text(script, encoding="utf-8")
    save(RepoProfile(repo=repo, commit="a" * 40, url=f"https://github.com/{repo}", folder=str(folder), verdict=verdict,
                     findings=list(findings), licence=licence, languages={"Python": 1}, size_files=1, size_bytes=10,
                     made=time.time()))
    return folder


def _add(services, repo="owner/tool", description="Turns notes into flashcards", **kw):
    from prometheus.repo_of_day import add_candidate

    return add_candidate(services.store, repo, description=description, folder=str(_profile(repo, **kw)))


def test_the_abilities_tabs_show_candidates_and_light_it(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/v3/candidates")
    assert page.status_code == 200
    assert '<a href="/v3/candidates" data-tab="candidates" class="on" aria-current="page">Candidates</a>' in page.text
    skills = client.get("/v3/skills").text
    assert 'data-tab="candidates">Candidates</a>' in skills                      # there, but not lit


def test_licence_and_safety_are_in_plain_words(app_client):
    client, services = app_client()
    login(client)
    _add(services, "a/copyleft", licence="GPL-3.0-only", verdict="caution",
         findings=["Runs a program when it starts: setup.py"])
    _add(services, "b/fine", licence="MIT")
    _add(services, "c/nolicence", licence="unknown")
    _add(services, "d/bad", verdict="danger", findings=["Steals passwords: x.py"])
    page = client.get("/v3/candidates").text
    assert "GPL-3.0-only" in page and "same licence" in page                    # plain-words licence note
    assert "Check this first:" in page and "Runs a program when it starts: setup.py" in page
    assert "Looks clean" in page and "Removed: unsafe" in page and "No licence stated" in page
    import html as _h, re as _re
    words = _h.unescape(_re.sub(r"<[^>]+>", " ", page)).lower()                 # what is on screen, not CSS classes
    assert "caution" not in words and "danger" not in words                     # never the code words
    assert "No skill suggested yet" in page and 'class="help-q"' in page        # the ? next to the licence
    assert "Accept" in page and "Reject" in page and "Show the scripts" in page


def test_accept_and_reject_change_the_status(app_client):
    from prometheus.repo_of_day import candidates

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    one, two = _add(services, "a/one"), _add(services, "a/two")
    assert one["status"] == "new"
    assert client.post(f"/api/v3/candidates/{one['id']}/reject").status_code == 403       # needs the CSRF header
    assert client.post(f"/api/v3/candidates/{one['id']}/accept").status_code == 403
    assert {c["id"]: c["status"] for c in candidates(services.store)} == {one["id"]: "new", two["id"]: "new"}
    assert client.post(f"/api/v3/candidates/{one['id']}/accept", headers=h).status_code == 200
    assert client.post(f"/api/v3/candidates/{two['id']}/reject", headers=h).status_code == 200
    assert {c["id"]: c["status"] for c in candidates(services.store)} == {one["id"]: "accepted", two["id"]: "rejected"}
    assert client.post("/api/v3/candidates/999/accept", headers=h).status_code == 404
    page = client.get("/v3/candidates").text
    assert "a/one" in page and "a/two" not in page                                          # rejected ones are tucked away


def test_a_rejected_repo_is_never_offered_again(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    rod = _rod(services)
    rod.suggest()
    n = [r["full_name"] for r in rod.todays()["repos"]].index("good/llm-notes") + 1
    rod.add_numbers([n])
    cand = next(c for c in __import__("prometheus.repo_of_day", fromlist=["x"]).candidates(services.store))
    assert client.post(f"/api/v3/candidates/{cand['id']}/reject", headers=h).status_code == 200
    rod.suggest()                                                                           # tomorrow's list
    assert "good/llm-notes" not in [r["full_name"] for r in rod.todays()["repos"]]
    from prometheus.repo_of_day import add_candidate

    assert add_candidate(services.store, "good/llm-notes") is None                         # and can't be added again


def test_script_text_is_shown_escaped(app_client):
    client, services = app_client()
    login(client)
    _add(services, "x/evil", script="<script>alert(1)</script>\nprint('x')")
    page = client.get("/v3/candidates").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<dialog" in page


def test_add_repo_one_goes_to_candidates_not_skills(app_client):
    from prometheus.repo_of_day import candidates

    client, services = app_client()
    login(client)
    rod = _rod(services)
    rod.suggest()
    first = rod.todays()["repos"][0]["full_name"]
    said = rod.answer(1, "add repo 1")
    assert "Candidates" in said and "Abilities" in said
    assert [c["repo"] for c in candidates(services.store)] == [first]
    assert services.skills.get("repo-" + first.replace("/", "-")) is None                  # not in Skills yet
    assert first in client.get("/v3/candidates").text
