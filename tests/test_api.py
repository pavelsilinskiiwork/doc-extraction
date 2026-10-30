from fastapi.testclient import TestClient

import api
from fakes import GOOD, FakeProvider
from llm_provider import ProviderError
from make_samples import pdf_bytes

client = TestClient(api.app)


def use(monkeypatch, provider):
    monkeypatch.setattr(api, "provider_for", lambda name=None: provider)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_extract_text_invoice(monkeypatch):
    use(monkeypatch, FakeProvider([GOOD]))
    r = client.post("/extract", files={"file": ("inv.txt", b"Invoice A-1 ...", "text/plain")})
    body = r.json()
    assert r.status_code == 200
    assert body["invoice"]["vendor"] == "Acme Ltd" and body["invoice"]["currency"] == "USD"
    assert body["invoice"]["total"] == "22.68"  # money stays exact (string), not a float
    assert body["usage"]["attempts"] == 1 and body["usage"]["cost_usd"] > 0


def test_extract_pdf_invoice(monkeypatch):
    use(monkeypatch, FakeProvider([GOOD]))
    r = client.post("/extract", files={"file": ("inv.pdf", pdf_bytes("Invoice A-1"), "application/pdf")})
    assert r.status_code == 200


def test_unsupported_file_is_422(monkeypatch):
    use(monkeypatch, FakeProvider([GOOD]))
    r = client.post("/extract", files={"file": ("inv.docx", b"x", "application/octet-stream")})
    assert r.status_code == 422 and "Only .pdf and .txt" in r.json()["detail"]


def test_invoice_that_never_validates_is_422_with_errors(monkeypatch):
    use(monkeypatch, FakeProvider(["no json here"]))
    r = client.post("/extract", files={"file": ("inv.txt", b"text", "text/plain")})
    detail = r.json()["detail"]
    assert r.status_code == 422 and detail["errors"]


def test_provider_errors_map_to_429_and_502(monkeypatch):
    class Failing(FakeProvider):
        def __init__(self, rate_limited):
            super().__init__([GOOD])
            self.rate_limited = rate_limited

        def generate(self, *a, **k):
            raise ProviderError("x", rate_limited=self.rate_limited)

    for limited, status in ((True, 429), (False, 502)):
        use(monkeypatch, Failing(limited))
        r = client.post("/extract", files={"file": ("inv.txt", b"text", "text/plain")})
        assert r.status_code == status
