"""Walker V5: the beam walker of round 18 made cheap, fast and graph-first.

Changes against walker/beam.py, each from reading the walker's traces:
- scoring with clef-flash (0.038 USD per million input tokens against clef's 0.24), the level's task stated once in
  the state and each candidate as a short question, so a request costs a fraction of round 18's;
- compact node descriptions: a document by title, body and year; a page by its top headings and the opening words of
  its first recommendations;
- graph edges as candidates when an answer is incomplete: the fully graded version of an ungraded statement taken
  (the same statement graded on another page of the same guideline), the pages that name the question's concepts, and
  the pages of the guidelines already kept, scored again with the writer's missing part as context;
- merged grade cells: a line inside a "COR | LOE" table that carries no grade cells is shown with the row above it,
  whose class and level it shares when the table merged the cell;
- an unanswerable question stops at once when the writer finds nothing and the library holds no page with one of the
  question's words.
"""
import json
import os
import re
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .agent import _similar, grade_of_row, grade_text
from .beam import BeamWalk
from .models import PRICES, ModelError, _post
from .text import cut, lines_of, words_of

SCORER = os.environ.get("V5_SCORER", "@cf/cloudflare/clef-flash")
BATCH = 60
DOCS_KEEP, DOCS_KEEP_WIDE = 4, 6
BODIES = {"ESC": "European Society of Cardiology (ESC)", "AHA": "American Heart Association (AHA)", "ACC": "American College of Cardiology (ACC)",
          "HRS": "Heart Rhythm Society (HRS)", "HFSA": "Heart Failure Society of America (HFSA)", "DGK": "German Cardiac Society (DGK)",
          "EHRA": "European Heart Rhythm Association (EHRA)", "HFA": "Heart Failure Association (HFA)", "EAPC": "European Association of Preventive Cardiology (EAPC)",
          "NICE": "National Institute for Health and Care Excellence (NICE)", "AWMF": "AWMF (German guideline register)"}
PAGES_KEEP, PAGES_KEEP_WIDE = 3, 6
STATEMENT_PAGES = 3  # pages of the best-scored statements whose lines are read too
MIN_STATEMENTS, FLOOR = 5, 0.25  # statements the writer always sees per round, if they score at least FLOOR
STATEMENTS_PER_DOC = 40  # statements per kept guideline that reach the scorer (BM25 prefilter in the graph)
TABLE_ROWS_PER_DOC = 20  # table statements per kept guideline that reach the scorer
PAGES_PER_DOC = 12  # pages per kept guideline that reach the page scorer (BM25 prefilter)
TAKE = 0.6
PIECES_MAX, PIECES_MAX_WIDE = 12, 40
LINES_PER_PAGE = 50
READ_PAGES = 3  # pages whose lines are scored per round
EVIDENCE_MAX, EVIDENCE_MAX_WIDE = 40, 60
RECHECK_EFFORT = os.environ.get("V5_RECHECK_EFFORT", "low")  # the writer's effort when the completion check rejects the fast answer
CHECK_KEEP = 0.5
CHECK = os.environ.get("V5_CHECK", "0") == "1"  # the completion check (off by default)
CHECK_Q = ("Does the answer state what the question asks for (the value, threshold, class, level, list or premise check), exactly as "
           "the evidence states it, for the group, situation and guideline the question names?")  # pieces the writer reads, best-scored first
TOP_PAGES_PER_DOC = 1  # the best word-matching page of each kept guideline is read in the line level
LINES_IN_DOCS, LINES_LIBRARY = 60, 30
DENSE_DOCS_FROM, DENSE_DOCS_MAX = 5, 2  # the top semantic windows whose guidelines may join the beam, at most two  # word-matched lines per round from the kept guidelines and from the whole library
_STOP = {"the", "and", "for", "with", "what", "which", "how", "does", "are", "is", "of", "in", "to", "a", "an", "or", "on", "by",
         "guideline", "guidelines", "recommendation", "patient", "according", "should", "be", "that", "this", "their", "at", "as"}
ROUNDS = 3
EXTRA = 6

