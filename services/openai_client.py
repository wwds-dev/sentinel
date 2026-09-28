import base64
import os
import re
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from services.api_limits import REQUEST_TIMEOUT_SECONDS, MAX_RETRIES
from services.runtime_paths import user_data_base

# dall-e-3 left OpenAI's model list in Sep 2026; the gpt-image models replace it.
DEFAULT_IMAGE_MODEL = "gpt-image-1.5"
IMAGE_MODEL_PREFIXES = ("gpt-image", "chatgpt-image", "dall-e")


def is_image_model(model: str) -> bool:
    """True for a model served by images.generate rather than chat."""
    return str(model).lower().startswith(IMAGE_MODEL_PREFIXES)


def image_output_dir() -> Path:
    """Generated images go under the ignored output/ folder of the data base."""
    return user_data_base() / "output" / "images"


class OpenAIClientWrapper:
    KNOWN_MODELS = [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "gpt-4.1",
        DEFAULT_IMAGE_MODEL,
    ]

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.client = (
            OpenAI(
                api_key=self.api_key,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
            if self.api_key
            else None
        )

    @staticmethod
    def key_available():
        return bool(os.getenv("OPENAI_API_KEY"))

    def list_models(self) -> list[str]:
        """Chat-capable model ids, newest listing from the API when reachable.

        Falls back to KNOWN_MODELS with no key or on any API error so the model
        dropdowns are never left empty.
        """
        if not self.client:
            return self.KNOWN_MODELS
        try:
            result = self.client.models.list()
            models = sorted(
                m.id for m in result.data
                if any(x in m.id.lower() for x in ("gpt", "o1", "o3", "o4", "dall-e", "image"))
            )
            return models if models else self.KNOWN_MODELS
        except Exception:
            return self.KNOWN_MODELS

    def chat(self, messages, model="gpt-4o-mini"):
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
        )

        text = response.choices[0].message.content or ""

        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        }

        return text, usage

    def generate(self, prompt, model="gpt-4o-mini"):
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages=messages, model=model)
    
    def generate_image(self, prompt: str, model: str = DEFAULT_IMAGE_MODEL,
                       size: str = "1024x1024", quality: str | None = None,
                       output_dir: Path | None = None) -> tuple[str, dict | None]:
        """Generate one image, save it, and return (transcript text, usage).

        The gpt-image models answer with base64 data, not a URL, so the image
        is written under output/images/ and the text names that file. Quality
        is only sent when given: gpt-image takes low/medium/high/auto, not
        dall-e-3's standard/hd, and the API default is auto.
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        params = {"model": model, "prompt": prompt, "size": size, "n": 1}
        if quality:
            params["quality"] = quality
        response = self.client.images.generate(**params)

        image = (response.data or [None])[0]
        if image is None:
            raise RuntimeError(f"{model} returned no image.")
        usage = None
        if getattr(response, "usage", None) is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        if not image.b64_json:
            if image.url:
                return f"Image: {image.url}", usage
            raise RuntimeError(f"{model} returned neither image data nor a URL.")

        extension = getattr(response, "output_format", None) or "png"
        folder = Path(output_dir) if output_dir is not None else image_output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", model)
        path = folder / f"{stamp}_{safe_model}.{extension}"
        path.write_bytes(base64.b64decode(image.b64_json))
        return f"Image saved to {path}", usage

    def stream_chat(self, messages, model="gpt-4o-mini"):
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        try:
            stream = self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )

            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

        except Exception as e:
            raise RuntimeError(f"OpenAI streaming request failed: {e}")
