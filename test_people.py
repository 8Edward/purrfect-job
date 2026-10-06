"""Offline tests: every person gets their own feed setup from their own résumé, nobody gets the owner's.
Run: python telegram/test_people.py -> ok

Temp folder only: no Telegram, no network, real data untouched."""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import guests  # noqa: E402
import tg_collect as tc  # noqa: E402
import webapp  # noqa: E402
import cv_reader  # noqa: E402

ROBOTICS_CV = """Igor Example
Senior Robotics Engineer / Head of Software Engineering
7+ years in robotics: drones, manipulators.
Skills: Python, C++, ROS2, MoveIt, PX4, ArduPilot, MQTT, Modbus, PyTorch, computer vision, SQL.
Led a team of 6 engineers. English B2.
"""
ANALYST_CV = """Анна Пример
Аналитик данных
Middle аналитик данных: SQL, ClickHouse, Python, pandas, Power BI, A/B-тесты, когорты.
Английский B1.
"""
UNKNOWN_CV = """Pavel Example
Senior Hydrographic Surveyor
Multibeam echosounders, survey planning, QINSy, CARIS HIPS. English C1.
"""

graph = webapp.load_graph()
for rid in ("robotics-engineer", "embedded-engineer", "software-engineer", "engineering-manager"):
    assert rid in graph, rid
    for other in graph[rid]["links"]:
        assert other in graph, (rid, other)  # no link to a role that doesn't exist

tmp = Path(tempfile.mkdtemp())
saved = guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, tc.save_search
guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH = tmp, tmp / "users.json", tmp / "pool.json"


def owner_untouched(*a, **k):
    raise AssertionError("a guest's setup wrote the owner's search.yml")


