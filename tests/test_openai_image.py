"""OpenAI image generation, against a fake client: no network, no cost."""

import base64
from types import SimpleNamespace

import pytest

from services import openai_client
from services.openai_client import (
    DEFAULT_IMAGE_MODEL, OpenAIClientWrapper, is_image_model,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake image body"


class FakeImages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _response(b64=None, url=None, output_format="png", usage=None):
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=b64, url=url)],
        output_format=output_format,
        usage=usage,
    )


def _wrapper(response):
    wrapper = OpenAIClientWrapper.__new__(OpenAIClientWrapper)
    wrapper.api_key = "test"
    images = FakeImages(response)
    wrapper.client = SimpleNamespace(images=images)
    return wrapper, images


def test_default_model_is_a_current_gpt_image_model():
    assert DEFAULT_IMAGE_MODEL.startswith("gpt-image")
    assert DEFAULT_IMAGE_MODEL in OpenAIClientWrapper.KNOWN_MODELS
    assert "dall-e-3" not in OpenAIClientWrapper.KNOWN_MODELS


@pytest.mark.parametrize("model", [
    "gpt-image-1", "gpt-image-1.5", "gpt-image-2.5-flare", "chatgpt-image-latest",
])
def test_selected_model_is_passed_through(tmp_path, model):
    wrapper, images = _wrapper(_response(b64=base64.b64encode(PNG_BYTES).decode()))
    wrapper.generate_image("a lighthouse", model=model, output_dir=tmp_path)
    assert images.calls[0]["model"] == model
    assert images.calls[0]["prompt"] == "a lighthouse"
    assert images.calls[0]["n"] == 1
    # dall-e-3's "standard" is not a gpt-image quality; omit it unless asked.
    assert "quality" not in images.calls[0]


def test_default_model_is_used_when_none_is_given(tmp_path):
    wrapper, images = _wrapper(_response(b64=base64.b64encode(PNG_BYTES).decode()))
    wrapper.generate_image("a lighthouse", output_dir=tmp_path)
    assert images.calls[0]["model"] == DEFAULT_IMAGE_MODEL


def test_explicit_quality_is_forwarded(tmp_path):
    wrapper, images = _wrapper(_response(b64=base64.b64encode(PNG_BYTES).decode()))
    wrapper.generate_image("x", quality="low", output_dir=tmp_path)
    assert images.calls[0]["quality"] == "low"


def test_b64_image_is_decoded_and_written(tmp_path):
    usage = SimpleNamespace(input_tokens=12, output_tokens=272, total_tokens=284)
    wrapper, _ = _wrapper(_response(
        b64=base64.b64encode(PNG_BYTES).decode(), output_format="webp", usage=usage,
    ))
    text, reported = wrapper.generate_image(
        "a lighthouse", model="gpt-image-2.5-flare", output_dir=tmp_path / "images",
    )
    files = list((tmp_path / "images").iterdir())
    assert len(files) == 1
    saved = files[0]
    assert saved.read_bytes() == PNG_BYTES
    assert saved.suffix == ".webp"
    assert "gpt-image-2.5-flare" in saved.name
    assert text == f"Image saved to {saved}"
    assert reported == {"input_tokens": 12, "output_tokens": 272, "total_tokens": 284}


def test_default_output_dir_is_under_the_ignored_output_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(openai_client, "user_data_base", lambda: tmp_path)
    wrapper, _ = _wrapper(_response(b64=base64.b64encode(PNG_BYTES).decode()))
    text, _ = wrapper.generate_image("x")
    saved = next((tmp_path / "output" / "images").iterdir())
    assert saved.read_bytes() == PNG_BYTES
    assert str(saved) in text


def test_url_response_is_still_shown(tmp_path):
    wrapper, _ = _wrapper(_response(url="https://example.invalid/i.png"))
    text, usage = wrapper.generate_image("x", output_dir=tmp_path)
    assert text == "Image: https://example.invalid/i.png"
    assert usage is None
    assert not any(tmp_path.iterdir())


def test_empty_response_is_an_error(tmp_path):
    wrapper, _ = _wrapper(SimpleNamespace(data=[], output_format=None, usage=None))
    with pytest.raises(RuntimeError, match="no image"):
        wrapper.generate_image("x", output_dir=tmp_path)


@pytest.mark.parametrize("model,expected", [
    ("gpt-image-1.5", True), ("chatgpt-image-latest", True), ("dall-e-3", True),
    ("gpt-4.1-mini", False), ("gpt-4o", False), ("chatgpt-4o-latest", False),
])
def test_is_image_model(model, expected):
    assert is_image_model(model) is expected


def test_run_backend_passes_the_selected_image_model():
    """main.py routes every image model to generate_image with that model."""
    import main

    calls = []
    host = SimpleNamespace(openai=SimpleNamespace(
        generate_image=lambda prompt, model: calls.append((prompt, model)) or ("ok", None),
    ))
    result = main.GodAI.run_backend(
        host, "openai", "gpt-image-2", [{"role": "user", "content": "p"}], "p",
    )
    assert result == ("ok", None)
    assert calls == [("p", "gpt-image-2")]
