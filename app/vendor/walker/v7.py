"""Walker V7: clef walks the proposition graph (experiments/2026-11-05_proposition_graph), no page reading.

Levels, each scored by clef-flash in one pass (DecisionRAG-style level scoring, as V5):
  1. guidelines: the 204 document nodes (V5's document level);
  2. topics (only for a guideline with more than TOPIC_FROM propositions): the guideline's proposition contexts, the
     topics the decomposition gave every proposition; the best TOPICS_KEEP topics stay;
  3. propositions: every proposition of the kept guidelines (of the kept topics for large ones), each a
     self-contained statement with its kind, class and level; the fitting ones become evidence with their verbatim
     source quote;
  beside it the 28.10 recommendation nodes of the kept guidelines (V5's statement level, graded statements and table
  rows with repaired merged cells).
No page text, no text windows, no embeddings, no vocabulary call. Widening rounds take the next guidelines of the
ranking and the graded versions of the statements taken (V5).
"""
import glob
import json
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .agent import grade_text
from .models import ModelError
from .text import cut
from .v5 import BODIES, DOCS_KEEP, DOCS_KEEP_WIDE, TASK, CRITERIA, EVIDENCE_MAX, EVIDENCE_MAX_WIDE, FLOOR, MIN_STATEMENTS, PIECES_MAX, PIECES_MAX_WIDE, ROUNDS, TAKE, WalkV5

PROPS = Path(os.environ.get("V7_PROPS", Path(__file__).resolve().parents[2] / "2026-11-05_proposition_graph"))
TOPIC_FROM = 120  # a guideline with more propositions is entered through its topic tree
TOPICS_KEEP = 3
DOC_TOPICS = int(os.environ.get("V7_DOC_TOPICS", 0))  # topic names per guideline at the document level (off by default)
TOPIC_FLOOR = 0.3
PER_DOC_TAKE = 6  # the best propositions of each kept guideline reach the writer before the rest (comparisons)
PROPS_MAX_PER_DOC = 600  # safety bound on the propositions of one guideline that reach the scorer
USE_STATEMENTS = os.environ.get("V7_STATEMENTS", "1") == "1"
RERANK = os.environ.get("V7_RERANK", "@cf/cloudflare/clef")  # second, stronger judge over the first judge's best
RERANK_N = int(os.environ.get("V7_RERANK_N", 150))  # how many of the first judge's best the second judge reads
FLASH_TASK = os.environ.get("V7_FLASH_TASK", "pieces")  # "answers": the first judge gets the strict question too
TASK.setdefault("answers", "Does this statement itself state what the question asks for (the asked value, threshold, interval, "
                           "class, level, criterion or fact) for the situation and group the question names? A statement on a "
                           "related subject that does not state the asked element does not fit. When the question assumes a "
                           "class, level or fact, the guideline's own statement on that exact subject fits even if it says "
                           "something different.")
TASK.setdefault("topics", "Which topics of this guideline could contain the answer to the question, or a part of it? Judge each "
                          "candidate topic by its name and description.")


def _ctx(x: str) -> str:
    return re.sub(r"\s+", " ", str(x or "").strip().rstrip(".")).lower()[:80] or "general"


class PropStore:
    """The proposition layer in memory: per guideline its propositions, grouped by topic (context)."""

    def __init__(self, root: Path):
        rows = {}
        for f in sorted(glob.glob(str(root / "work/props_*.jsonl"))):
            for line in open(f, encoding="utf-8"):
                r = json.loads(line)
                rows.setdefault(r["page"], r)
        self.by_doc: dict[str, list[dict]] = defaultdict(list)
        for pg, r in rows.items():
            for n, x in enumerate(r["propositions"]):
                self.by_doc[r["document"]].append({
                    "key": f"{pg}#prop{n}", "page": pg, "document": r["document"], "number": r["number"],
                    "text": str(x.get("text") or "").strip(), "kind": str(x.get("kind") or "other").lower(),
                    "cls": str(x.get("class") or "").strip(), "lvl": str(x.get("level") or "").strip(),
                    "population": str(x.get("population") or "").strip(), "context": _ctx(x.get("context")),
                    "quote": str(x.get("quote") or "").strip()})

        # the topic tree (build_topics.py): per guideline named topics with a description, each grouping contexts
        self.tree: dict[str, list[dict]] = {}
        tfile = root / "work/topics.jsonl"
        for line in (open(tfile, encoding="utf-8") if tfile.exists() else []):
            t = json.loads(line)
            self.tree[t["document"]] = t["topics"]

    def topics(self, doc: str) -> dict[str, tuple[str, list[dict]]]:
        """Topic name -> (description, propositions); contexts outside the tree go to "other"."""
        of = {}
        for tp in self.tree.get(doc, []):
            for c in tp["contexts"]:
                of[c] = tp["name"]
        desc = {tp["name"]: tp.get("description", "") for tp in self.tree.get(doc, [])}
        out = defaultdict(list)
        for p in self.by_doc.get(doc, []):
            out[of.get(p["context"], "other")].append(p)
        return {name: (desc.get(name, ""), ps) for name, ps in out.items()}


