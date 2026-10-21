"""Provider registry, uncensored tag, memory fit and the LM Studio client.

Identical in Sentinel and Imprint: it tests only the two shared modules
(`services/providers.py`, `services/lmstudio_client.py`) and nothing app-side.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from services import lmstudio_client, providers
from services.lmstudio_client import LMStudioClient


# ── the registry ────────────────────────────────────────────────────────────

def test_every_provider_has_one_spec_and_a_known_protocol():
    assert providers.SUPPORTED == tuple(providers.REGISTRY)
    for name, spec in providers.REGISTRY.items():
        assert spec.name == name
        assert spec.kind in {providers.LOCAL, providers.CLOUD}
        assert spec.protocol in {providers.PROTOCOL_OPENAI,
                                 providers.PROTOCOL_OLLAMA,
                                 providers.PROTOCOL_NATIVE}


def test_ollama_and_lmstudio_are_local_and_the_rest_are_cloud():
    assert providers.LOCAL_PROVIDERS == ("ollama", "lmstudio")
    assert providers.CLOUD_PROVIDERS == {
        "openai", "deepseek", "kimi", "gemini", "anthropic", "qwen"}
    assert providers.get("lmstudio").protocol == "openai"
    assert providers.get("ollama").protocol == "ollama"


def test_local_providers_have_no_key_and_cloud_providers_have_one():
    for spec in providers.REGISTRY.values():
        assert bool(spec.key_name) == spec.is_cloud, spec.name


def test_is_local_is_case_insensitive_and_false_for_unknown_names():
    assert providers.is_local("LMStudio") and providers.is_local(" ollama ")
    assert not providers.is_local("openai")
    assert not providers.is_local("legacy-provider")
    assert not providers.is_local("") and not providers.is_local(None)
    assert not providers.is_cloud("legacy-provider")


def test_a_list_naming_one_local_runtime_admits_every_local_runtime():
    assert providers.expand_allowed(["ollama", "openai"]) == {
        "ollama", "lmstudio", "openai"}
    assert providers.expand_allowed(["lmstudio"]) == {"ollama", "lmstudio"}
    assert providers.expand_allowed(["openai", "gemini"]) == {"openai", "gemini"}
    assert providers.expand_allowed(None) == set()


@pytest.mark.parametrize("raw,expected", [
    (None, "http://127.0.0.1:11434"),
    ("", "http://127.0.0.1:11434"),
    ("10.0.0.5", "http://10.0.0.5:11434"),
    ("10.0.0.5:9000", "http://10.0.0.5:9000"),
    (":9000", "http://127.0.0.1:9000"),
    ("http://box.local:11434/", "http://box.local:11434"),
    ("https://box.local", "https://box.local:11434"),
    ("0.0.0.0:11434", "http://127.0.0.1:11434"),
])
def test_ollama_host_reads_ollama_host(raw, expected):
    env = {} if raw is None else {"OLLAMA_HOST": raw}
    assert providers.ollama_host(env) == expected
    assert providers.base_url("ollama", env) == expected


def test_lmstudio_url_default_and_override():
    assert providers.lmstudio_url({}) == "http://localhost:1234/v1"
    assert providers.base_url("lmstudio", {"LMSTUDIO_URL": "http://h:5/v1/"}) \
        == "http://h:5/v1"


def test_ollama_client_follows_ollama_host(monkeypatch):
    from services.ollama_client import OllamaClient
    monkeypatch.setenv("OLLAMA_HOST", "10.1.2.3:7")
    assert OllamaClient().base_url == "http://10.1.2.3:7"
    assert OllamaClient("http://x:1/").base_url == "http://x:1"


# ── the uncensored / abliterated tag ────────────────────────────────────────

# Every model downloaded on the owner's Mac (~/.lmstudio/models, 2026-10-19),
# by the name the file or the picker carries, and what each must be tagged.
ON_THIS_MAC = [
    ("DeepSeek-R1-Distill-Qwen-32B-abliterated-v2.i1-IQ3_M.gguf", "mradermacher", "abliterated"),
    ("DeepSeek-R1-Distill-Qwen-14B-abliterated.i1-Q6_K.gguf", "mradermacher", "abliterated"),
    ("DeepSeek-R1-Distill-Qwen-14B-abliterated-v2.i1-Q4_K_M.gguf", "mradermacher", "abliterated"),
    ("Dolphin3.0-Llama3.1-8B-Q8_0.gguf", "bartowski", "uncensored"),
    ("dolphin-2.9.2-qwen2-7b.Q8_0.gguf", "mradermacher", "uncensored"),
]


@pytest.mark.parametrize("name,publisher,expected", ON_THIS_MAC)
def test_every_model_on_this_mac_is_tagged(name, publisher, expected):
    assert providers.uncensored_tag(name, publisher) == expected
    assert providers.uncensored_tag(name) == expected         # name alone is enough
    assert expected.upper() in providers.tagged(name, publisher)


@pytest.mark.parametrize("name,expected", [
    ("mlabonne/Llama-3.1-8B-Instruct-abliterated", "abliterated"),
    ("huihui-ai/Qwen2.5-14B-Instruct-abliterated-v2", "abliterated"),
    ("Qwen3-14B-Heretic", "abliterated"),
    ("llama-3-lorablated-70b", "abliterated"),
    ("Llama-3-Uncensored-8B", "uncensored"),
    ("Wizard-Vicuna-30B-Uncensored", "uncensored"),
    ("dolphin-mixtral:8x7b", "uncensored"),
    ("dolphin3:8b", "uncensored"),
    ("Lexi-Llama-3-8B-Uncensored", "uncensored"),
    ("some-model-unfiltered", "uncensored"),
    ("DOLPHIN-2.9", "uncensored"),
])
def test_known_variant_names_are_tagged(name, expected):
    assert providers.uncensored_tag(name) == expected


@pytest.mark.parametrize("name", [
    "deepseek-r1:8b", "DeepSeek-R1-Distill-Qwen-14B", "llama3.1:8b",
    "muse-glimmer:30b-mlx", "gpt-4o", "claude-sonnet-5-5", "gemini-2.5-flash",
    "qwen3-14b", "gemma-3-27b-it", "", None,
])
def test_ordinary_models_carry_no_tag(name):
    assert providers.uncensored_tag(name) is None
    assert providers.tagged(name or "") == (name or "")


def test_publisher_metadata_tags_a_model_whose_name_does_not():
    assert providers.uncensored_tag("Qwen2.5-14B-Instruct", "huihui-ai") == "abliterated"
    assert providers.uncensored_tag("Llama-3-8B-Instruct", "cognitivecomputations") == "uncensored"
    assert providers.uncensored_tag("Llama-3-8B-Instruct", "bartowski") is None


def test_abliterated_wins_over_uncensored_when_both_apply():
    assert providers.uncensored_tag("dolphin-abliterated-8b") == "abliterated"


def test_the_tag_is_derived_from_the_name_not_the_quantiser():
    # mradermacher and bartowski quantise everything; they say nothing.
    assert providers.uncensored_tag("Llama-3.1-8B-Instruct-Q8_0.gguf", "mradermacher") is None


# ── does it fit? ────────────────────────────────────────────────────────────

GB = 1e9


def test_fit_check_levels_use_the_1_2_factor_against_free_and_total_memory():
    # 15 GB file on a 24 GB Mac: needs 18 GB, fits in total (24 - 3 = 21) ...
    assert providers.fit_check(15 * GB, 9 * GB, 24 * GB)["level"] == "tight"
    assert providers.fit_check(15 * GB, 20 * GB, 24 * GB)["level"] == "ok"
    # ... 19 GB needs 22.8 GB: never fits on 24 GB.
    assert providers.fit_check(19 * GB, 20 * GB, 24 * GB)["level"] == "too_big"
    fit = providers.fit_check(10 * GB, 24 * GB, 24 * GB)
    assert fit["needed_gb"] == pytest.approx(12.0)


def test_fit_check_boundary_is_exact():
    assert providers.fit_check(10 * GB, 12 * GB, 40 * GB)["level"] == "ok"
    assert providers.fit_check(10 * GB, 11.99 * GB, 40 * GB)["level"] == "tight"


# ── LM Studio: reading the disk ─────────────────────────────────────────────

def _models_dir(tmp_path: Path) -> Path:
    for rel, size in {
        "bartowski/Dolphin3.0-Llama3.1-8B-GGUF/Dolphin3.0-Llama3.1-8B-Q8_0.gguf": 80,
        "mradermacher/DeepSeek-R1-Distill-Qwen-14B-abliterated-i1-GGUF/"
        "DeepSeek-R1-Distill-Qwen-14B-abliterated.i1-Q6_K.gguf": 110,
        "mradermacher/Big-GGUF/Big-Q4_K_M-00001-of-00002.gguf": 50,
        "mradermacher/Big-GGUF/Big-Q4_K_M-00002-of-00002.gguf": 50,
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    (tmp_path / "bartowski" / "notes.txt").write_text("not a model")
    return tmp_path


def test_scan_disk_lists_gguf_files_with_publisher_quant_and_size(tmp_path):
    found = {m["name"]: m for m in lmstudio_client.scan_disk(_models_dir(tmp_path))}
    assert set(found) == {"Dolphin3.0-Llama3.1-8B",
                          "DeepSeek-R1-Distill-Qwen-14B-abliterated-i1", "Big"}
    dolphin = found["Dolphin3.0-Llama3.1-8B"]
    assert dolphin["publisher"] == "bartowski" and dolphin["quant"] == "Q8_0"
    assert dolphin["size_bytes"] == 80
    assert dolphin["state"] == "on disk · server off"
    assert dolphin["id"] == "bartowski/Dolphin3.0-Llama3.1-8B-GGUF/Dolphin3.0-Llama3.1-8B-Q8_0.gguf"
    assert found["DeepSeek-R1-Distill-Qwen-14B-abliterated-i1"]["quant"] == "Q6_K"


def test_split_shards_are_listed_once(tmp_path):
    big = [m for m in lmstudio_client.scan_disk(_models_dir(tmp_path))
           if m["name"] == "Big"]
    assert len(big) == 1 and big[0]["path"].endswith("00001-of-00002.gguf")


def test_scan_disk_of_a_missing_folder_is_empty(tmp_path):
    assert lmstudio_client.scan_disk(tmp_path / "nope") == []


@pytest.mark.parametrize("filename,quant", [
    ("DeepSeek-R1-Distill-Qwen-32B-abliterated-v2.i1-IQ3_M.gguf", "IQ3_M"),
    ("DeepSeek-R1-Distill-Qwen-14B-abliterated-v2.i1-Q4_K_M.gguf", "Q4_K_M"),
    ("dolphin-2.9.2-qwen2-7b.Q8_0.gguf", "Q8_0"),
    ("model-F16.gguf", "F16"),
    ("plain.gguf", ""),
])
def test_quant_of_reads_the_quantisation_from_the_file_name(filename, quant):
    assert lmstudio_client.quant_of(filename) == quant


# ── LM Studio: the server ───────────────────────────────────────────────────

class _Reply:
    def __init__(self, payload=None, lines=(), status=200):
        self._payload, self._lines, self.status = payload, lines, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))

    def json(self):
        return self._payload

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


API_V0 = {"data": [
    {"id": "deepseek-r1-distill-qwen-14b-abliterated", "type": "llm",
     "publisher": "mradermacher", "arch": "qwen2", "quantization": "Q6_K",
     "state": "loaded", "max_context_length": 131072},
    {"id": "dolphin3.0-llama3.1-8b", "type": "llm", "publisher": "bartowski",
     "arch": "llama", "quantization": "Q8_0", "state": "not-loaded",
     "max_context_length": 131072},
    {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings",
     "state": "not-loaded"},
]}


def test_server_up_lists_state_quant_arch_and_context_and_drops_embedders(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        return _Reply(API_V0)

    monkeypatch.setattr(lmstudio_client.requests, "get", fake_get)
    monkeypatch.setattr(lmstudio_client, "scan_disk", lambda root=None: [])
    models = LMStudioClient("http://localhost:1234/v1").model_details()
    assert seen["url"] == "http://localhost:1234/api/v0/models"
    assert [m["id"] for m in models] == ["deepseek-r1-distill-qwen-14b-abliterated",
                                         "dolphin3.0-llama3.1-8b"]
    first = models[0]
    assert (first["state"], first["quant"], first["arch"], first["max_context"]) \
        == ("loaded", "Q6_K", "qwen2", 131072)
    assert models[1]["state"] == "not-loaded"
    assert "Authorization" not in (seen["kwargs"].get("headers") or {})


def test_server_off_falls_back_to_the_disk_listing(monkeypatch, tmp_path):
    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(lmstudio_client.requests, "get", refuse)
    monkeypatch.setattr(lmstudio_client, "models_dir", lambda: _models_dir(tmp_path))
    client = LMStudioClient()
    assert client.server_up() is False
    states = {m["state"] for m in client.model_details()}
    assert states == {"on disk · server off"}
    assert len(client.list_models()) == 3


def test_sizes_come_from_disk_even_when_the_server_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(lmstudio_client.requests, "get", lambda *a, **k: _Reply(API_V0))
    monkeypatch.setattr(lmstudio_client, "models_dir", lambda: _models_dir(tmp_path))
    # The server's id for the Dolphin file is a slug of the file name.
    api = {"data": [{"id": "dolphin3.0-llama3.1-8b", "type": "llm", "state": "not-loaded"}]}
    monkeypatch.setattr(lmstudio_client.requests, "get", lambda *a, **k: _Reply(api))
    assert LMStudioClient().model_size_bytes("dolphin3.0-llama3.1-8b") == 80
    assert LMStudioClient().model_size_bytes("nothing") is None


def test_chat_uses_openai_compatible_completions_without_a_key(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Reply({"choices": [{"message": {"content": "hi there"}}]})

    monkeypatch.setattr(lmstudio_client.requests, "post", fake_post)
    out = LMStudioClient("http://localhost:1234/v1").chat(
        "m", [{"role": "user", "content": "hi"}])
    assert out == "hi there"
    assert seen["url"] == "http://localhost:1234/v1/chat/completions"
    assert seen["json"]["model"] == "m" and seen["json"]["stream"] is False
    assert "headers" not in seen          # no Authorization, no key of any kind


def test_stream_chat_yields_the_deltas_until_done(monkeypatch):
    lines = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        b"",
        b'data: {"choices":[{"delta":{"content":"lo"}}]}',
        b"data: [DONE]",
        b'data: {"choices":[{"delta":{"content":"never"}}]}',
    ]
    monkeypatch.setattr(lmstudio_client.requests, "post",
                        lambda *a, **k: _Reply(lines=lines))
    assert "".join(LMStudioClient().stream_chat("m", [])) == "Hello"


def test_a_stream_error_line_is_raised_not_swallowed(monkeypatch):
    lines = [b'data: {"error":"out of memory"}']
    monkeypatch.setattr(lmstudio_client.requests, "post",
                        lambda *a, **k: _Reply(lines=lines))
    with pytest.raises(RuntimeError, match="out of memory"):
        list(LMStudioClient().stream_chat("m", []))


def test_a_dead_server_surfaces_as_a_runtime_error(monkeypatch):
    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(lmstudio_client.requests, "post", refuse)
    with pytest.raises(RuntimeError, match="LM Studio"):
        LMStudioClient().chat("m", [])
    with pytest.raises(RuntimeError, match="LM Studio"):
        list(LMStudioClient().stream_chat("m", []))


def test_start_server_runs_lms_server_start(monkeypatch):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "Success! Server is now running on port 1234", ""

    monkeypatch.setattr(lmstudio_client, "lms_binary", lambda: "/x/lms")
    monkeypatch.setattr(lmstudio_client.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or Done())
    ok, message = LMStudioClient.start_server()
    assert ok and "running" in message
    assert calls == [["/x/lms", "server", "start"]]


def test_start_server_without_lms_says_how_to_get_it(monkeypatch):
    monkeypatch.setattr(lmstudio_client, "lms_binary", lambda: None)
    ok, message = LMStudioClient.start_server()
    assert not ok and "lms" in message


def test_start_server_reports_a_failing_command(monkeypatch):
    class Done:
        returncode, stdout, stderr = 1, "", "port in use"

    monkeypatch.setattr(lmstudio_client, "lms_binary", lambda: "/x/lms")
    monkeypatch.setattr(lmstudio_client.subprocess, "run", lambda *a, **k: Done())
    ok, message = LMStudioClient.start_server()
    assert not ok and message == "port in use"


def test_the_client_only_talks_to_the_configured_local_url():
    assert LMStudioClient().base_url == providers.DEFAULT_LMSTUDIO_URL
    assert LMStudioClient("http://localhost:9/v1/").root == "http://localhost:9"


def test_the_registry_copy_has_no_network_or_qt_imports():
    source = Path(providers.__file__).read_text(encoding="utf-8")
    assert "requests" not in source and "PySide6" not in source
