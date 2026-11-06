"""The beam walker (round 18): DecisionRAG's level-wise navigation (Wan et al., arXiv 2610.35868) on the guideline graph.

Instead of one move per navigator request, clef scores all candidates of a level of the graph's tree in shared requests
(yes/no questions, about 50 per request, the requests of a level in parallel), keeps the best and descends:

  1. documents: all 204 guidelines, described by title, body, year and type; the best DOCS_KEEP stay
     (DOCS_KEEP_WIDE for questions that ask for a set, several documents or a count);
  2. pages: the pages of the kept documents, described by their headings, number of recommendations and opening line,
     plus pages from outside the beam (DecisionRAG's added candidates against irreversible pruning): the pages that name
     the question's concepts in the graph's page-concept layer, weighted by how few pages name a concept, and the best
     BM25 pages of the whole library for the question's own words; the best PAGES_KEEP stay;
  3. pieces: the recommendations and lines of the kept pages, each scored; those at or above TAKE are the evidence.

The writer, the answer check and the grade guard are those of the frozen walker (`Walk.write_answer`). When the writer
finds the evidence incomplete, the beam is widened with the missing part as new words for the concept and BM25
candidates, up to ROUNDS times. No move-by-move navigation and no OPEN/BACK.
"""
import math
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .agent import Walk, _similar, grade_of_row, grade_text, strength_text
from .models import ModelError, classify
from .text import cut, lines_of, words_of

BATCH = 50  # yes/no questions per request (clef takes up to 64)
DOCS_KEEP, DOCS_KEEP_WIDE = 3, 6
PAGES_KEEP, PAGES_KEEP_WIDE = 5, 10
EXTRA_PAGES = 6  # concept and BM25 candidates added from outside the beam, each
TAKE = 0.6
PIECES_MAX, PIECES_MAX_WIDE = 12, 40  # evidence kept for the writer
LINES_PER_PAGE = 80
ROUNDS = 3  # answer attempts: the first and up to two widened ones
CONCEPT_MAX_PAGES = 150  # a concept named on more pages says little (and the layer has mislinks such as "patients")

DOC_Q = ("Could this clinical practice guideline contain the answer to the question, or a part of it? Judge by its "
         "title, issuing body, year and type.")
PAGE_Q = ("Could this page of a clinical practice guideline contain the answer to the question, or a part of it? Judge "
          "by its guideline, headings, number of recommendations and opening line.")
PIECE_Q = ("Does this piece of a clinical practice guideline state an answer element for the situation, group and "
           "guideline the question names (a recommendation with its class or level, a value, a criterion, or an element "
           "of the asked list)?")
YES_NO = {"true": "Yes: it may hold what the question asks for.", "false": "No: it is about something else."}
PIECE_YES_NO = {"true": "Yes: it states an answer element for the asked situation.",
                "false": "No: it is about another topic, group, situation or guideline, or does not state what is asked."}


class ConceptIndex:
    """Label of a concept -> pages that name it (the 18.10 page-concept layer), for matching the words of a question."""

    def __init__(self, links: list[dict]):
        self.pages: dict[tuple, dict[str, int]] = defaultdict(dict)
        for r in links:
            key = tuple(words_of(r["label"]))
            if key:
                self.pages[key][r["page"]] = self.pages[key].get(r["page"], 0) + r["mentions"]
        self.n_pages = len({p for d in self.pages.values() for p in d}) or 1
        self.max_len = max((len(k) for k in self.pages), default=1)

    def match(self, text: str) -> list[tuple[str, ...]]:
        """The concept labels that occur as a whole word sequence in the text (single words of at least four letters)."""
        w = words_of(text)
        found = []
        for n in range(min(self.max_len, 6), 0, -1):
            for i in range(len(w) - n + 1):
                key = tuple(w[i:i + n])
                if key in self.pages and (n > 1 or len(key[0]) >= 4) and len(self.pages[key]) <= CONCEPT_MAX_PAGES:
                    found.append(key)
        return list(dict.fromkeys(found))

    def ranked_pages(self, text: str, limit: int) -> list[tuple[str, float, list[str]]]:
        """Pages scored by the rarity of the matched concepts they name (idf times log of the mention count)."""
        score: dict[str, float] = defaultdict(float)
        why: dict[str, list[str]] = defaultdict(list)
        for key in self.match(text):
            idf = math.log(self.n_pages / len(self.pages[key]))
            for page, m in self.pages[key].items():
                score[page] += idf * math.log(1 + m)
                why[page].append(" ".join(key))
        return [(p, round(s, 2), why[p]) for p, s in sorted(score.items(), key=lambda kv: -kv[1])[:limit]]


