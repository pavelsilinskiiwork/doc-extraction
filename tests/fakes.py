"""A scripted provider so no test touches the network."""

import json

from llm_provider import LLMResult, Provider

GOOD = {
    "vendor": "Acme Ltd", "invoice_number": "A-1", "invoice_date": "2026-03-04", "due_date": None,
    "currency": "usd",
    "line_items": [{"description": "Widget", "quantity": 2, "unit_price": 10.5, "amount": 21.0}],
    "subtotal": 21.0, "discount": 0, "tax": 1.68, "total": 22.68,
}


class FakeProvider(Provider):
    name = "fake"
    model = "claude-haiku-5-5"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, system, messages, tools=None, max_tokens=500):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if not isinstance(text, str):
            text = json.dumps(text)
        return LLMResult(text=text, input_tokens=1000, output_tokens=200, model=self.model, provider=self.name)