tc.save_search = owner_untouched
try:
    guests.write_json(guests.POOL_PATH, {"channels": {
        "foranalysts": {"title": "Job for Analysts & Data Scientists"},
        "analytics_jobs": {"title": "BA & SA Jobs — бизнес и системные аналитики"},
        "forpython": {"title": "Job for Python"},
        "forfrontend": {"title": "Job for Frontend (JavaScript + Node.js) Developers"},
        "remoteit": {"title": "Remote IT (Inflow)"},
        "forallqa": {"title": "Job for QA"},
    }})
    pool = guests.load_pool()
    reg = {"users": {}}
    igor = guests.register(reg, SimpleNamespace(id=11, username="igor", first_name="Igor", last_name=None))
    anna = guests.register(reg, SimpleNamespace(id=12, username="anna", first_name="Анна", last_name=None))
    pavel = guests.register(reg, SimpleNamespace(id=13, username="pavel", first_name="Pavel", last_name=None))

    # a newcomer starts empty: no words, channels, sites, hh — and none of the owner's own lists
    for g in (igor, anna, pavel):
        s = g.load_search()
        assert s["keywords"]["role"] == [] and s["pool"] == [] and s["sites"] == [] and "hh" not in s, s
        assert guests.RU_TAG not in s["keywords"]["tags"], "the owner's Russian-company list leaked to a newcomer"

    # the headline title decides the role; tools (SQL, Python) alone don't make anyone a data analyst
    assert cv_reader.headline_titles(ROBOTICS_CV) == ["robotics engineer", "head of software engineering"]
    a_igor = cv_reader.analyze(ROBOTICS_CV, graph, pool)
    a_anna = cv_reader.analyze(ANALYST_CV, graph, pool)
    a_pavel = cv_reader.analyze(UNKNOWN_CV, graph, pool)
    prim = lambda a: [r["id"] for r in a["roles"] if r["primary"]]  # noqa: E731
    assert prim(a_igor) == ["robotics-engineer", "engineering-manager"], a_igor["roles"]
    assert "data-analyst" not in prim(a_igor) and "data-scientist" not in prim(a_igor)
    assert prim(a_anna) == ["data-analyst"], a_anna["roles"]
    assert prim(a_pavel) == ["own:hydrographic surveyor"], a_pavel["roles"]  # a job the dictionary doesn't know
    assert a_igor["skill_words"][:2] == ["c++", "robotics"], a_igor["skill_words"]  # his own tools first, not SQL
    assert a_igor["level"] == "Senior" and a_igor["english"] == "B2"

    # every IT profession is read as itself, not as an analyst
    for text, want in (("Ivan\nSenior Frontend Developer\nReact, TypeScript, Redux, HTML, CSS.", "frontend-developer"),
                       ("Олег\nBackend разработчик\nGo, PostgreSQL, Kafka, Docker, микросервисы.", "backend-developer"),
                       ("Leo\nFullstack Developer\nReact, Node.js, PostgreSQL.", "fullstack-developer"),
                       ("Max\nDevOps Engineer\nKubernetes, Terraform, Ansible, AWS, Linux.", "devops-engineer"),
                       ("Мария\nQA Automation Engineer\nPython, pytest, Selenium, Playwright.", "qa-automation"),
                       ("Dan\niOS Developer\nSwift, SwiftUI, Xcode.", "mobile-developer"),
                       ("Ann\nИнженер информационной безопасности\nSIEM, пентест, OWASP.", "security-engineer"),
                       ("Света\nИнженер технической поддержки\nL2 support, ITIL, Zabbix.", "it-support"),
                       ("Tim\nUnity Developer\nC#, Unity, shaders, gameplay.", "game-developer")):
        got = cv_reader.analyze(text, graph, pool)
        assert [r["id"] for r in got["roles"] if r["primary"]] == [want], (want, got["roles"])
        assert not set(got["channels"]) & {"foranalysts", "analytics_jobs"}, (want, got["channels"])

    # channels: each family gets its own, a general channel goes to everyone, no role -> nothing proposed
    assert set(a_igor["channels"]) == {"forpython", "remoteit"}, a_igor["channels"]
    assert set(a_anna["channels"]) == {"foranalysts", "analytics_jobs", "remoteit"}, a_anna["channels"]
    assert a_pavel["channels"] == ["remoteit"], a_pavel["channels"]
    assert cv_reader.suggest_channels(pool, []) == []

    # applying writes only that person's own files
    cv_reader.save(igor, "igor.pdf", ROBOTICS_CV, a_igor)
    cv_reader.save(anna, "anna.pdf", ANALYST_CV, a_anna)
    cv_reader.save(pavel, "pavel.pdf", UNKNOWN_CV, a_pavel)
    r_igor = cv_reader.apply(igor, graph, prim(a_igor), a_igor["channels"], "replace")
    r_anna = cv_reader.apply(anna, graph, prim(a_anna), a_anna["channels"], "replace")
    r_pavel = cv_reader.apply(pavel, graph, prim(a_pavel), a_pavel["channels"], "replace")
    w_igor, w_anna, w_pavel = (set(g.load_search()["keywords"]["role"]) for g in (igor, anna, pavel))
    assert "robotics engineer" in w_igor and r"c\+\+" in w_igor and "аналитик данных" not in w_igor, w_igor
    assert "аналитик данных" in w_anna and "robotics engineer" not in w_anna, w_anna
    assert "hydrographic surveyor" in w_pavel, w_pavel
    assert '"robotics engineer"' in r_igor["hh_query"] and '"аналитик данных"' in r_anna["hh_query"]
    assert r_pavel["hh_query"] == '"hydrographic surveyor"'
    assert set(igor.selected(pool)) == {"forpython", "remoteit"} and set(anna.selected(pool)) == {"foranalysts", "analytics_jobs", "remoteit"}
    assert igor.load_state()["cv"]["file"] == "igor.pdf" and anna.load_state()["cv"]["file"] == "anna.pdf"
    assert igor.dir != anna.dir and (igor.dir / "cv.txt").read_text(encoding="utf-8") == ROBOTICS_CV

    # a role id from the app that is neither in the dictionary nor a sane own title is refused
    try:
        cv_reader.apply(pavel, graph, ["own:" + "x" * 60], None, "merge")
        raise AssertionError("an over-long own role was accepted")
    except ValueError:
        pass

    # a résumé analysed by the older reader is re-read once (new roles reach people who uploaded before)
    old = dict(a_igor, v=1, roles=[{"id": "data-scientist", "label": "data scientist", "score": 5, "primary": False, "why": []}])
    igor.update_state(cv={"file": "igor.pdf", "at": "2026-10-06T00:00:00+00:00", "analysis": old})
    before = igor.load_search()
    info = cv_reader.refresh(igor, graph, pool)
    assert igor.load_search() == before  # the feed is not touched, only the proposal
    assert info["file"] == "igor.pdf" and info["analysis"]["v"] == cv_reader.ANALYSIS_VERSION
    assert [r["id"] for r in info["analysis"]["roles"] if r["primary"]] == ["robotics-engineer", "engineering-manager"]
    assert cv_reader.refresh(igor, graph, pool) == igor.load_state()["cv"]  # nothing to do the second time
    json.dumps(info, ensure_ascii=False)
finally:
    guests.USERS_DIR, guests.REGISTRY_PATH, guests.POOL_PATH, tc.save_search = saved

print("ok")