TASK = {
    "documents": "Which clinical practice guidelines could contain the answer to the question? Judge each candidate guideline by its title, issuing body and year.",
    "pages": "Which pages of these guidelines could contain the answer to the question, or a part of it? Judge each candidate page by its guideline, headings and first recommendations.",
    "pieces": ("Which pieces state an answer element for the situation, group and guideline the question names: a recommendation with "
               "its class or level, a value, a criterion, or an element of the asked list? When the question assumes a class, level or "
               "fact, the guideline's own statement on that exact subject fits even if it says something different (it shows the "
               "premise is wrong)."),
}
VOCAB = ("List the words a clinical guideline page would use for what the question asks: the guideline's own terms, synonyms, "
         "abbreviations and drug or test names, and the kind of value it asks for. Return a JSON object {\"english\": \"15 to 25 words\", "
         "\"german\": \"the same in German clinical wording\"}. Words only, no sentences.")
CRITERIA = {"true": "Yes, for this question.", "false": "No: another topic, group, situation or guideline, or it does not state what is asked."}
_GRADE_HEADER = re.compile(r"\b(COR|Class)\b.*\b(LOE|Level|Evidence)\b", re.I)


_GRADE_CLAIM = re.compile(r"\b(?:as\s+)?(?:a\s+)?(?:class|COR)\s+(?:I{1,3}|IIa|IIb|1|2a|2b|3)\b(?:\s*\([^)]*\))?"
                          r"|\b(?:level\s+of\s+evidence|LOE)\s+[ABC](?:-(?:R|NR|LD|EO))?(?![\w-])", re.I)


def _without_grades(q: str) -> str:
    """The question without the classes and levels it states ("... classify X as class III?" -> "... classify X?")."""
    return re.sub(r"\s{2,}", " ", _GRADE_CLAIM.sub("", q)).strip()


def _stem(w: str) -> str:
    """Plural to singular for matching ("DOACs" and "DOAC", "inhibitors" and "inhibitor")."""
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