@dataclass
class WalkV7(WalkV5):
    _store = None
    precision_hint: bool = os.environ.get("V7_PRECISION", "1") == "1"  # writer: the exact asked value, never a neighbouring one

    @property
    def store(self) -> PropStore:
        if WalkV7._store is None:
            WalkV7._store = PropStore(PROPS)
        return WalkV7._store

    def vocabulary(self) -> str:
        return ""

    def emit(self, **ev) -> None:
        cb = getattr(self, "on_event", None)  # live view (TIB Chat Graph Walker)
        if cb:
            cb(ev)

    def classify_question(self) -> None:
        super().classify_question()
        q = next((t for t in reversed(self.trace) if t.get("type") == "question"), {})
        self.emit(t="question", aggregate=q.get("aggregate"), p=q.get("p"))

    def dense_windows(self, k: int = 20) -> list[dict]:
        return []

    def evidence_text(self, e: dict, limit: int = 400) -> str:
        if e.get("kind") != "statement":
            return super().evidence_text(e, limit)
        grade = f' ({grade_text(e["cor"], e["loe"])})' if (e.get("cor") or e.get("loe")) else " (no class or level stated)"
        src = f' [source text: "{cut(e["quote"], 400)}"]' if e.get("quote") else ""
        unfit = " [the answer check judged this piece as not fitting the question: do not use it]" if e.get("unfit") else ""
        return f'{e["documentTitle"]}, page {e["number"]}, {e["proposition_kind"]}{grade}: {cut(e["text"], limit)}{src}{unfit}'

    # --- guidelines, described by their topic tree ----------------------------------------------------------------
    def documents_level(self) -> list[str]:
        """V5's document level, with each guideline described also by the names of its largest topics (the topic tree
        of the proposition graph), so a guideline whose title says little ("Empfehlungen und Stellungnahmen") is
        judged by what it covers."""
        topics = self.document_topics()

        def describe(d):
            body = d.get("body") or ""
            body = ", ".join(BODIES.get(b.strip(), b.strip()) for b in re.split(r"[/,]", body) if b.strip()) if body else ""
            topic, lang = topics.get(d["id"], ("", ""))
            tree = self.store.topics(d["id"])
            names = [t for t, _ in sorted(tree.items(), key=lambda kv: -len(kv[1][1])) if t != "other" and t != d["title"]][:DOC_TOPICS]
            facts = "; ".join(x for x in (body, str(d.get("year") or ""), f"topic: {topic}" if topic else "",
                                           f"covers: {', '.join(names)}" if names else "", "in German" if lang == "de" else "") if x)
            return f'{d["title"]} ({cut(facts, 420)})'
        items = [(d["id"], describe(d)) for d in self.graph.documents()]
        probs = self.score("documents", "", items, CRITERIA)
        keep = DOCS_KEEP_WIDE if self.aggregate else DOCS_KEEP
        self.doc_ranking = [k for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])]
        kept = self.doc_ranking[:keep]
        self.trace.append({"type": "level", "level": "documents", "kept": kept, "p": {k: round(probs[k], 3) for k in kept}})
        return kept

    # --- topics of a large guideline --------------------------------------------------------------------------------
    def topics_level(self, doc: str) -> list[dict]:
        topics = self.store.topics(doc)
        items = [(f"{doc}|{t}", f'{cut(self.doc_title(doc), 50)}: topic "{t}": {cut(desc, 220) or cut(ps[0]["text"], 160)} ({len(ps)} statements)')
                 for t, (desc, ps) in topics.items()]
        probs = self.score("topics", "", items, CRITERIA)
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])
        kept = [k.split("|", 1)[1] for k, v in ranked[:TOPICS_KEEP] if v >= TOPIC_FLOOR] or [ranked[0][0].split("|", 1)[1]] if ranked else []
        self.trace.append({"type": "level", "level": "topics", "document": doc, "candidates": len(items), "kept": kept,
                           "p": {k.split("|", 1)[1]: round(v, 3) for k, v in ranked[:6]}})
        return [p for t in kept for p in topics[t][1]]

    # --- propositions ---------------------------------------------------------------------------------------------
    def propositions_level(self, docs: list[str]) -> int:
        taken = {e["key"] for e in self.evidence}
        cands = []
        for d in docs:
            ps = self.store.by_doc.get(d, [])
            if len(ps) > TOPIC_FROM:
                ps = self.topics_level(d)
            cands += [p for p in ps[:PROPS_MAX_PER_DOC] if p["key"] not in taken and p["key"] not in self._scored]
        self._scored |= {p["key"] for p in cands}

        def describe(p):
            g = f", {grade_text(p['cls'] or None, p['lvl'] or None)}" if (p["cls"] or p["lvl"]) else ""
            pop = f" [population: {cut(p['population'], 60)}]" if p["population"] else ""
            return f'{cut(self.doc_title(p["document"]), 40)} p.{p["number"]}, {p["kind"]}{g}: {cut(p["text"], 300)}{pop}'
        flash = self.score(FLASH_TASK, "", [(p["key"], describe(p)) for p in cands], CRITERIA)
        pre = sorted((p for p in cands if p["key"] in flash), key=lambda p: -flash[p["key"]])
        if RERANK:  # cascade: the stronger judge decides among the first judge's best
            top = pre[:RERANK_N]
            probs = self.score("answers", "", [(p["key"], describe(p)) for p in top], CRITERIA, scorer=RERANK)
            ranked = sorted((p for p in top if p["key"] in probs), key=lambda p: -probs[p["key"]])
        else:
            probs, ranked = flash, pre
        limit = PIECES_MAX_WIDE if self.aggregate else PIECES_MAX
        fit = [p for p in ranked if probs[p["key"]] >= TAKE]
        per_doc = defaultdict(int)
        best = []
        for p in fit:  # each guideline's best statements first, so one guideline cannot fill every slot
            if per_doc[p["document"]] < PER_DOC_TAKE:
                best.append(p)
                per_doc[p["document"]] += 1
        best = (best + [p for p in fit if p not in best])[:limit]
        best += [p for p in ranked if p not in best and probs[p["key"]] >= FLOOR][:max(0, MIN_STATEMENTS - len(best))]
        for p in best:
            pg = self.graph.page(p["page"])
            self.evidence.append({"kind": "statement", "proposition_kind": p["kind"], "key": p["key"], "text": p["text"],
                                  "quote": p["quote"], "cor": p["cls"] or None, "loe": p["lvl"] or None, "about": "", "context": "",
                                  "page": pg["id"], "number": pg["number"], "document": pg["document"],
                                  "documentTitle": pg["documentTitle"], "score": round(probs[p["key"]], 3)})
        self.trace.append({"type": "level", "level": "propositions", "candidates": len(cands), "taken": [p["key"] for p in best],
                           "p": {p["key"]: round(probs[p["key"]], 3) for p in ranked},
                           "flash": {p["key"]: round(flash[p["key"]], 3) for p in pre[:RERANK_N]}})
        self.emit(t="taken", level="propositions", candidates=len(cands), keys=[p["key"] for p in best],
                  scores={p["key"]: round(probs[p["key"]], 3) for p in best})
        return len(best)

    # --- the walk -------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        try:
            self.classify_question()
            docs = self.documents_level()
            self.emit(t="guidelines", kept=list(docs))
            keep = len(docs)
            for attempt in range(ROUNDS):
                with ThreadPoolExecutor(2) as ex:
                    fp = ex.submit(self.propositions_level, docs)
                    fs = ex.submit(self.statements_level, docs, set()) if USE_STATEMENTS else None
                    fp.result()
                    if fs:
                        fs.result()
                self.emit(t="round", n=attempt + 1, docs=list(docs),
                          evidence=[{k: e.get(k) for k in ("key", "kind", "score", "document", "page", "number")} for e in self.evidence])
                final = attempt == ROUNDS - 1
                absent = self.library_absent()
                if not self.evidence and absent:
                    self.trace.append({"type": "note", "text": f"absent from the library: {', '.join(absent)}"})
                    final = True
                if attempt >= 1 and sum(1 for t in self.trace if t.get("type") == "writer" and (t.get("output") or {}).get("status") == "not_covered") >= 1:
                    final = True
                cap = EVIDENCE_MAX_WIDE if self.aggregate else EVIDENCE_MAX
                self.evidence = sorted(self.evidence, key=lambda x: -x.get("score", 0))[:cap]
                self.emit(t="writing", final=final, pieces=len(self.evidence))
                out = self.write_answer(final=final)
                if "finish" in out and not out["answer"].strip():
                    out["answer"] = ("No guideline in the library addresses this question: no statement about it was found in the guidelines "
                                     "searched.") if out["finish"] == "not covered" else "The evidence found does not state an answer."
                if "finish" in out:
                    self.finish(out["finish"], out["answer"], out["answer_items"], out["count"])
                    break
                self.evidence = [e for e in self.evidence if not e.get("unfit")]
                extra = list(self.graded_version_pages())
                docs = list(dict.fromkeys(self.doc_ranking[:keep * (attempt + 2)] + [self._doc_of.get(x) for x in extra if self._doc_of.get(x)]))
        except ModelError as e:
            self.trace.append({"type": "error", "error": str(e)})
            self.finish("error", f"The walk stopped: {e}")
        if self.finished is None:
            self.finish("not covered", "No guideline statement answering the question was found.")
        return {**self.finished, "moves": self.requests, "usd": round(self.usd, 5)}
