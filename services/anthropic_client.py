import os

from services.api_limits import REQUEST_TIMEOUT_SECONDS, MAX_RETRIES

try:
    import anthropic as _sdk
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


class AnthropicClientWrapper:
    KNOWN_MODELS = [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]

    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = (
            _sdk.Anthropic(
                api_key=api_key,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
            if _HAS_SDK and api_key
            else None
        )

    @staticmethod
    def key_available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY", ""))

    def list_models(self) -> list[str]:
        if not self.client:
            return self.KNOWN_MODELS
        try:
            result = self.client.models.list()
            models = sorted(m.id for m in result.data)
            return models if models else self.KNOWN_MODELS
        except Exception:
            return self.KNOWN_MODELS

    def test_connection(self) -> tuple[bool, str]:
        """Send a minimal request and return (success, message).
        Distinguishes: no key, bad key (401), network failure, model error, and OK."""
        if not _HAS_SDK:
            return False, "The 'anthropic' package is not installed. Run: pip install anthropic"
        if not self.client:
            return False, "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        try:
            self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                messages=[{"role": "user", "content": "hi"}],
            )
            return True, "Connection successful — API key is valid."
        except _sdk.AuthenticationError:
            return False, "Authentication failed (401). Your ANTHROPIC_API_KEY is invalid or expired. Generate a new one at console.anthropic.com."
        except _sdk.APIConnectionError:
            return False, "Connection error — could not reach api.anthropic.com. Check your internet connection or firewall."
        except _sdk.RateLimitError:
            return False, "Rate limit hit (429) — but the key works. Slow down requests."
        except _sdk.NotFoundError as e:
            return False, f"Model not found (404): {e}"
        except Exception as e:
            return False, f"Unexpected error ({type(e).__name__}): {e}"

    def stream_chat(self, messages: list, model: str = "claude-sonnet-4-6"):
        if not self.client:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set.\n"
                "Add it to your .env file and restart the app."
            )
        system, chat_messages = self._split(messages)
        kwargs = {"model": model, "max_tokens": 8096, "messages": chat_messages}
        if system:
            kwargs["system"] = system
        try:
            with self.client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
                # Surface the provider's real token usage after the text stream
                # so billing/budget use exact counts, not a char/4 estimate.
                try:
                    usage = stream.get_final_message().usage
                    inp = int(getattr(usage, "input_tokens", 0) or 0)
                    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                    real = {
                        "input_tokens": inp + cache_read + cache_write,
                        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                    }
                    if cache_read:
                        real["cached_input_tokens"] = cache_read
                    yield {"__usage__": real}
                except Exception:
                    pass  # fall back to the stream estimate if usage is unavailable
        except _sdk.AuthenticationError:
            raise RuntimeError(
                "AuthenticationError (401) — API key invalid or expired.\n"
                "→ Go to console.anthropic.com → API Keys and generate a new key,\n"
                "  then update ANTHROPIC_API_KEY in your .env file."
            )
        except _sdk.APIConnectionError:
            raise RuntimeError(
                "APIConnectionError — Could not reach api.anthropic.com.\n"
                "→ Check your internet connection.\n"
                "→ If your key is new, make sure it has been activated.\n"
                "→ Check that no firewall or VPN is blocking outbound HTTPS."
            )
        except _sdk.RateLimitError:
            raise RuntimeError(
                "RateLimitError (429) — Too many requests.\n"
                "→ Wait a moment and try again.\n"
                "→ Check your usage limits at console.anthropic.com."
            )
        except _sdk.NotFoundError:
            raise RuntimeError(
                f"NotFoundError (404) — Model '{model}' does not exist.\n"
                "→ Select a different model from the dropdown."
            )
        except _sdk.APIStatusError as e:
            raise RuntimeError(
                f"APIStatusError ({e.status_code}) — {e.message}"
            )

    def chat(self, messages: list, model: str = "claude-sonnet-4-6"):
        if not self.client:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set.\n"
                "Add it to your .env file and restart the app."
            )
        system, chat_messages = self._split(messages)
        kwargs = {"model": model, "max_tokens": 8096, "messages": chat_messages}
        if system:
            kwargs["system"] = system
        try:
            response = self.client.messages.create(**kwargs)
        except _sdk.AuthenticationError:
            raise RuntimeError(
                "AuthenticationError (401) — API key invalid or expired.\n"
                "→ Go to console.anthropic.com → API Keys and generate a new key,\n"
                "  then update ANTHROPIC_API_KEY in your .env file."
            )
        except _sdk.APIConnectionError:
            raise RuntimeError(
                "APIConnectionError — Could not reach api.anthropic.com.\n"
                "→ Check your internet connection.\n"
                "→ If your key is new, make sure it has been activated.\n"
                "→ Check that no firewall or VPN is blocking outbound HTTPS."
            )
        except _sdk.RateLimitError:
            raise RuntimeError(
                "RateLimitError (429) — Too many requests.\n"
                "→ Wait a moment and try again."
            )
        except _sdk.NotFoundError:
            raise RuntimeError(
                f"NotFoundError (404) — Model '{model}' does not exist.\n"
                "→ Select a different model from the dropdown."
            )
        except _sdk.APIStatusError as e:
            raise RuntimeError(
                f"APIStatusError ({e.status_code}) — {e.message}"
            )
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return response.content[0].text, usage

    def _split(self, messages: list) -> tuple[str, list]:
        system = ""
        chat = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat.append({"role": msg["role"], "content": msg["content"]})
        return system, chat
