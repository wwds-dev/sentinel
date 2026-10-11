import json  # used for saving history as JSON
from pathlib import Path  # makes folder paths easier
from datetime import datetime  # used to generate timestamped filenames
from uuid import uuid4
from services.runtime_paths import user_data_base


class HistoryStore:
    def __init__(self, folder: str | Path | None = None):
        self.folder = Path(folder) if folder is not None else user_data_base() / "data" / "chats"
        self.folder.mkdir(parents=True, exist_ok=True)  # create the folder if needed

    def save_chat(self, agent: str, backend: str, model: str, command: str,
                  messages: list, response: str, project: str | None = None):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        filepath = self.folder / f"{timestamp}_{uuid4().hex}.json"

        payload = {
            "timestamp": timestamp,
            "agent": agent,
            "backend": backend,
            "model": model,
            "command": command,
            "messages": messages,
            "response": response
        }
        if project:
            payload["project"] = project

        # Exclusive creation prevents an accidental overwrite even if clocks
        # are frozen or UUID generation is replaced in a test.
        with open(filepath, "x", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)  # save nicely formatted JSON

        return filepath

    def update_chat(self, filepath: str | Path, *, messages: list, response: str,
                    backend: str | None = None, model: str | None = None,
                    command: str | None = None, project: str | None = None) -> Path:
        """Rewrite an existing saved chat with the conversation so far.

        One conversation is one file: save_chat() creates it on the first
        turn and this keeps it current on every later turn. Before this, every
        turn wrote a new snapshot, so a ten-turn chat showed up ten times under
        the same title, and a rename or a project move landed on one of them.
        The title the user gave it and its original timestamp are kept.
        """
        path = Path(filepath)
        payload = self.load_chat(str(path))
        payload["messages"] = messages
        payload["response"] = response
        payload["updated"] = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        if backend:
            payload["backend"] = backend
        if model:
            payload["model"] = model
        if command:
            payload["command"] = command
        if project is not None:
            if project:
                payload["project"] = project
            else:
                payload.pop("project", None)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
        return path

    def list_chats(self):
        return sorted(self.folder.glob("*.json"), reverse=True)  # newest first

    def load_chat(self, filepath: str):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)  # read a saved chat back into Python