@dataclass
class BeamWalk(Walk):
    concepts: ConceptIndex | None = None
    requests: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- one level: score candidates in parallel batches -----------------------------------------------------------
    def score(self, level: str, task: str, items: list[tuple[str, str]], criteria: dict) -> dict[str, float]:
        """Probability per candidate key that it fits; candidates in batches of BATCH, the batches in parallel."""
        batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]

        def one(batch):
            questions = {f"c{n}": {"type": "bool", "instructions": f"{task} Candidate: {text}", "criteria": criteria}
                         for n, (_, text) in enumerate(batch)}
            answers, usage = classify({"question": self.question, "level": level}, questions)
            with self._lock:
                self.usd += usage["usd"]
                self.requests += 1
                self.trace.append({"type": "beam", "level": level, "candidates": len(batch), "usage": usage})
            out = {}
            for n, (key, _) in enumerate(batch):
                a = answers.get(f"c{n}") or {}
                prob = a.get("noul", a.get("probability"))
                if isinstance(prob, (int, float)):
                    out[key] = float(prob)
            return out

        probs: dict[str, float] = {}
        if batches:
            with ThreadPoolExecutor(min(8, len(batches))) as ex:
                for part in ex.map(one, batches):
                    probs.update(part)
        return probs

    # --- the three levels ----------------------------------------------------------------------------------------
    def documents_level(self) -> list[str]:
        docs = self.graph.documents()
        items = [(d["id"], f'{d["title"]} ({", ".join(str(x) for x in (d.get("body"), d.get("year"), d.get("type")) if x)}; {d.get("pages", "?")} pages)')
                 for d in docs]
        probs = self.score("documents", DOC_Q, items, YES_NO)
        keep = DOCS_KEEP_WIDE if self.aggregate else DOCS_KEEP
        kept = [k for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])[:keep]]
        self.trace.append({"type": "level", "level": "documents", "kept": kept, "p": {k: round(probs[k], 3) for k in kept}})
        return kept

    def extra_pages(self, text: str) -> dict[str, str]:
        """Pages from outside the beam: concept matches and BM25 over the whole library, with why they were added."""
        out: dict[str, str] = {}
        if self.concepts:
            for page, s, why in self.concepts.ranked_pages(text, EXTRA_PAGES):
                out[page] = f"names {', '.join(why[:3])}"
        for h in self.index.search(text, None, EXTRA_PAGES):
            out.setdefault(h["page"], f"matches the words ({h['holds']})")
        return out

    def pages_level(self, docs: list[str], text: str, exclude: set[str]) -> list[str]:
        cands: dict[str, str] = {}
        for d in docs:
            title = self.doc_title(d)
            for e in self.graph.toc(d):
                if e["page"] not in exclude:
                    heads = "; ".join(e["headings"]) or "no headings"
                    cands[e["page"]] = f'{title}, page {e["number"]}: {heads}; {e["recommendations"]} recommendations; begins "{e["opening"]}"'
        added = {p: why for p, why in self.extra_pages(text).items() if p not in exclude}
        for p, why in added.items():
            if p not in cands:
                pg = self.graph.page(p)
                heads = "; ".join(pg["headings"]) or "no headings"
                cands[p] = f'{pg["documentTitle"]}, page {pg["number"]}: {heads}; {len(pg["recommendations"])} recommendations'
            cands[p] += f" [{why}]"
        probs = self.score("pages", PAGE_Q, list(cands.items()), YES_NO)
        keep = PAGES_KEEP_WIDE if self.aggregate else PAGES_KEEP
        kept = [k for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])[:keep]]
        self.trace.append({"type": "level", "level": "pages", "candidates": len(cands), "added": list(added),
                           "kept": kept, "p": {k: round(probs[k], 3) for k in kept}})
        return kept

    def pieces(self, page_id: str) -> list[dict]:
        """The takeable pieces of a page, in the same form as the walker's TAKE (recommendations, table rows, lines)."""
        p = self.graph.page(page_id)
        base = {"page": p["id"], "number": p["number"], "document": p["document"], "documentTitle": p["documentTitle"]}
        out = [{**base, "kind": "recommendation", "key": r["id"], "text": r["text"], "cor": r["cor"], "loe": r["loe"], "about": r["about"],
                "modality": r.get("modality", ""), "polarity": r.get("polarity", "")} for r in p["recommendations"]]
        lines = [l for l in lines_of(p["text"]) if not any(_similar(l, r["text"]) > 0.8 for r in p["recommendations"])
                 and not re.match(r"^\(?continued\)?$", l.strip("* ."), re.I)]
        for i, line in enumerate(lines[:LINES_PER_PAGE]):
            cor, loe = grade_of_row(line)
            out.append({**base, "kind": "table row" if cor or loe else "line", "key": f'{p["id"]}#line{i}', "text": line, "cor": cor, "loe": loe,
                        "about": "", "context": lines[i - 1] if i > 0 else ""})
        return out

    def pieces_level(self, pages: list[str]) -> int:
        taken = {e["key"] for e in self.evidence}
        cands = [x for pg in pages for x in self.pieces(pg) if x["key"] not in taken]
        for pg in pages:
            self.read_pages.add(pg)

        def describe(x):
            grade = f'{grade_text(x["cor"], x["loe"])}; {strength_text(x)}' if x["kind"] == "recommendation" else (
                f'table row, {grade_text(x["cor"], x["loe"])}' if x["kind"] == "table row" else "line of text")
            context = f' (after: {cut(x["context"], 120)})' if x.get("context") else ""
            return f'{x["documentTitle"]}, page {x["number"]}, {grade}: {cut(x["text"], 400)}{context}'
        probs = self.score("pieces", PIECE_Q, [(x["key"], describe(x)) for x in cands], PIECE_YES_NO)
        limit = PIECES_MAX_WIDE if self.aggregate else PIECES_MAX
        best = sorted((x for x in cands if probs.get(x["key"], 0) >= TAKE), key=lambda x: -probs[x["key"]])[:limit - len(self.evidence)]
        self.evidence += best
        self.trace.append({"type": "level", "level": "pieces", "candidates": len(cands), "taken": [x["key"] for x in best]})
        return len(best)

    # --- the walk ----------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        try:
            self.classify_question()
            docs = self.documents_level()
            text = self.question
            seen: set[str] = set()
            for attempt in range(ROUNDS):
                pages = self.pages_level(docs if attempt == 0 else [], text, seen)
                seen |= set(pages)
                self.pieces_level(pages)
                out = self.write_answer(final=attempt == ROUNDS - 1)
                if "finish" in out:
                    self.finish(out["finish"], out["answer"], out["answer_items"], out["count"])
                    break
                text = f"{self.question} {self.missing}"  # widen with what the writer says is missing
        except ModelError as e:
            self.trace.append({"type": "error", "error": str(e)})
            self.finish("error", f"The walk stopped: {e}")
        if self.finished is None:
            self.finish("not covered", "No guideline statement answering the question was found.")
        return {**self.finished, "moves": self.requests, "usd": round(self.usd, 5)}
