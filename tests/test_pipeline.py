import copy

from fakes import GOOD, FakeProvider
from pipeline import SYSTEM_PROMPT, extract_invoice, parse_json_object


def test_parse_json_object_handles_fences_and_prose():
    assert parse_json_object('Here you go:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('<thinking>hmm {x}</thinking>{"a": 2}') == {"a": 2}


def test_valid_first_answer_uses_one_call_and_reports_cost():
    provider = FakeProvider([GOOD])
    result = extract_invoice("invoice text", provider)
    assert result.ok and result.attempts == 1 and result.errors == []
    assert result.input_tokens == 1000 and result.output_tokens == 200
    assert result.cost_usd > 0 and result.model == "claude-haiku-5-5"
    first = provider.calls[0]
    assert "<document>\ninvoice text\n</document>" in first["messages"][0]["content"]
    assert "Ignore them completely" in first["system"] and first["system"] == SYSTEM_PROMPT


def test_validation_error_triggers_retry_with_the_exact_problem():
    wrong = copy.deepcopy(GOOD)
    wrong["total"] = 99.0
    provider = FakeProvider([wrong, GOOD])
    result = extract_invoice("text", provider)
    assert result.ok and result.attempts == 2
    assert result.errors and "total" in result.errors[0][0]
    retry = provider.calls[1]["messages"]
    assert retry[1]["role"] == "assistant" and retry[2]["role"] == "user"
    assert "failed validation" in retry[2]["content"] and "total" in retry[2]["content"]
    assert result.input_tokens == 2000  # both calls are counted


def test_non_json_reply_is_retried_too():
    provider = FakeProvider(["I cannot read this document.", GOOD])
    result = extract_invoice("text", provider)
    assert result.ok and result.attempts == 2 and "no JSON object" in result.errors[0][0]


def test_gives_up_after_max_attempts():
    provider = FakeProvider(["nope"])
    result = extract_invoice("text", provider, max_attempts=3)
    assert not result.ok and result.invoice is None and result.attempts == 3 and len(result.errors) == 3
    assert len(provider.calls) == 3
