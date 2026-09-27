import os
from openai import OpenAI

from services.api_limits import REQUEST_TIMEOUT_SECONDS, MAX_RETRIES


DEEPSEEK_BALANCE_MESSAGE = (
    "The DeepSeek cloud API could not run this request because the cloud account "
    "has no API credit. Local DeepSeek models in Ollama remain free to use."
)


class DeepSeekInsufficientBalanceError(RuntimeError):
    """A safe, user-facing form of DeepSeek's HTTP 402 response."""


def is_insufficient_balance_error(error: object) -> bool:
    """Recognize DeepSeek balance failures without depending on one SDK version."""
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    status_code = status_code or getattr(response, "status_code", None)
    text = str(error).lower()
    return status_code == 402 or "insufficient balance" in text or "no api credit" in text


def _friendly_deepseek_error(error: Exception, *, streaming: bool) -> RuntimeError:
    if is_insufficient_balance_error(error):
        return DeepSeekInsufficientBalanceError(DEEPSEEK_BALANCE_MESSAGE)
    action = "streaming request" if streaming else "request"
    return RuntimeError(f"DeepSeek {action} failed: {error}")


class DeepSeekClientWrapper:
    # Offline fallback only. Ordered to match what the API currently serves —
    # the older deepseek-chat / deepseek-reasoner ids are no longer offered,
    # and deepseek-v4-flash is served as deepseek-flash (seen 2026-09-27).
    KNOWN_MODELS = [
        "deepseek-flash",
        "deepseek-v4-pro",
    ]

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.client = (
            OpenAI(
                api_key=self.api_key,
                base_url="https://api.deepseek.com",
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
            if self.api_key
            else None
        )

    @staticmethod
    def key_available():
        return bool(os.getenv("DEEPSEEK_API_KEY"))

    def list_models(self) -> list[str]:
        """Available model ids, from the API when reachable.

        Falls back to KNOWN_MODELS with no key or on any API error so the model
        dropdowns are never left empty.
        """
        if not self.client:
            return self.KNOWN_MODELS
        try:
            result = self.client.models.list()
            models = sorted(m.id for m in result.data)
            return models if models else self.KNOWN_MODELS
        except Exception:
            return self.KNOWN_MODELS

    def chat(self, messages, model="deepseek-flash"):
        if not self.client:
            raise RuntimeError("DEEPSEEK_API_KEY is not set.")

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
            )
        except Exception as error:
            raise _friendly_deepseek_error(error, streaming=False) from error

        text = response.choices[0].message.content or ""

        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        }

        return text, usage

    def generate(self, prompt, model="deepseek-flash"):
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages=messages, model=model)
    
    def stream_chat(self, messages, model="deepseek-flash"):
        if not self.client:
            raise RuntimeError("DEEPSEEK_API_KEY is not set.")

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

        except Exception as error:
            raise _friendly_deepseek_error(error, streaming=True) from error
