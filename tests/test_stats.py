from fastapi.testclient import TestClient

import api
from fakes import GOOD, FakeProvider
from stats import markdown_table

client = TestClient(api.app)


def run(monkeypatch, replies, **provider_attrs):
    provider = FakeProvider(replies)
    for k, v in provider_attrs.items():
        setattr(provider, k, v)
    monkeypatch.setattr(api, "provider_for", lambda name=None: provider)
    return client.post("/extract", files={"file": ("inv.txt", b"Invoice", "text/plain")})


def test_empty_report(database):
    assert client.get("/stats/accuracy").json() == {"groups": []}


def test_unreviewed_runs_are_counted_but_not_scored(monkeypatch, database):
    run(monkeypatch, [GOOD])
    g = client.get("/stats/accuracy").json()["groups"][0]
    assert g["extractions"] == 1 and g["reviewed"] == 0 and g["scored"] == 0
    assert g["fully_correct_pct"] is None and g["field_accuracy_pct"] == {}


def test_approved_and_corrected_runs_give_field_accuracy(monkeypatch, database):
    for i in range(1, 5):  # four different invoices so none is flagged as a duplicate
        run(monkeypatch, [{**GOOD, "invoice_number": f"A-{i}"}])
    client.post("/extractions/1/approve")
    client.post("/extractions/2/approve")
    client.post("/extractions/3/approve")
    wrong_vendor = {**GOOD, "invoice_number": "A-4", "vendor": "Acme Limited"}
    client.put("/extractions/4/correction", json=wrong_vendor)

    g = client.get("/stats/accuracy").json()["groups"][0]
    assert g["extractions"] == 4 and g["reviewed"] == 4 and g["corrected_by_reviewer"] == 1
    assert g["scored"] == 4 and g["fully_correct_pct"] == 75.0
    assert g["field_accuracy_pct"]["vendor"] == 75.0 and g["field_accuracy_pct"]["total"] == 100.0
    assert g["line_item_recall_pct"] == 100.0


def test_retries_and_failures_are_counted_per_provider_and_model(monkeypatch, database):
    run(monkeypatch, [{**GOOD, "total": 99.0}, GOOD])  # retried
    run(monkeypatch, ["no json"], name="other", model="other-model")  # failed, other group
    groups = {(g["provider"], g["model"]): g for g in client.get("/stats/accuracy").json()["groups"]}
    assert groups[("fake", "claude-haiku-5-5")]["retried"] == 1
    assert groups[("other", "other-model")]["failed_validation"] == 1


def test_hand_entered_failure_is_reviewed_but_not_scored(monkeypatch, database):
    run(monkeypatch, ["no json"])
    client.put("/extractions/1/correction", json=GOOD)
    g = client.get("/stats/accuracy").json()["groups"][0]
    assert g["reviewed"] == 1 and g["scored"] == 0 and g["failed_validation"] == 1


def test_markdown_table_renders(monkeypatch, database):
    run(monkeypatch, [GOOD])
    client.post("/extractions/1/approve")
    text = markdown_table(client.get("/stats/accuracy").json())
    assert "| fake / claude-haiku-5-5 | 1 | 0 | 0 | 1 | 0 | 100.0% |" in text
    assert "| vendor | 100.0% |" in text
