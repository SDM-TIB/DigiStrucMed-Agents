"""Full-text search over the graph's page text (`i:originalOcr`): Okapi BM25, in one guideline or the whole library.

The query is written by the text model when the navigator chooses SEARCH, as jev-ultrafast's small LLM writes a
field's value for TYPE_TEXT. Each hit shows the page's best-matching recommendation with its grade, or else its
best-matching line, so the navigator sees what a page holds before opening it.
"""
import math
from collections import Counter

from .text import cut, lines_of, words_of

STOP = set("""a an and are as at be by for from has have how in is it its of on or that the this to was were what when
which with within without who whom why will would should can may""".split())
K1, B = 1.2, 0.75


def terms(text: str) -> list[str]:
    return [w for w in words_of(text) if w not in STOP and (len(w) > 1 or w.isdigit())]


class Index:
    def __init__(self, pages: list[dict], recommendations: list[dict]):
        self.pages = pages
        self.tf = [Counter(terms(p["text"])) for p in pages]
        self.length = [sum(c.values()) for c in self.tf]
        self.avg = sum(self.length) / max(1, len(self.length))
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(pages)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.recs: dict[str, list[dict]] = {}
        for r in recommendations:
            self.recs.setdefault(r["page"], []).append(r)
        # a second BM25 index over the recommendations themselves, for collecting across guidelines
        self.rec_list = recommendations
        self.rec_tf = [Counter(terms(r["text"])) for r in recommendations]
        self.rec_len = [sum(c.values()) for c in self.rec_tf]
        self.rec_avg = sum(self.rec_len) / max(1, len(self.rec_len))
        rdf = Counter()
        for c in self.rec_tf:
            rdf.update(c.keys())
        m = len(recommendations)
        self.rec_idf = {t: math.log(1 + (m - f + 0.5) / (f + 0.5)) for t, f in rdf.items()}

    def search_recommendations(self, query: str, document: str | None = None, limit: int = 60) -> list[dict]:
        """The recommendations (of the document, or of all) whose text best matches the query, with their page."""
        q = list(dict.fromkeys(terms(query)))
        out = []
        for i, r in enumerate(self.rec_list):
            if document is not None and not r["page"].startswith(document.replace("/guideline/", "/page/")):
                continue
            tf, norm = self.rec_tf[i], K1 * (1 - B + B * self.rec_len[i] / self.rec_avg)
            score = sum(self.rec_idf.get(t, 0) * tf[t] * (K1 + 1) / (tf[t] + norm) for t in q if tf[t])
            if score > 0:
                out.append((score, i))
        return [{**self.rec_list[i], "score": round(sc, 2)} for sc, i in sorted(out, key=lambda x: -x[0])[:limit]]

    def title_of(self, page: str) -> str:
        titles = getattr(self, "_titles", None)
        if titles is None:
            titles = self._titles = {p["page"]: p["documentTitle"] for p in self.pages}
        return titles.get(page, "")

    def score(self, i: int, query: list[str]) -> float:
        tf, norm = self.tf[i], K1 * (1 - B + B * self.length[i] / self.avg)
        return sum(self.idf.get(t, 0) * tf[t] * (K1 + 1) / (tf[t] + norm) for t in query if tf[t])

    def best_line(self, page: dict, query: list[str]) -> str:
        """The page's recommendation (with grade) or line that holds most of the query's terms."""
        q = set(query)

        def overlap(text):
            return len(q & set(terms(text)))

        recs = self.recs.get(page["page"], [])
        rec = max(recs, key=lambda r: overlap(r["text"]), default=None)
        line = max(lines_of(page["text"]), key=overlap, default="")
        if rec is not None and overlap(rec["text"]) >= overlap(line):
            grade = f"class {rec['cor'] or '-'}, level {rec['loe'] or '-'}" if rec["cor"] or rec["loe"] else "no grade"
            return f'recommendation ({grade}): {cut(rec["text"], 260)}'
        return f"line: {cut(line, 260)}"

    def search(self, query: str, document: str | None = None, limit: int = 20) -> list[dict]:
        q = list(dict.fromkeys(terms(query)))
        scored = [(self.score(i, q), i) for i, p in enumerate(self.pages) if document is None or p["document"] == document]
        top = sorted((x for x in scored if x[0] > 0), key=lambda x: -x[0])[:limit]
        return [{**{k: self.pages[i][k] for k in ("page", "document", "documentTitle", "number")},
                 "score": round(s, 2), "best": self.best_line(self.pages[i], q),
                 "holds": f"holds {sum(1 for t in q if self.tf[i][t])} of the query's {len(q)} words"} for s, i in top]

    def absent(self, query: str, document: str | None = None) -> list[str]:
        """The query's words that no page (of the document) holds: a reader then knows the library does not cover them."""
        scope = [i for i, p in enumerate(self.pages) if document is None or p["document"] == document]
        return [t for t in dict.fromkeys(terms(query)) if not any(self.tf[i][t] for i in scope)]