@dataclass
class WalkV5(BeamWalk):
    _tables = {}  # guideline -> its table statements (shared by all walks of a run)
    precision_hint: bool = False  # the writer's precision instruction (agent.write_answer), untested, off
    _lines = None  # every line of the library (built once per run)
    _topics = None  # document -> (topic, language)
    _dense = None  # the semantic window index (or False when absent)
    _doc_map = None  # page -> guideline
    _line_idf = {}
    _scored: set = field(default_factory=set)
    lock5: threading.Lock = field(default_factory=threading.Lock)

    # --- one level --------------------------------------------------------------------------------------------------
    def score(self, level: str, task: str, items: list[tuple[str, str]], criteria: dict, scorer: str | None = None) -> dict[str, float]:
        scorer = scorer or SCORER
        batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
        # the scorer judges whether a candidate is about the asked subject; a class or level the question assumes is left
        # out of its question (the writer still sees it), so a statement that contradicts a wrong premise is not rejected
        state = {"question": _without_grades(self.question), "task": TASK[level], **({"still_missing": self.missing} if self.missing else {})}

        def one(batch):
            questions = {f"c{n}": {"type": "noul", "instructions": text, "criteria": CRITERIA} for n, (_, text) in enumerate(batch)}
            import time
            started = time.perf_counter()
            if scorer.startswith("@cf/"):  # Cloudflare Workers AI
                res = _post(f"https://api.cloudflare.com/client/v4/accounts/{{account}}/ai/run/{scorer}",
                            {"model": scorer.rsplit("/", 1)[-1], "state": state, "questions": questions})
                if res.get("success") is False:
                    raise ModelError(f"scorer error: {str(res.get('errors'))[:300]}")
                result = res.get("result") or {}
                tokens = int((result.get("usage") or {}).get("input_tokens") or 0)
                usd = tokens * PRICES.get(scorer, (0.24, 0))[0] / 1e6
            else:  # OpenRouter System One (baseten/joey-1, baseten/elephant-1, baseten/mammoth-1, ...)
                from .models import systemone_openrouter
                result = systemone_openrouter(scorer, state, questions)
                u = result.get("usage") or {}
                tokens = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
                usd = float(u["cost"]) if u.get("cost") is not None else tokens * PRICES.get(scorer, (0.1, 0))[0] / 1e6
            usage = {"model": scorer, "input_tokens": tokens, "usd": usd, "ms": round((time.perf_counter() - started) * 1000)}
            answers = result.get("answers") or {}
            with self.lock5:
                self.usd += usage["usd"]
                self.requests += 1
                self.trace.append({"type": "beam", "level": level, "candidates": len(batch), "usage": usage})
            out = {}
            for n, (key, _) in enumerate(batch):
                prob = (answers.get(f"c{n}") or {}).get("noul")
                if isinstance(prob, (int, float)):
                    out[key] = float(prob)
            return out

        probs: dict[str, float] = {}
        if batches:
            with ThreadPoolExecutor(min(8, len(batches))) as ex:
                for part in ex.map(one, batches):
                    probs.update(part)
        cb = getattr(self, "on_event", None)  # live view (TIB Chat Graph Walker): every scored level, as it happens
        if cb:
            cb({"t": "scores", "level": level, "scorer": scorer, "probs": probs})
        return probs

    # --- descriptions -----------------------------------------------------------------------------------------------
    def document_topics(self) -> dict[str, tuple[str, str]]:
        """Topic and language of every document from the graph's document layer (y:topic, y:language), read once."""
        if WalkV5._topics is None:
            rows = self.graph.select("SELECT ?d (SAMPLE(?t) AS ?topic) (SAMPLE(?l) AS ?lang) WHERE { ?d a y:Document . OPTIONAL { ?d y:topic ?t } OPTIONAL { ?d y:language ?l } } GROUP BY ?d")
            WalkV5._topics = {r.get("d", ""): (r.get("topic", ""), r.get("lang", "")) for r in rows}
        return WalkV5._topics

    def documents_level(self) -> list[str]:
        topics = self.document_topics()

        def describe(d):
            body = d.get("body") or ""
            body = ", ".join(BODIES.get(b.strip(), b.strip()) for b in re.split(r"[/,]", body) if b.strip()) if body else ""
            topic, lang = topics.get(d["id"], ("", ""))
            facts = "; ".join(x for x in (body, str(d.get("year") or ""), f"topic: {topic}" if topic else "", "in German" if lang == "de" else "") if x)
            return f'{d["title"]} ({facts})'
        items = [(d["id"], describe(d)) for d in self.graph.documents()]
        probs = self.score("documents", "", items, CRITERIA)
        keep = DOCS_KEEP_WIDE if self.aggregate else DOCS_KEEP
        self.doc_ranking = [k for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])]
        kept = self.doc_ranking[:keep]
        self.trace.append({"type": "level", "level": "documents", "kept": kept, "p": {k: round(probs[k], 3) for k in kept}})
        return kept

    def page_text(self, page_id: str, title: str | None = None) -> str:
        pg = self.graph.page(page_id)
        heads = "; ".join(cut(h, 70) for h in pg["headings"][:3]) or "no headings"
        recs = " | ".join(cut(r["text"], 90) for r in pg["recommendations"][:2])
        return f'{cut(title or pg["documentTitle"], 60)}, p.{pg["number"]}: {heads}' + (f' | recs: {recs}' if recs else "")

    def graded_version_pages(self) -> dict[str, str]:
        """Graph edge: the fully graded version of an ungraded statement taken, on another page of the same guideline."""
        out = {}
        for e in self.evidence:
            if e["kind"] in ("recommendation", "table row", "line") and not (e.get("cor") and e.get("loe")):
                for g in self.graph.graded_versions(e["text"], 0.5)[:3]:
                    if g["page"] != e["page"] and g["cor"] and g["loe"] and self.graph.page(g["page"])["document"] == e["document"]:
                        out[g["page"]] = f"graded version of a statement taken ({grade_text(g['cor'], g['loe'])})"
        return out

    def pages_level(self, docs: list[str], text: str, exclude: set[str]) -> list[str]:
        cands: dict[str, str] = {}
        titles = {d: self.doc_title(d) for d in docs}
        for d in docs:
            for h in self.index.search(text, d, PAGES_PER_DOC):
                if h["page"] not in exclude:
                    cands[h["page"]] = self.page_text(h["page"], titles[d])
        added = {**self.extra_pages(text), **self.graded_version_pages()}
        added = {p: why for p, why in added.items() if p not in exclude}
        for p, why in added.items():
            cands[p] = (cands.get(p) or self.page_text(p)) + f" [{why}]"
        probs = self.score("pages", "", list(cands.items()), CRITERIA)
        keep = PAGES_KEEP_WIDE if self.aggregate else PAGES_KEEP
        ranked = [k for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])]
        kept = ranked[:keep]
        for p, why in added.items():  # a graded version found through the graph is read whatever its score
            if why.startswith("graded version") and p not in kept:
                kept.append(p)
        self.trace.append({"type": "level", "level": "pages", "candidates": len(cands), "added": list(added),
                           "kept": kept, "p": {k: round(probs.get(k, 0), 3) for k in kept}})
        return kept

    def pieces(self, page_id: str) -> list[dict]:
        p = self.graph.page(page_id)
        base = {"page": p["id"], "number": p["number"], "document": p["document"], "documentTitle": p["documentTitle"]}
        out = [{**base, "kind": "recommendation", "key": r["id"], "text": r["text"], "cor": r["cor"], "loe": r["loe"], "about": r["about"],
                "modality": r.get("modality", ""), "polarity": r.get("polarity", "")} for r in p["recommendations"]]
        lines = [l for l in lines_of(p["text"]) if not any(_similar(l, r["text"]) > 0.8 for r in p["recommendations"])
                 and not re.match(r"^\(?continued\)?$", l.strip("* ."), re.I)]
        header, row_above = "", None
        for i, line in enumerate(lines[:LINES_PER_PAGE]):
            cor, loe = grade_of_row(line)
            if _GRADE_HEADER.search(line) and "|" in line:
                header, row_above = line, None
            context = lines[i - 1] if i > 0 else ""
            inherited = False
            if header and not (cor or loe) and row_above and len(line) > 40 and not _GRADE_HEADER.search(line):
                # a row of a grade table without grade cells: the table merged the class and level cell with the row
                # above (rowspan); the row carries that grade, marked as inherited
                cor, loe = grade_of_row(row_above)
                inherited = True
                context = f'grade from the merged cell of the row above in the table "{cut(header, 60)}": {cut(row_above, 140)}'
            elif cor or loe:
                row_above = line
            out.append({**base, "kind": "table row" if cor or loe else "line", "key": f'{p["id"]}#line{i}', "text": line, "cor": cor, "loe": loe,
                        "about": "", "context": context, **({"inherited_grade": True} if inherited else {})})
        return out

    def pieces_level(self, pages: list[str]) -> int:
        taken = {e["key"] for e in self.evidence}
        cands = [x for pg in pages for x in self.pieces(pg) if x["key"] not in taken]
        for pg in pages:
            self.read_pages.add(pg)

        def describe(x):
            if x["kind"] == "recommendation":
                grade = f'rec, {grade_text(x["cor"], x["loe"])}'
            elif x["kind"] == "table row":
                grade = f'table row, {grade_text(x["cor"], x["loe"])}'
            else:
                grade = "line"
            ctx = " (grade from the merged cell above)" if x.get("inherited_grade") else ""
            return f'{cut(x["documentTitle"], 40)} p.{x["number"]} {grade}: {cut(x["text"], 300)}{ctx}'
        probs = self.score("pieces", "", [(x["key"], describe(x)) for x in cands], CRITERIA)
        limit = PIECES_MAX_WIDE if self.aggregate else PIECES_MAX
        best = sorted((x for x in cands if probs.get(x["key"], 0) >= TAKE), key=lambda x: -probs[x["key"]])[:limit]  # per round
        self.evidence += best
        self.trace.append({"type": "level", "level": "pieces", "candidates": len(cands), "taken": [x["key"] for x in best]})
        return len(best)

    def table_statements(self, doc: str) -> list[dict]:
        """Graph repair: the graded rows of the grade tables ("COR | LOE | Recommendations") of a guideline's pages that
        the recommendation layer does not hold, as statement nodes; a row without grade cells takes the merged cell of
        the row above. Built from the page text in the graph, the same for every guideline."""
        cache = WalkV5._tables
        if doc in cache:
            return cache[doc]
        out = []
        layer = {}
        for r in self.index.rec_list:
            layer.setdefault(r["page"], []).append(r["text"])
        for pg in self.index.pages:
            if pg["document"] != doc:
                continue
            lines = lines_of(pg["text"])
            header, row_above = "", None
            for i, line in enumerate(lines):
                cor, loe = grade_of_row(line)
                if _GRADE_HEADER.search(line) and "|" in line:
                    header, row_above = line, None
                    continue
                if not header:
                    continue
                inherited = False
                if not (cor or loe) and row_above and len(line) > 40:
                    cor, loe = grade_of_row(row_above)
                    inherited = True
                elif cor or loe:
                    row_above = line
                if not (cor or loe) or len(line) < 25:
                    continue
                if any(_similar(line, t) > 0.6 for t in layer.get(pg["page"], [])):
                    continue
                out.append({"id": f'{pg["page"]}#tline{i}', "page": pg["page"], "text": line, "cor": cor, "loe": loe,
                            "modality": "", "polarity": "", "table": True, "inherited_grade": inherited})
        cache[doc] = out
        return out

    def statements_level(self, docs: list[str], exclude: set[str]) -> list[str]:
        """Graph-first level: every recommendation node of the kept guidelines (with its class and level) is scored
        directly; the fitting ones become evidence. Returns the pages of the best-scored statements."""
        doc_of = WalkV5._doc_map or {p["page"]: p["document"] for p in self.index.pages}
        WalkV5._doc_map = doc_of
        self._doc_of = doc_of
        taken = {e["key"] for e in self.evidence}
        # prefilter in the graph: per kept guideline the statements whose wording matches the question (BM25 over the
        # recommendation layer), plus every statement on a page that names one of the question's concepts
        text = f"{self.question} {self.missing}".strip()
        wanted: dict[str, dict] = {}
        for d in docs:
            for r in self.index.search_recommendations(text, d, STATEMENTS_PER_DOC):
                wanted.setdefault(r["id"], r)
        if self.concepts:
            concept_pages = {p for p, _, _ in self.concepts.ranked_pages(text, 20) if doc_of.get(p) in docs}
            for r in self.index.rec_list:
                if r["page"] in concept_pages:
                    wanted.setdefault(r["id"], r)
        qterms = {_stem(t) for t in words_of(text)}
        for d in docs:  # table statements of the kept guidelines, ranked by the question words they hold
            rows = [(sum(self.index.idf.get(t, 0) for t in qterms & {_stem(w) for w in words_of(r["text"])}), r) for r in self.table_statements(d)]
            for sc, r in sorted((x for x in rows if x[0] > 0), key=lambda x: -x[0])[:TABLE_ROWS_PER_DOC]:
                wanted.setdefault(r["id"], r)
        recs = [r for r in wanted.values() if r["id"] not in taken and r["id"] not in exclude and r["id"] not in self._scored]
        self._scored |= {r["id"] for r in recs}  # a statement is scored once per walk; later rounds bring new ones
        titles = {d: cut(self.doc_title(d), 40) for d in docs}
        number = {p["page"]: p["number"] for p in self.index.pages}
        items = [(r["id"], f'{titles[doc_of[r["page"]]]} p.{number[r["page"]]}, {"table row, " if r.get("table") else ""}{grade_text(r["cor"], r["loe"])}: {cut(r["text"], 260)}') for r in recs]
        probs = self.score("pieces", "", items, CRITERIA)
        ranked = sorted((r for r in recs if r["id"] in probs), key=lambda r: -probs[r["id"]])
        limit = PIECES_MAX_WIDE if self.aggregate else PIECES_MAX
        best = [r for r in ranked if probs[r["id"]] >= TAKE][:limit]
        # the scorer's threshold can drop the one statement that answers (it rates a premise-contradicting class low):
        # the best-ranked statements always reach the writer, which judges them and marks the unfit ones
        best += [r for r in ranked if r not in best and probs[r["id"]] >= FLOOR][:max(0, MIN_STATEMENTS - len(best))]
        for r in best:
            pg = self.graph.page(r["page"])
            about = next((x["about"] for x in pg["recommendations"] if x["id"] == r["id"]), "")
            piece = {"kind": "table row" if r.get("table") else "recommendation", "key": r["id"], "text": r["text"], "cor": r["cor"], "loe": r["loe"],
                     "about": about, "modality": r.get("modality", ""), "polarity": r.get("polarity", ""), "page": pg["id"], "number": pg["number"],
                     "document": pg["document"], "documentTitle": pg["documentTitle"]}
            if r.get("inherited_grade"):
                piece["context"] = "the class and level come from the merged cell of the table row above"
            piece["score"] = round(probs[r["id"]], 3)
            self.evidence.append(piece)
        pages = list(dict.fromkeys(r["page"] for r in ranked[:STATEMENT_PAGES]))
        self.trace.append({"type": "level", "level": "statements", "candidates": len(recs), "taken": [r["id"] for r in best],
                           "kept": pages, "p": {r["id"]: round(probs[r["id"]], 3) for r in ranked[:8]}})
        return pages

    def lines_level(self, pages: list[str], text: str = "", docs: list[str] | None = None) -> int:
        """Lines and table rows: the lines of the chosen pages and the lines whose words best match the question, from the
        kept guidelines and the whole library (values, criteria, table rows the recommendation layer misses)."""
        taken = {e["key"] for e in self.evidence}
        seen_keys = set()
        cands = []
        for x in [y for pg in pages for y in self.pieces(pg) if y["kind"] != "recommendation"] + self.retrieve_lines(text or self.question, docs or []) + self.dense_windows():
            if x["key"] not in taken and x["key"] not in seen_keys and x["key"] not in self._scored:
                seen_keys.add(x["key"])
                cands.append(x)
        self._scored |= seen_keys
        for pg in pages:
            self.read_pages.add(pg)

        def describe(x):
            grade = f'table row, {grade_text(x["cor"], x["loe"])}' if x["kind"] == "table row" else "line"
            ctx = " (grade from the merged cell above)" if x.get("inherited_grade") else ""
            return f'{cut(x["documentTitle"], 40)} p.{x["number"]} {grade}: {cut(x["text"], 300)}{ctx}'
        probs = self.score("pieces", "", [(x["key"], describe(x)) for x in cands], CRITERIA)
        limit = PIECES_MAX_WIDE if self.aggregate else PIECES_MAX
        best = sorted((x for x in cands if probs.get(x["key"], 0) >= TAKE), key=lambda x: -probs[x["key"]])[:limit]
        self.evidence += [{**x, "score": round(probs[x["key"]], 3)} for x in best]
        self.trace.append({"type": "level", "level": "lines", "candidates": len(cands), "taken": [x["key"] for x in best]})
        return len(best)

    def line_index(self) -> list[dict]:
        """Every line of every page (with merged table grades), indexed once per run: the candidates of the line level."""
        if WalkV5._lines is None:
            rows = []
            for pg in self.index.pages:
                for x in self.pieces_of_text(pg):
                    rows.append(x)
            df: dict[str, int] = {}
            for x in rows:
                x["terms"] = {_stem(t) for t in words_of(x["text"])}
                for t in x["terms"]:
                    df[t] = df.get(t, 0) + 1
            n = len(rows) or 1
            WalkV5._line_idf = {t: __import__("math").log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
            WalkV5._lines = rows
        return WalkV5._lines

    def pieces_of_text(self, pg: dict) -> list[dict]:
        """The lines of a page from the graph's page text (no page query), with merged-cell grades in grade tables."""
        lines = [l for l in lines_of(pg["text"]) if not re.match(r"^\(?continued\)?$", l.strip("* ."), re.I)]
        out, header, row_above = [], "", None
        for i, line in enumerate(lines):
            cor, loe = grade_of_row(line)
            if _GRADE_HEADER.search(line) and "|" in line:
                header, row_above = line, None
            inherited = False
            if header and not (cor or loe) and row_above and len(line) > 40 and not _GRADE_HEADER.search(line):
                cor, loe = grade_of_row(row_above)
                inherited = True
            elif cor or loe:
                row_above = line
            if len(line) < 12:
                continue
            out.append({"page": pg["page"], "document": pg["document"], "documentTitle": pg["documentTitle"], "number": pg["number"],
                        "kind": "table row" if cor or loe else "line", "key": f'{pg["page"]}#line{i}', "text": line, "cor": cor, "loe": loe,
                        "about": "", "context": lines[i - 1] if i > 0 else "", **({"inherited_grade": True} if inherited else {})})
        return out

    def retrieve_lines(self, text: str, docs: list[str]) -> list[dict]:
        """Lines whose words best match the question: LINES_IN_DOCS from the kept guidelines and LINES_LIBRARY from the
        whole library (a guideline the document level missed)."""
        q = {_stem(t) for t in words_of(text)} - _STOP
        idf = WalkV5._line_idf if WalkV5._lines is not None else {}
        rows = self.line_index()
        idf = WalkV5._line_idf
        scored = [(sum(idf.get(t, 0) for t in q & x["terms"]), x) for x in rows]
        scored = [(sc, x) for sc, x in scored if sc > 0]
        scored.sort(key=lambda z: -z[0])
        in_docs = [x for _, x in scored if x["document"] in docs][:LINES_IN_DOCS]
        library = [x for _, x in scored if x["document"] not in docs][:LINES_LIBRARY]
        return [{k: v for k, v in x.items() if k != "terms"} for x in in_docs + library]

    def vocabulary(self) -> str:
        """The words a guideline page would use for what the question asks, in English and German (one writer call per
        walk, no reasoning): questions paraphrase ("pulse-per-minute cutoff at enrollment") what the guideline states in
        its own terms ("resting heart rate >= 77 bpm, SHIFT inclusion"); a third of the library is German. The words feed
        the word filters over the graph's statements and lines; nothing else."""
        if getattr(self, "_vocab", None) is None:
            from .models import write_json
            try:
                out, usage = write_json(VOCAB, {"question": self.question}, {"english", "german"}, max_tokens=600, effort="none")
                self.usd += usage["usd"]
                self.trace.append({"type": "writer", "task": "vocabulary", "output": out, "usage": usage})
                self._vocab = f'{out.get("english") or ""} {out.get("german") or ""}'.strip()
            except ModelError:
                self._vocab = ""
        return self._vocab

    def german(self) -> str:
        return ""

    def dense_windows(self, k: int = 20) -> list[dict]:
        """Secondary channel: the page-text windows nearest to the question in the semantic index built from the graph's
        page text (2026-11-04_walker_v5/build_dense.py, EmbeddingGemma on Cloudflare); empty when the index is absent."""
        if WalkV5._dense is None:
            import numpy as np
            path = Path(os.environ.get("V5_DENSE", Path(__file__).resolve().parents[2] / "2026-11-04_walker_v5" / "dense"))
            if not (path / "windows.npz").exists():
                WalkV5._dense = False
            else:
                z = np.load(path / "windows.npz", allow_pickle=True)
                meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
                WalkV5._dense = {"vec": z["vec"].astype(np.float32), "page": z["page"], "text": z["text"], "page_ids": meta["page_ids"], "model": meta["model"]}
        if not WalkV5._dense:
            return []
        if getattr(self, "_dense_hits", None) is None:
            import numpy as np
            import time
            d = WalkV5._dense
            started = time.perf_counter()
            res = _post(f"https://api.cloudflare.com/client/v4/accounts/{{account}}/ai/run/{d['model']}", {"text": [f"task: question answering | query: {self.question}"]})
            q = np.asarray(res["result"]["data"][0], dtype=np.float32)
            q /= np.linalg.norm(q)
            top = np.argsort(-(d["vec"] @ q))[:k]
            self.requests += 1
            self.trace.append({"type": "beam", "level": "dense", "candidates": k, "usage": {"model": d["model"], "ms": round((time.perf_counter() - started) * 1000), "usd": 0.0, "input_tokens": 0}})
            meta = {p["page"]: p for p in self.index.pages}
            hits = []
            for j in top:
                pid = d["page_ids"][int(d["page"][j])]
                pg = meta.get(pid)
                if pg:
                    hits.append({"page": pid, "document": pg["document"], "documentTitle": pg["documentTitle"], "number": pg["number"], "kind": "line",
                                 "key": f"{pid}#dense{int(j)}", "text": str(d["text"][j]), "cor": None, "loe": None, "about": "", "context": ""})
            self._dense_hits = hits
        return self._dense_hits

    def answer_holds(self, answer: str) -> bool:
        """One clef-flash question: does the answer state what the question asks for (the value, class, level or list),
        exactly as the evidence states it, for the asked group and guideline? True when it does or when the check fails."""
        import time
        evidence = [cut(self.evidence_text(e), 300) for e in self.evidence[:10]]
        state = {"question": self.question, "answer": cut(answer.split("\n\nEvidence:")[0], 1500), "evidence": evidence}
        q = {"holds": {"type": "noul", "instructions": CHECK_Q, "criteria": {"true": "Yes, the answer gives exactly that.",
                                                                            "false": "No: a neighbouring value, another group or guideline, a missing part, or not in the evidence."}}}
        started = time.perf_counter()
        try:
            res = _post(f"https://api.cloudflare.com/client/v4/accounts/{{account}}/ai/run/{SCORER}", {"model": SCORER.rsplit("/", 1)[-1], "state": state, "questions": q})
        except ModelError:
            return True
        result = res.get("result") or {}
        tokens = int((result.get("usage") or {}).get("input_tokens") or 0)
        usage = {"model": SCORER, "input_tokens": tokens, "usd": tokens * PRICES.get(SCORER, (0.24, 0))[0] / 1e6, "ms": round((time.perf_counter() - started) * 1000)}
        prob = ((result.get("answers") or {}).get("holds") or {}).get("noul")
        with self.lock5:
            self.usd += usage["usd"]
            self.requests += 1
        self.trace.append({"type": "beam", "level": "check", "candidates": 1, "p": prob, "usage": usage})
        return not isinstance(prob, (int, float)) or prob >= CHECK_KEEP

    def write_answer_with(self, final: bool, effort: str):
        """The writer's answer with another reasoning effort (the module default is read at call time)."""
        from . import models
        old = models.WRITER_EFFORT
        models.WRITER_EFFORT = effort
        try:
            return self.write_answer(final=final)
        except ModelError:
            return None
        finally:
            models.WRITER_EFFORT = old

    def library_absent(self) -> list[str]:
        """Words of the question (at least five letters) that no page of the library holds."""
        return [w for w in self.index.absent(self.question, None) if len(w) >= 5]

    # --- the walk ---------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        """Two parallel stages per walk: (A) question kind, guideline level, guideline vocabulary and semantic windows;
        (B) per round, the statement level and the line level side by side; then the writer."""
        try:
            with ThreadPoolExecutor(4) as ex:
                fa = [ex.submit(self.classify_question), ex.submit(self.vocabulary), ex.submit(self.dense_windows)]
                for f in fa:
                    f.result()
                docs = self.documents_level()  # needs the question kind (how many guidelines stay)
            keep = len(docs)
            for h in self.dense_windows()[:DENSE_DOCS_FROM]:  # guidelines the semantic channel points to join the beam
                if h["document"] not in docs and len(docs) < keep + DENSE_DOCS_MAX:
                    docs.append(h["document"])
            text = f"{self.question} {self.vocabulary()}"
            for attempt in range(ROUNDS):
                with ThreadPoolExecutor(2) as ex:
                    fs = ex.submit(self.statements_level, docs, set())
                    top_pages = [h["page"] for d in docs for h in self.index.search(text, d, TOP_PAGES_PER_DOC)]  # best word match per guideline
                    fl = ex.submit(self.lines_level, [p for p in top_pages if p not in self.read_pages][:READ_PAGES + 2], text, docs)
                    fs.result()
                    fl.result()
                final = attempt == ROUNDS - 1
                absent = self.library_absent()
                if not self.evidence and absent:  # the library holds no page with a word of the question
                    self.trace.append({"type": "note", "text": f"absent from the library: {', '.join(absent)}"})
                    final = True
                if attempt >= 1 and sum(1 for t in self.trace if t.get("type") == "writer" and (t.get("output") or {}).get("status") == "not_covered") >= 1:
                    final = True  # the writer found nothing twice: the guidelines do not cover the question
                # the writer reads the best-scored evidence first and at most EVIDENCE_MAX pieces (more for set questions)
                cap = EVIDENCE_MAX_WIDE if self.aggregate else EVIDENCE_MAX
                self.evidence = sorted(self.evidence, key=lambda x: -x.get("score", 0))[:cap]
                out = self.write_answer(final=final)
                if "finish" in out and not out["answer"].strip():
                    out["answer"] = ("No guideline in the library addresses this question: no statement about it was found in the guidelines "
                                     "searched.") if out["finish"] == "not covered" else "The evidence found does not state an answer."
                if CHECK and "finish" in out and out["finish"] in ("answered", "contradicted") and not self.answer_holds(out["answer"]):
                    # independent completion check (jev-ultrafast's DONE verification): the fast answer does not hold up
                    # against the evidence, so the writer answers once more, this time with brief reasoning
                    retry = self.write_answer_with(final=True, effort=RECHECK_EFFORT)
                    if retry and "finish" in retry and retry["answer"].strip():
                        out = retry
                if "finish" in out:
                    self.finish(out["finish"], out["answer"], out["answer_items"], out["count"])
                    break
                # pieces the writer judged as not fitting leave the evidence, so the next round's pieces are read
                self.evidence = [e for e in self.evidence if not e.get("unfit")]
                text = f"{self.question} {self.missing} {self.vocabulary()}"
                extra = list(self.graded_version_pages())  # graph edge: the graded version of a statement taken
                docs = list(dict.fromkeys(self.doc_ranking[:keep * (attempt + 2)] + [self._doc_of.get(x) for x in extra if self._doc_of.get(x)]))
        except ModelError as e:
            self.trace.append({"type": "error", "error": str(e)})
            self.finish("error", f"The walk stopped: {e}")
        if self.finished is None:
            self.finish("not covered", "No guideline statement answering the question was found.")
        return {**self.finished, "moves": self.requests, "usd": round(self.usd, 5)}
