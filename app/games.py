import json
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR


class GameCatalogue:
    def __init__(self):
        self.questions: list[dict[str, Any]] = []

    def load(self) -> None:
        path = Path(ROOT_DIR) / "data" / "games.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported game catalogue schema")
        questions = payload.get("questions", [])
        for q in questions:
            if len(q.get("options", [])) < 2 or not 0 <= q.get("answer", -1) < len(q["options"]):
                raise ValueError(f"Invalid game question: {q.get('id')}")
        self.questions = questions

    def get(self, question_id: str) -> dict[str, Any] | None:
        return next((q for q in self.questions if q["id"] == question_id), None)

    def random_question(self) -> dict[str, Any]:
        import random
        if not self.questions:
            raise RuntimeError("No game questions loaded")
        return random.choice(self.questions)
