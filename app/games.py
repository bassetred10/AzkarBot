import json
import random
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR


CATEGORY_LABELS = {
    "quran": "القرآن الكريم",
    "seerah": "السيرة النبوية",
    "hadith": "الحديث النبوي",
    "prophets": "قصص الأنبياء",
    "worship": "العبادات",
    "companions": "الصحابة",
    "manners": "الأخلاق والآداب",
}
CATEGORY_ICONS = {
    "quran": "📖",
    "seerah": "🕌",
    "hadith": "📜",
    "prophets": "🌟",
    "worship": "🤲",
    "companions": "🛡️",
    "manners": "🌿",
}


class GameCatalogue:
    def __init__(self):
        self.questions: list[dict[str, Any]] = []

    def load(self) -> None:
        path = Path(ROOT_DIR) / "data" / "games.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported game catalogue schema")
        questions = payload.get("questions", [])
        seen: set[str] = set()
        for question in questions:
            question_id = question.get("id")
            options = question.get("options", [])
            answer = question.get("answer", -1)
            if not question_id or question_id in seen:
                raise ValueError(f"Missing or duplicate game question id: {question_id}")
            if len(options) < 2 or not 0 <= answer < len(options):
                raise ValueError(f"Invalid game question: {question_id}")
            if not question.get("question") or not question.get("source"):
                raise ValueError(f"Game question requires a prompt and source: {question_id}")
            category = question.get("category_id", "quran")
            if category not in CATEGORY_LABELS:
                raise ValueError(f"Unknown game category '{category}' in {question_id}")
            seen.add(question_id)
        if not questions:
            raise ValueError("Game catalogue is empty")
        self.questions = questions

    def get(self, question_id: str) -> dict[str, Any] | None:
        return next((question for question in self.questions if question["id"] == question_id), None)

    def by_category(self, category_id: str | None = None) -> list[dict[str, Any]]:
        if not category_id or category_id == "all":
            return list(self.questions)
        if category_id not in CATEGORY_LABELS:
            return []
        return [
            question for question in self.questions
            if question.get("category_id", "quran") == category_id
        ]

    def counts(self) -> dict[str, int]:
        return {
            category_id: len(self.by_category(category_id))
            for category_id in CATEGORY_LABELS
        }

    def random_question(
        self,
        category_id: str | None = None,
        exclude_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        pool = self.by_category(category_id)
        if exclude_ids:
            unused = [question for question in pool if question["id"] not in exclude_ids]
            if unused:
                pool = unused
        if not pool:
            raise RuntimeError(f"No questions available for category: {category_id or 'all'}")
        return random.choice(pool)
