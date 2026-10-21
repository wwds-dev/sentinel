"""LM Studio client — discovery and chat for the local `lmstudio` provider.

Byte-identical in Sentinel and Imprint (`tests/test_providers_drift.py`).

Server on:   GET {root}/api/v0/models lists every downloaded model with its
             state (loaded / not-loaded), quantisation, architecture and
             maximum context. Chat goes through the OpenAI-compatible
             /v1/chat/completions. LM Studio loads a model on first request
             (JIT), so a model that is only on disk still works; it just
             starts slower.
Server off:  ~/.lmstudio/models is read instead, and every model is listed
             as "on disk · server off". `start_server()` runs
             `lms server start`.

Only localhost is contacted and no API key is ever sent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import requests

from services import providers

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 300

STATE_LOADED = "loaded"
STATE_IDLE = "not-loaded"
STATE_DISK = "on disk · server off"

_QUANT = re.compile(
    r"(?<![A-Za-z0-9])((?:i1-)?(?:IQ\d(?:_[A-Z0-9]+)*|Q\d(?:_[A-Z0-9]+)*|"
    r"F16|F32|BF16|MXFP4))(?![A-Za-z0-9])", re.I)


def models_dir() -> Path:
    """Where LM Studio keeps downloads (LMSTUDIO_MODELS_DIR overrides)."""
    override = os.environ.get("LMSTUDIO_MODELS_DIR", "").strip()
    return Path(override).expanduser() if override else (
        Path.home() / ".lmstudio" / "models")


def quant_of(filename: str) -> str:
    """'Q6_K' from 'DeepSeek-R1-…-abliterated.i1-Q6_K.gguf', else ''."""
    stem = Path(filename).stem
    found = _QUANT.findall(stem)
    return found[-1].upper().replace("I1-", "") if found else ""


def scan_disk(root: Path | None = None) -> list[dict]:
    """Every .gguf under publisher/repo/, as model dicts with state on-disk.

    The id is `publisher/repo/file.gguf`, the path LM Studio itself loads by.
    Split shards (…-00002-of-00003.gguf) are skipped after the first.
    """
    base = Path(root) if root is not None else models_dir()
    found: list[dict] = []
    if not base.is_dir():
        return found
    for path in sorted(base.glob("*/*/*.gguf")):
        if path.name.lower().startswith("mmproj"):
            continue          # a vision projector rides along with a model; it is not one
        shard = re.search(r"-(\d+)-of-\d+\.gguf$", path.name)
        if shard and int(shard.group(1)) > 1:
            continue
        publisher, repo = path.parent.parent.name, path.parent.name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        found.append({
            "id": f"{publisher}/{repo}/{path.name}",
            "name": re.sub(r"[-_.]?GGUF$", "", repo, flags=re.I),
            "publisher": publisher,
            "quant": quant_of(path.name),
            "size_bytes": size,
            "state": STATE_DISK,
            "arch": "",
            "max_context": None,
            "path": str(path),
        })
    found.extend(_scan_mlx(base))
    return found


def _scan_mlx(base: Path) -> list[dict]:
    """MLX models: a folder `publisher/repo/` of config.json and .safetensors.

    They have no .gguf, so the scan above cannot see them. The size is the sum
    of the weight shards and the quantisation comes from config.json
    (`quantization.bits`), else from the folder name ("MLX-4bit").
    """
    found = []
    for folder in sorted(p for p in base.glob("*/*") if p.is_dir()):
        shards = list(folder.glob("*.safetensors"))
        if not shards or not (folder / "config.json").is_file():
            continue
        try:
            size = sum(p.stat().st_size for p in shards)
        except OSError:
            continue
        bits = None
        try:
            bits = (json.loads((folder / "config.json").read_text(encoding="utf-8"))
                    .get("quantization") or {}).get("bits")
        except (OSError, ValueError, AttributeError):
            pass
        if not bits:
            named = re.search(r"(\d+)[-_ ]?bit", folder.name, re.I)
            bits = named.group(1) if named else None
        found.append({
            "id": f"{folder.parent.name}/{folder.name}",
            "name": re.sub(r"[-_.]?MLX(?:[-_.]?\d+bit)?$", "", folder.name, flags=re.I)
            + " (MLX)",
            "publisher": folder.parent.name,
            "quant": f"{bits}BIT" if bits else "",
            "size_bytes": size,
            "state": STATE_DISK,
            "arch": "mlx",
            "max_context": None,
            "path": str(folder),
        })
    return found


def lms_binary() -> str | None:
    """The `lms` command line tool, on PATH or in LM Studio's own folder."""
    found = shutil.which("lms")
    if found:
        return found
    candidate = Path.home() / ".lmstudio" / "bin" / "lms"
    return str(candidate) if candidate.is_file() else None


def running_port(timeout: float = 5.0) -> int | None:
    """The port LM Studio's server is really on, from `lms server status`.

    1234 is only LM Studio's default: the server can be set to another port
    (here it came up on 1235), and then asking 1234 reads as "server off".
    None when `lms` is missing, the server is off or the output is unreadable.
    """
    lms = lms_binary()
    if lms is None:
        return None
    try:
        done = subprocess.run([lms, "server", "status"], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"running on port (\d+)", (done.stdout or "") + (done.stderr or ""))
    return int(found.group(1)) if found else None


