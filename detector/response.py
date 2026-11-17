"""Replace this response-only adapter with another detector, retaining event schema."""
from typing import Protocol
import json
import re

FIELDS = ("name", "email", "phone", "birth_date", "identifier")

class ResponseDetector(Protocol):
    def inspect(self, body: bytes, metadata: dict) -> dict | None: ...

class JsonFieldDetector:
    version = "json-fields-v1"

    def inspect(self, body: bytes, metadata: dict):
        counts = {f + "_count": 0 for f in FIELDS}
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        def visit(node):
            if isinstance(node, dict):
                for key, val in node.items():
                    if key in FIELDS and isinstance(val, str):
                        valid = bool(val)
                        if key == "email":
                            valid = bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", val))
                        if key == "identifier":
                            valid = val.startswith("SYN-") and "*" not in val
                        counts[key + "_count"] += int(valid)
                    visit(val)
            elif isinstance(node, list):
                for val in node:
                    visit(val)
        visit(value)
        return {**metadata, **counts, "detector_version": self.version} if sum(counts.values()) else None
