"""Tiny BM25 retriever over markdown runbooks (swap for Chroma/FAISS in production)."""

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "has", "not", "can"}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOP]


class RunbookRetriever:
    def __init__(self, directory: Path, k1: float = 1.5, b: float = 0.75) -> None:
        self.docs: list[dict[str, Any]] = []
        for p in sorted(directory.glob("*.md")):
            raw = p.read_text()
            _, fm, body = raw.split("---", 2)
            meta = dict(line.split(": ", 1) for line in fm.strip().splitlines())
            toks = tokenize(meta["title"] + " " + body)
            self.docs.append({**meta, "body": body.strip(), "tf": Counter(toks), "len": len(toks)})
        self.k1, self.b = k1, b
        self.avg = sum(d["len"] for d in self.docs) / max(1, len(self.docs))
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d["tf"].keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        q = set(tokenize(query))
        scored = []
        for d in self.docs:
            s = 0.0
            for t in q:
                tf = d["tf"].get(t, 0)
                if tf:
                    s += (
                        self.idf[t]
                        * tf
                        * (self.k1 + 1)
                        / (tf + self.k1 * (1 - self.b + self.b * d["len"] / self.avg))
                    )
            scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return [
            {
                "id": d["id"],
                "title": d["title"],
                "category": d["category"],
                "score": round(s, 3),
                "snippet": d["body"][:240],
            }
            for s, d in scored[:k]
            if s > 0
        ]