class LMStudioClient:
    def __init__(self, base_url: str | None = None):
        # base_url is the /v1 root; the management API sits beside it.
        self.base_url = (base_url or providers.lmstudio_url()).rstrip("/")
        # An address set by the caller or LMSTUDIO_URL is the user's word; only
        # the default may be corrected from what the server reports.
        self._pinned = bool(base_url or os.environ.get("LMSTUDIO_URL", "").strip())
        self.root = self.base_url[:-3] if self.base_url.endswith("/v1") \
            else self.base_url

    # ── discovery ───────────────────────────────────────────────────────────
    def _answers(self) -> bool:
        try:
            requests.get(f"{self.base_url}/models",
                         timeout=CONNECT_TIMEOUT).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def server_up(self) -> bool:
        """Is the server answering? Follows it to its real port when the
        default one is silent (see `running_port`). Blocking: not for the UI thread."""
        if self._answers():
            return True
        if self._pinned:
            return False
        port = running_port()
        if port is None:
            return False
        self.base_url = f"http://localhost:{port}/v1"
        self.root = self.base_url[:-3]
        return self._answers()

    def model_details(self) -> list[dict]:
        """Chat-capable models, from the server when it is up, else the disk.

        Embedding models are dropped: they cannot answer a prompt.
        """
        try:
            response = requests.get(f"{self.root}/api/v0/models",
                                    timeout=CONNECT_TIMEOUT)
            response.raise_for_status()
            rows = response.json().get("data", [])
        except (requests.RequestException, ValueError):
            return scan_disk()
        models = []
        for row in rows:
            if str(row.get("type", "llm")) in {"embeddings", "embedding"}:
                continue
            ident = str(row.get("id", ""))
            models.append({
                "id": ident,
                "name": ident,
                "publisher": str(row.get("publisher", "")),
                "quant": str(row.get("quantization", "")).upper(),
                "size_bytes": None,
                "state": STATE_LOADED if row.get("state") == "loaded"
                else STATE_IDLE,
                "arch": str(row.get("arch", "")),
                "max_context": row.get("max_context_length"),
            })
        self._attach_sizes(models)
        return models

    def _attach_sizes(self, models: list[dict]) -> None:
        """The server does not report file size; the disk scan does."""
        disk = scan_disk()
        squash = lambda text: re.sub(r"[^a-z0-9]", "", text.casefold())  # noqa: E731
        for model in models:
            # The server's id may carry a different publisher than the folder
            # ("qwen/qwen3.8-27b" is stored under lmstudio-community), so the
            # bare repo name is tried when the full id finds nothing.
            keys = [squash(model["id"]), squash(model["id"].rsplit("/", 1)[-1])]
            for key in dict.fromkeys(k for k in keys if k):
                hit = next((i for i in disk if key in squash(i["id"])), None)
                if hit:
                    model["size_bytes"] = hit["size_bytes"]
                    model["path"] = hit["path"]
                    break

    def list_models(self) -> list[str]:
        return [m["id"] for m in self.model_details()]

    def model_size_bytes(self, model: str) -> int | None:
        for item in self.model_details():
            if item["id"] == model:
                return item["size_bytes"]
        return None

    @staticmethod
    def start_server() -> tuple[bool, str]:
        """Run `lms server start`. Returns (started, message)."""
        lms = lms_binary()
        if lms is None:
            return False, ("LM Studio's `lms` tool was not found. Open LM "
                           "Studio once, then run `lms bootstrap`.")
        try:
            done = subprocess.run([lms, "server", "start"], capture_output=True,
                                  text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"`lms server start` failed: {exc}"
        text = (done.stdout or done.stderr or "").strip()
        return done.returncode == 0, text or "LM Studio server started."

    # ── chat ────────────────────────────────────────────────────────────────
    def _payload(self, model: str, messages: list[dict], stream: bool) -> dict:
        return {"model": model, "messages": messages, "stream": stream}

    def chat(self, model: str, messages: list[dict], **_ignored) -> str:
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=self._payload(model, messages, False),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"] or ""
        except requests.RequestException as exc:
            raise RuntimeError(f"LM Studio request failed: {exc}")
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(f"LM Studio sent an unreadable reply: {exc}")

    def stream_chat(self, model: str, messages: list[dict], **_ignored):
        try:
            with requests.post(
                    f"{self.base_url}/chat/completions",
                    json=self._payload(model, messages, True), stream=True,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
                response.raise_for_status()
                for raw in response.iter_lines():
                    if not raw:
                        continue
                    line = raw.decode("utf-8")
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    data = json.loads(body)
                    if data.get("error"):
                        raise RuntimeError(
                            f"LM Studio stopped mid-reply: {data['error']}")
                    delta = (data.get("choices") or [{}])[0].get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
        except requests.RequestException as exc:
            raise RuntimeError(f"LM Studio streaming request failed: {exc}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LM Studio streaming JSON parse failed: {exc}")

    def generate(self, prompt: str, model: str, **_ignored) -> str:
        return self.chat(model, [{"role": "user", "content": prompt}])

    def key_available(self) -> bool:
        return True            # local; there is no key
