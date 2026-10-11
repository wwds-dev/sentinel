import json
import platform

import requests

# Meta's Muse Glimmer — 30B open-weights agentic model (Apache 2.0), released
# 2026-08-10 and tuned for tool use, long-running tasks and failure recovery.
# Ollama is one of its launch runtimes, so it needs no provider client of its
# own: it is served by the local Ollama daemon like any other local model.
#
# Ollama publishes it in several precisions. Only these two are sensible here:
#   30b-mlx      21 GB  MLX build — Apple silicon native, fastest on M-series
#   30b-q4_K_M   18 GB  GGUF 4-bit — smallest, most headroom
# The others are deliberately excluded: nvfp4 targets NVIDIA Blackwell FP4,
# mxfp8 is 33 GB, and the bf16 builds are 57-65 GB.
MUSE_GLIMMER_VARIANTS = {
    "muse-glimmer:30b-mlx": 21,
    "muse-glimmer:30b-q4_K_M": 18,
}


def muse_glimmer_default(total_ram_gb: float | None = None) -> tuple[str, int]:
    """Pick the Muse Glimmer build to install, returning (tag, size_gb).

    The MLX build is the fast path on Apple silicon, but it is 3 GB larger. On a
    machine that is already tight on memory the smaller GGUF build leaves more
    room for the OS and Sentinel itself, so prefer it under 32 GB of RAM.
    """
    is_apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"

    if is_apple_silicon and (total_ram_gb is None or total_ram_gb >= 32):
        tag = "muse-glimmer:30b-mlx"
    else:
        tag = "muse-glimmer:30b-q4_K_M"

    return tag, MUSE_GLIMMER_VARIANTS[tag]


class OllamaClient:
    # (connect, read). A dead or wedged daemon now fails in ~5 s instead of
    # hanging the agent for the full request window, while the long read
    # timeout still allows genuinely slow local generation to finish.
    CONNECT_TIMEOUT = 5
    READ_TIMEOUT = 300

    # Fallback list used when the daemon is unreachable, so the model dropdowns
    # are never empty. These are names, not a promise that they are pulled —
    # is_model_installed() is the check for that.
    KNOWN_MODELS = [
        *MUSE_GLIMMER_VARIANTS,
        "deepseek-r1:8b",
        "deepseek-r1:1.5b",
    ]

    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        self.base_url = base_url.rstrip("/")

    def list_models(self) -> list[str]:
        try:
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            return [model["name"] for model in data.get("models", [])]

        except requests.RequestException as e:
            raise RuntimeError(f"Ollama model list request failed: {e}")

    def model_details(self) -> dict[str, dict]:
        """Map installed model name -> {size_bytes, parameters, quantization}.

        /api/tags already carries this on every call; the app used to read only
        the name and throw the rest away. Returns {} if the daemon is down.
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, json.JSONDecodeError):
            return {}

        out: dict[str, dict] = {}
        for model in data.get("models", []):
            details = model.get("details") or {}
            out[model["name"]] = {
                "size_bytes": model.get("size", 0) or 0,
                "parameters": details.get("parameter_size", ""),
                "quantization": details.get("quantization_level", ""),
            }
        return out

    def model_size_bytes(self, model: str) -> int | None:
        """On-disk size of an installed model, or None if it is not installed.

        For a not-yet-pulled model the size is unknown to the daemon — callers
        fall back to a published figure (see MUSE_GLIMMER_VARIANTS).
        """
        details = self.model_details()
        for candidate in (model, f"{model}:latest", model.removesuffix(":latest")):
            if candidate in details:
                return details[candidate]["size_bytes"]
        return None

    def loaded_models(self) -> list[dict]:
        """Models currently resident in memory, from /api/ps. [] if unknown."""
        try:
            response = requests.get(f"{self.base_url}/api/ps", timeout=5)
            response.raise_for_status()
            return response.json().get("models", []) or []
        except (requests.RequestException, json.JSONDecodeError):
            return []

    def is_model_installed(self, model: str) -> bool:
        """True when `model` has actually been pulled locally.

        Matches with and without the ":latest" suffix, since Ollama reports a
        bare "name" as "name:latest" in /api/tags.
        """
        try:
            installed = self.list_models()
        except RuntimeError:
            return False

        wanted = {model, f"{model}:latest", model.removesuffix(":latest")}
        return any(name in wanted for name in installed)

    def pull_model(self, model: str, on_progress=None) -> None:
        """Download `model` into the local Ollama daemon.

        `on_progress` is called with (status, completed_bytes, total_bytes) as
        the download streams; total is 0 while Ollama is still resolving the
        manifest. Raises RuntimeError on transport failure or if Ollama reports
        an error mid-stream.

        This moves gigabytes — Muse Glimmer is ~21 GB — so callers must run it
        off the UI thread.
        """
        try:
            with requests.post(
                f"{self.base_url}/api/pull",
                json={"model": model, "stream": True},
                stream=True,
                timeout=None,
            ) as response:
                response.raise_for_status()

                for line in response.iter_lines():
                    if not line:
                        continue

                    data = json.loads(line.decode("utf-8"))

                    if "error" in data:
                        raise RuntimeError(f"Ollama pull failed: {data['error']}")

                    if on_progress is not None:
                        on_progress(
                            data.get("status", ""),
                            data.get("completed", 0),
                            data.get("total", 0),
                        )

        except requests.RequestException as e:
            raise RuntimeError(f"Ollama pull request failed: {e}")

        except json.JSONDecodeError as e:
            raise RuntimeError(f"Ollama pull JSON parse failed: {e}")

    def generate(self, model: str, prompt: str, keep_alive: str = "3m") -> str:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
            )
            response.raise_for_status()

            data = response.json()
            return data.get("response", "")

        except requests.RequestException as e:
            raise RuntimeError(f"Ollama generate request failed: {e}")

    def chat(self, model: str, messages: list[dict], keep_alive: str = "3m") -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
            )
            response.raise_for_status()

            data = response.json()
            return data.get("message", {}).get("content", "")

        except requests.RequestException as e:
            raise RuntimeError(f"Ollama chat request failed: {e}")

    def stream_chat(self, model: str, messages: list[dict], keep_alive: str = "3m"):
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive,
        }

        try:
            with requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=True,
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
            ) as response:
                response.raise_for_status()

                for line in response.iter_lines():
                    if not line:
                        continue

                    data = json.loads(line.decode("utf-8"))

                    if data.get("error"):
                        # Ollama reports a failure part-way through a stream
                        # (runner crash, out of memory) as an error line with
                        # HTTP 200; ending quietly would save a cut-off reply
                        # as a successful turn.
                        raise RuntimeError(f"Ollama stopped mid-reply: {data['error']}")

                    if "message" in data and "content" in data["message"]:
                        yield data["message"]["content"]

                    if data.get("done"):
                        break

        except requests.RequestException as e:
            raise RuntimeError(f"Ollama streaming request failed: {e}")

        except json.JSONDecodeError as e:
            raise RuntimeError(f"Ollama streaming JSON parse failed: {e}")