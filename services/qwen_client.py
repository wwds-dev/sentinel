import os
from openai import OpenAI

# Alibaba's Qwen, served through Model Studio / DashScope. The API is
# OpenAI-compatible, so this wrapper is the same shape as the DeepSeek and Kimi
# ones — only the base URL and key differ.
#
# Endpoints are regional. The international ("intl", Singapore-routed) host is
# the right default outside mainland China; DASHSCOPE_BASE_URL overrides it for
# a China account or a workspace-scoped regional host, so switching regions
# never needs a code change.
INTL_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
CHINA_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _base_url() -> str:
    # `.env.example` ships the line as `DASHSCOPE_BASE_URL=`; an empty value
    # must mean "the default", not "no base URL at all".
    return os.getenv("DASHSCOPE_BASE_URL", "").strip() or INTL_BASE_URL


class QwenClientWrapper:
    # Qwen3.8-Max — 2.4T-parameter MoE (95B active per token), released
    # 2026-08-03. 1M-token context, up to 131,072 output tokens, multimodal in
    # (text/image/video), text out, with function calling and structured output.
    KNOWN_MODELS = [
        "qwen3.8-max",
        "qwen3-max",
        "qwen-plus",
        "qwen-flash",
    ]

    DEFAULT_MODEL = "qwen3.8-max"

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.base_url = _base_url()
        self.client = (
            OpenAI(api_key=self.api_key, base_url=self.base_url)
            if self.api_key
            else None
        )

    @staticmethod
    def key_available():
        return bool(os.getenv("DASHSCOPE_API_KEY"))

    def list_models(self) -> list[str]:
        """Model ids from the API when reachable, else KNOWN_MODELS.

        Mirrors the other clients: never returns empty, so the model dropdowns
        always have something selectable.
        """
        if not self.client:
            return self.KNOWN_MODELS
        try:
            result = self.client.models.list()
            models = sorted(m.id for m in result.data)
            return models if models else self.KNOWN_MODELS
        except Exception:
            return self.KNOWN_MODELS

    def chat(self, messages, model=DEFAULT_MODEL):
        if not self.client:
            raise RuntimeError("DASHSCOPE_API_KEY is not set.")

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

    def generate(self, prompt, model=DEFAULT_MODEL):
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages=messages, model=model)

    def stream_chat(self, messages, model=DEFAULT_MODEL):
        if not self.client:
            raise RuntimeError("DASHSCOPE_API_KEY is not set.")

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
            raise RuntimeError(f"Qwen streaming request failed: {e}")

    def test_connection(self) -> tuple[bool, str]:
        """Send a minimal request and return (success, message)."""
        if not self.client:
            return False, "DASHSCOPE_API_KEY is not set. Add it to your .env file."
        try:
            self.client.chat.completions.create(
                model=self.DEFAULT_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return True, f"Connected to Qwen ({self.base_url})."
        except Exception as e:
            return False, f"Qwen connection failed: {e}"
