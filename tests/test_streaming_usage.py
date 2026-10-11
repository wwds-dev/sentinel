"""Streaming requests bill from the provider's real token usage, not char/4.

Regression cover for the defect where the streaming path discarded the SDK's
token counts and always recorded a `stream-estimated` char/4 figure, which then
also fed the session/daily budget caps. Fakes stand in for the SDKs, so these
tests never touch the network.
"""

from __future__ import annotations

from types import SimpleNamespace

from services.usage_tracker import UsageTracker


# ── ChatWorker captures the usage sentinel and does not display it ───────────

def _run_worker(stream_items):
    from ui.workers import ChatWorker

    tokens: list[str] = []
    usages: list[dict] = []
    worker = ChatWorker(
        run_backend_func=lambda *a, **k: iter(stream_items),
        backend="anthropic", model="claude-sonnet-5",
        messages=[{"role": "user", "content": "hi"}], prompt="hi",
    )
    worker.token_signal.connect(tokens.append)
    worker.usage_signal.connect(usages.append)
    finished: list[str] = []
    worker.finished_signal.connect(finished.append)
    worker.run()  # run synchronously on this thread
    return tokens, usages, finished


def test_streaming_uses_real_usage_when_the_sentinel_is_present():
    tokens, usages, finished = _run_worker(
        ["Hello", " world", {"__usage__": {"input_tokens": 120, "output_tokens": 30}}]
    )
    assert "".join(tokens) == "Hello world"          # sentinel not shown as text
    assert finished == ["Hello world"]
    assert usages == [{"input_tokens": 120, "output_tokens": 30}]


def test_streaming_falls_back_to_estimate_without_a_sentinel():
    tokens, usages, _ = _run_worker(["Just", " text"])
    assert "".join(tokens) == "Just text"
    assert usages == [{"cost_type_override": "stream-estimated"}]


def test_real_stream_usage_is_billed_exact_not_estimated():
    # The sentinel's inner dict flows straight into log_request → normalize_usage.
    i, o, cost_type = UsageTracker().normalize_usage(
        {"input_tokens": 120, "output_tokens": 30}, "prompt", "response text")
    assert (i, o, cost_type) == (120, 30, "exact")


# ── Anthropic client surfaces real usage after the text stream ──────────────

def test_anthropic_stream_yields_a_usage_sentinel():
    from services.anthropic_client import AnthropicClientWrapper

    class _FakeStream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        @property
        def text_stream(self):
            yield from ["Ans", "wer"]
        def get_final_message(self):
            return SimpleNamespace(usage=SimpleNamespace(
                input_tokens=90, output_tokens=12,
                cache_read_input_tokens=10, cache_creation_input_tokens=0))

    wrapper = AnthropicClientWrapper()
    wrapper.client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStream()))

    items = list(wrapper.stream_chat([{"role": "user", "content": "q"}], "claude-sonnet-5"))
    text = [x for x in items if isinstance(x, str)]
    sentinels = [x for x in items if isinstance(x, dict)]
    assert "".join(text) == "Answer"
    # total input = 90 + 10 cache-read; cached portion surfaced separately
    assert sentinels == [{"__usage__": {
        "input_tokens": 100, "output_tokens": 12, "cached_input_tokens": 10}}]


# ── Kimi client requests usage and surfaces it ──────────────────────────────

def test_kimi_stream_requests_usage_and_yields_a_sentinel():
    from services.kimi_client import KimiClientWrapper

    captured_kwargs = {}

    def _create(**kwargs):
        captured_kwargs.update(kwargs)
        return iter([
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))], usage=None),
            SimpleNamespace(choices=[], usage=SimpleNamespace(
                prompt_tokens=50, completion_tokens=10, total_tokens=60)),
        ])

    wrapper = KimiClientWrapper()
    wrapper.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))

    items = list(wrapper.stream_chat([{"role": "user", "content": "q"}], "kimi-k2.7-code"))
    assert captured_kwargs.get("stream_options") == {"include_usage": True}
    text = [x for x in items if isinstance(x, str)]
    sentinels = [x for x in items if isinstance(x, dict)]
    assert "".join(text) == "Hello"
    assert sentinels == [{"__usage__": {
        "input_tokens": 50, "cached_input_tokens": 0,
        "output_tokens": 10, "total_tokens": 60}}]


def test_an_ollama_error_line_mid_stream_raises_instead_of_ending_quietly(monkeypatch):
    """Ollama reports a runner crash part-way through as an error line with
    HTTP 200; a cut-off reply must not be saved as a successful turn."""
    import json as _json

    import pytest
    from services import ollama_client

    lines = [
        _json.dumps({"message": {"content": "The answer is "}}).encode(),
        _json.dumps({"error": "model runner has unexpectedly stopped"}).encode(),
    ]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            return iter(lines)

    monkeypatch.setattr(ollama_client.requests, "post", lambda *a, **k: Response())
    client = ollama_client.OllamaClient()
    received = []
    with pytest.raises(RuntimeError, match="stopped mid-reply"):
        for chunk in client.stream_chat("llama3", [{"role": "user", "content": "q"}]):
            received.append(chunk)
    assert received == ["The answer is "]
