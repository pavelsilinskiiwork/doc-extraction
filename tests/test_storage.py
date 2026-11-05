import pytest
from fastapi.testclient import TestClient

import api
from fakes import GOOD, FakeProvider

client = TestClient(api.app)  # no `with`: lifespan does not run, tests connect the database themselves


def use(monkeypatch, provider):
    monkeypatch.setattr(api, "provider_for", lambda name=None: provider)


def upload(name="inv.txt", text=b"Invoice A-1"):
    return client.post("/extract", files={"file": (name, text, "text/plain")})


def test_without_a_database_extract_still_works_and_review_endpoints_say_503(monkeypatch):
    use(monkeypatch, FakeProvider([GOOD]))
    r = upload()
    assert r.status_code == 200 and r.json()["id"] is None
    assert client.get("/extractions").status_code == 503
    assert client.get("/health").json() == {"status": "ok", "storage": False}


def test_extraction_is_saved_with_lines_and_cost(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    body = upload().json()
    assert body["id"] == 1 and body["review_status"] == "ok" and body["review_reasons"] == []

    detail = client.get("/extractions/1").json()
    assert detail["vendor"] == "Acme Ltd" and detail["currency"] == "USD" and detail["total"] == "22.68"
    assert detail["document_text"] == "Invoice A-1" and detail["filename"] == "inv.txt"
    assert detail["invoice"]["total"] == "22.68" and detail["corrected_invoice"] is None
    assert detail["line_items"] == [{"position": 1, "description": "Widget", "quantity": "2.0000",
                                     "unit_price": "10.5000", "amount": "21.00"}]
    assert float(detail["cost_usd"]) > 0


def test_retry_puts_the_run_in_the_review_queue(monkeypatch, database):
    use(monkeypatch, FakeProvider([{**GOOD, "total": 99.0}, GOOD]))
    body = upload().json()
    assert body["review_status"] == "needs_review"
    queue = client.get("/extractions", params={"review_status": "needs_review"}).json()
    assert queue["total"] == 1 and queue["items"][0]["review_reasons"]


def test_failed_extraction_is_stored_and_its_id_returned(monkeypatch, database):
    use(monkeypatch, FakeProvider(["no json here"]))
    r = upload()
    assert r.status_code == 422 and r.json()["detail"]["extraction_id"] == 1
    row = client.get("/extractions/1").json()
    assert row["status"] == "failed" and row["review_status"] == "needs_review"
    assert row["invoice"] is None and row["validation_errors"]
    assert client.get("/extractions", params={"status": "failed"}).json()["total"] == 1


def test_same_invoice_twice_is_flagged_as_duplicate(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    upload()
    second = upload().json()
    assert second["review_status"] == "needs_review"
    assert any("#1" in reason for reason in second["review_reasons"])


def test_approve_then_status_and_timestamp_change(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    upload()
    r = client.post("/extractions/1/approve")
    assert r.status_code == 200 and r.json()["review_status"] == "approved" and r.json()["reviewed_at"]


def test_correction_replaces_final_values_but_keeps_the_model_output(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    upload()
    fixed = {**GOOD, "vendor": "Acme Limited", "currency": "USD"}
    r = client.put("/extractions/1/correction", json=fixed)
    detail = r.json()
    assert r.status_code == 200 and detail["review_status"] == "corrected"
    assert detail["vendor"] == "Acme Limited"  # flattened column follows the correction
    assert detail["invoice"]["vendor"] == "Acme Ltd"  # the model's answer is untouched
    assert detail["corrected_invoice"]["vendor"] == "Acme Limited"
    assert len(detail["line_items"]) == 1


def test_correction_must_pass_the_same_validation(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    upload()
    r = client.put("/extractions/1/correction", json={**GOOD, "total": 1.0})
    assert r.status_code == 422
    assert client.get("/extractions/1").json()["review_status"] == "ok"


def test_failed_extraction_can_be_filled_in_by_hand(monkeypatch, database):
    use(monkeypatch, FakeProvider(["no json here"]))
    upload()
    assert client.post("/extractions/1/approve").status_code == 409  # nothing to approve
    r = client.put("/extractions/1/correction", json=GOOD)
    assert r.status_code == 200 and r.json()["vendor"] == "Acme Ltd" and r.json()["invoice"] is None


def test_reject_and_not_found(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    upload()
    assert client.post("/extractions/1/reject").json()["review_status"] == "rejected"
    assert client.get("/extractions/99").status_code == 404
    assert client.post("/extractions/99/approve").status_code == 404


def test_paging(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    for _ in range(3):
        upload()
    page = client.get("/extractions", params={"limit": 2}).json()
    assert page["total"] == 3 and [i["id"] for i in page["items"]] == [3, 2]
    assert client.get("/extractions", params={"limit": 0}).status_code == 422


def test_storage_failure_does_not_lose_the_extraction(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))

    def broken(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(api.db, "save_extraction", broken)
    r = upload()
    assert r.status_code == 200 and r.json()["id"] is None and r.json()["invoice"]["total"] == "22.68"


def test_api_key_protects_everything_but_health(monkeypatch, database):
    use(monkeypatch, FakeProvider([GOOD]))
    monkeypatch.setenv("API_KEY", "s3cret")
    assert client.get("/health").status_code == 200
    assert upload().status_code == 401
    assert client.get("/extractions").status_code == 401
    assert client.get("/extractions", headers={"X-API-Key": "wrong"}).status_code == 401
    ok = client.post("/extract", files={"file": ("inv.txt", b"x", "text/plain")}, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200
    assert client.get("/extractions", headers={"X-API-Key": "s3cret"}).status_code == 200


@pytest.mark.parametrize("raw,expected", [
    ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ("postgresql://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("sqlite:///x.db", "sqlite:///x.db"),
])
def test_database_url_is_normalised(raw, expected):
    import db
    assert db.normalize_url(raw) == expected
