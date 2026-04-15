"""Guide loader for MatCreator.

Guides are Markdown files with YAML frontmatter stored in _GUIDES_DIR.
Each guide exposes .name, .description, .instructions (body), and .metadata.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .constants import _GUIDES_DIR

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Guide:
    name: str
    description: str
    instructions: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: pathlib.Path) -> "Guide":
        text = path.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if m:
            front = yaml.safe_load(m.group(1)) or {}
            body = text[m.end():]
        else:
            front = {}
            body = text
        return cls(
            name=front.get("name", path.stem),
            description=front.get("description", ""),
            instructions=body.strip(),
            metadata=front,
        )


def load_guides() -> list[Guide]:
    """Load all guides from _GUIDES_DIR that are .md files."""
    guides_root = pathlib.Path(_GUIDES_DIR)
    guides = []
    for guide_file in sorted(guides_root.glob("*.md")):
        guides.append(Guide.from_file(guide_file))
    return guides


ALL_GUIDES = load_guides()
