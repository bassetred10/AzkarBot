import json
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR


class ContentCatalogue:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT_DIR / "data" / "adhkar.json"
        self._payload: dict[str, Any] = {}

    def load(self) -> None:
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported adhkar catalogue schema")
        categories = {item["id"] for item in payload.get("categories", [])}
        seen_ids: set[str] = set()
        for item in payload.get("items", []):
            required = {"id", "category", "text", "source", "reference", "source_url"}
            if not required.issubset(item):
                raise ValueError(f"Content item is missing required source fields: {item.get('id', 'unknown')}")
            if item["category"] not in categories:
                raise ValueError(f"Unknown content category: {item['category']}")
            if item["id"] in seen_ids:
                raise ValueError(f"Duplicate content id: {item['id']}")
            if not item["source_url"].startswith("https://"):
                raise ValueError(f"Source URL must use HTTPS: {item['id']}")
            seen_ids.add(item["id"])
        self._payload = payload

    @property
    def categories(self) -> list[dict[str, Any]]:
        return self._payload.get("categories", [])

    @property
    def items(self) -> list[dict[str, Any]]:
        return self._payload.get("items", [])

    def get(self, content_id: str) -> dict[str, Any] | None:
        return next((item for item in self.items if item["id"] == content_id), None)

    def by_category(self, category_id: str) -> list[dict[str, Any]]:
        return [item for item in self.items if item["category"] == category_id]

    def public_payload(self) -> dict[str, Any]:
        # Source URLs point to the public references already attached to each catalogue entry.
        # Expose them so the Mini App can offer a direct "open source" action.
        public_items = [dict(item) for item in self.items]
        return {"categories": self.categories, "items": public_items}
