import os
from google import genai
from google.genai import types as genai_types

from services.api_limits import REQUEST_TIMEOUT_MS


def _gemini_api_key():
    """Read the Gemini key, accepting either name. Google's own SDK uses
    GOOGLE_API_KEY; Sentinel historically referenced GEMINI_API_KEY. Support
    both so whichever is set in .env works."""
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


class GeminiClientWrapper:
    KNOWN_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]

    def __init__(self):
        self.api_key = _gemini_api_key()
        self.client = (
            genai.Client(
                api_key=self.api_key,
                # google-genai takes milliseconds here, unlike the other SDKs
                http_options=genai_types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
            )
            if self.api_key
            else None
        )

    @staticmethod
    def key_available():
        return bool(_gemini_api_key())

    def list_models(self) -> list[str]:
        """Model ids that support generateContent, from the API when reachable.

        Falls back to KNOWN_MODELS with no key or on any API error so the model
        dropdowns are never left empty.
        """
        if not self.client:
            return self.KNOWN_MODELS
        try:
            def supports_generate(m) -> bool:
                # The SDK has used both attribute names across versions, and
                # either can come back as None.
                actions = (getattr(m, "supported_actions", None)
                           or getattr(m, "supported_generation_methods", None)
                           or [])
                return "generateContent" in actions

            models = sorted(
                m.name.replace("models/", "")
                for m in self.client.models.list()
                if supports_generate(m)
            )
            return models if models else self.KNOWN_MODELS
        except Exception:
            return self.KNOWN_MODELS

    def chat(self, messages, model="gemini-2.5-flash"):
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        prompt = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages
        )

        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
        )

        text = response.text or ""

        usage_metadata = getattr(response, "usage_metadata", None)

        usage = {
            "input_tokens": getattr(usage_metadata, "prompt_token_count", 0) if usage_metadata else 0,
            "output_tokens": getattr(usage_metadata, "candidates_token_count", 0) if usage_metadata else 0,
            "total_tokens": getattr(usage_metadata, "total_token_count", 0) if usage_metadata else 0,
        }

        return text, usage

    def generate(self, prompt, model="gemini-2.5-flash"):
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages=messages, model=model)
    
    def stream_chat(self, messages, model="gemini-2.5-flash"):
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        prompt = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages
        )

        try:
            stream = self.client.models.generate_content_stream(
                model=model,
                contents=prompt,
            )

            for chunk in stream:
                text = getattr(chunk, "text", "")
                if text:
                    yield text

        except Exception as e:
            raise RuntimeError(f"Gemini streaming request failed: {e}")