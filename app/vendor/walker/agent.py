"""The walk: place -> indexed elements -> operation + targets in one navigator request -> execution.

jev-ultrafast's loop (agent.py, model.py) carried over from a browser to the guideline graph. The place is what a
reader sees: the library, a guideline's table of contents, a page, or search hits. Every observation numbers the
elements visible there, with their values. One classifier request asks which operation to perform and, speculatively,
which element each target-taking operation would use; only the chosen operation's target is executed. The text model
writes only text: a search query when the operation is SEARCH, and the answer from the evidence when it is ANSWER.
Model output never becomes a graph query; every executed target is an observed element.
"""
import re
from dataclasses import dataclass, field

from .models import QUERY_EFFORT, ModelError, classify, validate_choice, write_json
from .rules import ANSWER, NEXT_ACTION, QUERY, SWEEP, TARGET
from .search import Index
from .text import cut, lines_of, words_of

MAX_MOVES = 30
MAX_ANSWER_CHECKS = 2
MAX_OPTIONS = 250
MAX_LINES = 80
HISTORY = 10
STALL = 4  # moves without a new page or a change of evidence before only progress-making moves are offered
RECENT_PAGES = 3  # a page opened this recently is shown but not offered again: its result is in the history
TARGET_OPS = ("OPEN", "TAKE", "DROP")
SWEEP_MIN = 2  # SWEEP is offered on a page with at least this many takeable recommendations or graded rows
SWEEP_MAX = 60
COLLECT_MAX = 60  # recommendations judged in one request when collecting across guidelines  # one yes/no question per candidate in one request (clef takes up to 64 questions)
SWEEP_TAKE = 0.6


def grade_text(cor, loe) -> str:
    return f"class {cor or 'not given'}, level {loe or 'not given'}" if cor or loe else "no grade"


def strength_text(rec: dict) -> str:
    """The recommendation layer's strength and direction, such as "recommended, against"; empty for lines."""
    mod, pol = rec.get("modality") or "", rec.get("polarity") or ""
    return "; ".join(x for x in (f"strength {mod.replace('_', ' ')}" if mod else "", pol if pol in ("against",) else "") if x)


_COR = re.compile(r"^(?:class\s+)?(I{1,3}|IIa|IIb|1|2a|2b|3)(?:\s*\(?(?:strong|moderate|weak|no benefit|harm)\)?)?$", re.I)
_LOE = re.compile(r"^(?:level\s+)?([ABC](?:-(?:R|NR|LD|EO))?)$", re.I)


def asks_grade(question: str) -> tuple[bool, bool]:
    """Whether the question asks for a class of recommendation and/or a level of evidence (lexical, any guideline family)."""
    q = question.lower()
    asked_class = bool(re.search(r"\bclass(?:es)?\b|\bcor\b", q))
    asked_level = bool(re.search(r"\blevels? of evidence\b|\bevidence levels?\b|\bloe\b", q))
    return asked_class, asked_level


def grade_of_row(line: str) -> tuple[str | None, str | None]:
    """The class and level a table row states in its own cells, such as "... | 1 | C-EO" or "... | IIa | B"; else none."""
    cells = [c.strip() for c in line.split("|")]
    if len(cells) < 2:
        return None, None
    cor = next((m.group(1) for c in cells[1:] if (m := _COR.match(c))), None)
    loe = next((m.group(1) for c in cells[1:] if (m := _LOE.match(c))), None)
    return cor, loe


def _similar(a: str, b: str) -> float:
    x, y = set(words_of(a)), set(words_of(b))
    return len(x & y) / len(x | y) if x and y else 0.0


@dataclass
class Walk:
    graph: object
    index: Index
    question: str
    here: dict = field(default_factory=lambda: {"kind": "library"})
    path: list = field(default_factory=list)
    history: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    read_pages: set = field(default_factory=set)
    opened_docs: set = field(default_factory=set)
    queries: list = field(default_factory=list)
    page_visits: list = field(default_factory=list)
    answer_checks: int = 0
    checked_key: tuple = ()
    absent_seen: bool = False
    absent_searches: int = 0  # library-wide searches that reported a question word no page holds
    absent_words: list[str] = field(default_factory=list)  # those words, in the order they were reported
    missing: str = ""
    aggregate: bool | None = None  # whether the question asks for every element of a kind, several documents or a count
    finished: dict | None = None
    trace: list = field(default_factory=list)
    usd: float = 0.0

    # --- what the reader sees -----------------------------------------------------------------------------------
    def label(self, here: dict) -> str:
        kind = here["kind"]
        if kind == "library":
            return "the library"
        if kind == "document":
            return f'the table of contents of "{self.doc_title(here["id"])}"'
        if kind == "page":
            p = self.graph.page(here["id"])
            return f'page {p["number"]} of "{p["documentTitle"]}"'
        scope = f'"{self.doc_title(here["scope"])}"' if here["scope"] else "all guidelines"
        return f'the hits of the search "{here["query"]}" in {scope}'

    def doc_title(self, doc: str) -> str:
        return next((d["title"] for d in self.graph.documents() if d["id"] == doc), doc)

    def current_document(self) -> str | None:
        kind = self.here["kind"]
        if kind == "document":
            return self.here["id"]
        if kind == "page":
            return self.graph.page(self.here["id"])["document"]
        if kind == "search":
            return self.here["scope"]
        return None

    def graded_pages(self) -> dict[str, dict]:
        """Pages that hold a graded version of an ungraded piece of evidence taken, with that version."""
        key = tuple(e["key"] for e in self.evidence)
        if getattr(self, "_graded", (None, None))[0] == key:
            return self._graded[1]
        out = {}
        for e in self.evidence:
            if e["kind"] in ("recommendation", "table row", "line") and not (e["cor"] and e["loe"]):
                for rec in self.graph.graded_versions(e["text"], 0.5)[:3]:
                    if rec["page"] != e["page"] and (rec["cor"] and rec["loe"]):
                        out.setdefault(rec["page"], rec)
        self._graded = (key, out)
        return out

    def page_value(self, page: str, recommendations: int | None = None) -> str:
        parts = [] if recommendations is None else [f"{recommendations} recommendations" if recommendations else "no recommendations"]
        graded = self.graded_pages().get(page)
        if graded:
            parts.append(f"holds the fully graded version ({grade_text(graded['cor'], graded['loe'])}) of evidence you took with no or partial grade")
        taken = sum(1 for e in self.evidence if e["page"] == page)
        if page in self.read_pages:
            parts.append(f"read before, {taken} pieces taken" if taken else "read before, nothing taken")
        return "; ".join(parts)

    def observe(self) -> tuple[dict, list, dict]:
        """The decision state, the numbered elements, and the operations with their target elements by index."""
        elements: list[dict] = []
        targets: dict[str, dict[str, dict]] = {op: {} for op in TARGET_OPS}

        recent = set(self.page_visits[-RECENT_PAGES:])

        def add(role, label, value, op, action):
            if op == "OPEN" and action["go"]["kind"] == "page" and action["go"]["id"] in recent:
                op, value = None, "; ".join(x for x in (value, "just read: not offered again") if x)
            index = str(len(elements) + 1)
            elements.append({"index": index, "role": role, "label": label, "value": value})
            if op:
                targets[op][index] = {**action, "label": label, "value": value}

        kind = self.here["kind"]
        place: dict = {"place": self.label(self.here)}
        if kind == "library":
            for d in self.graph.documents():
                meta = ", ".join(str(x) for x in (d["year"], d["body"], f'{d["pages"]} pages') if x)
                add("guideline", f'{d["title"]} ({meta})', "opened before" if d["id"] in self.opened_docs else "", "OPEN",
                    {"go": {"kind": "document", "id": d["id"]}})
        elif kind == "document":
            toc = self.graph.toc(self.here["id"])
            if len(toc) > MAX_OPTIONS:
                place["note"] = f"the table of contents lists the first {MAX_OPTIONS} of {len(toc)} pages; search for the others"
            for e in toc[:MAX_OPTIONS]:
                add("page", f'page {e["number"]}: {"; ".join(e["headings"]) or e["opening"]}', self.page_value(e["page"], e["recommendations"]),
                    "OPEN", {"go": {"kind": "page", "id": e["page"]}})
        elif kind == "page":
            p = self.graph.page(self.here["id"])
            place["headings"] = "; ".join(p["headings"])
            toc = {e["number"]: e for e in self.graph.toc(p["document"])}
            for name, n in (("previous_page", p["number"] - 1), ("next_page", p["number"] + 1)):
                if n in toc:
                    e = toc[n]
                    cont = "; begins with a table row" if " | " in e["opening"] else ""
                    place[name] = f'page {n}: {"; ".join(e["headings"]) or e["opening"]} ({e["recommendations"]} recommendations{cont}{"; read before" if e["page"] in self.read_pages else ""})'
            page_lines = lines_of(p["text"])
            if page_lines and " | " in page_lines[-1]:
                place["note_table"] = "this page ends in a table row: the table may continue on the next page"
            taken = {e["key"] for e in self.evidence}
            shown_versions = 0
            for r in p["recommendations"]:
                value = "; ".join(x for x in (grade_text(r["cor"], r["loe"]), strength_text(r), f"about {r['about']}" if r["about"] else "",
                                              "taken as evidence" if r["id"] in taken else "") if x)
                add("recommendation", cut(r["text"], 420), value, None if r["id"] in taken else "TAKE",
                    {"take": {"kind": "recommendation", "key": r["id"], "text": r["text"], "cor": r["cor"], "loe": r["loe"], "about": r["about"],
                              "modality": r.get("modality", ""), "polarity": r.get("polarity", "")}})
                if not (r["cor"] or r["loe"]) and shown_versions < 6:
                    for g in [g for g in self.graph.graded_versions(r["text"]) if g["page"] != p["id"]][:2]:
                        gp = self.graph.page(g["page"])
                        add("graded version", f'{gp["documentTitle"]}, page {gp["number"]}: "{cut(g["text"], 200)}"',
                            grade_text(g["cor"], g["loe"]) + ("; read before" if g["page"] in self.read_pages else ""),
                            "OPEN", {"go": {"kind": "page", "id": g["page"]}})
                        shown_versions += 1
            lines = [l for l in lines_of(p["text"])
                     if not any(_similar(l, r["text"]) > 0.8 for r in p["recommendations"]) and not re.match(r"^\(?continued\)?$", l.strip("* ."), re.I)]
            for i, line in enumerate(lines[:MAX_LINES]):
                key = f'{p["id"]}#line{i}'
                cor, loe = grade_of_row(line)
                value = "; ".join(x for x in (f"table row: {grade_text(cor, loe)}" if cor or loe else "", "taken as evidence" if key in taken else "") if x)
                add("line", cut(line, 360), value, None if key in taken else "TAKE",
                    {"take": {"kind": "table row" if cor or loe else "line", "key": key, "text": line, "cor": cor, "loe": loe, "about": "",
                              "context": lines[i - 1] if i > 0 else ""}})
            if len(lines) > MAX_LINES:
                place["note"] = f"the page shows its first {MAX_LINES} of {len(lines)} lines"
        else:
            meta = {d["id"]: d for d in self.graph.documents()}
            for h in self.here["hits"]:
                d = meta.get(h["document"], {})
                about = ", ".join(str(x) for x in (d.get("body"), d.get("year")) if x)
                recs = self.index.recs.get(h["page"], [])
                graded = sum(1 for r in recs if r["cor"] or r["loe"])
                counts = f"{len(recs)} recommendations ({graded} graded)" if recs else "no recommendations"
                add("search hit", f'{h["documentTitle"]}{f" ({about})" if about else ""}, page {h["number"]}',
                    "; ".join(x for x in (h["best"], h.get("holds", ""), counts, self.page_value(h["page"])) if x), "OPEN", {"go": {"kind": "page", "id": h["page"]}})
            if self.here.get("absent"):
                place["note"] = f'no page of the searched text holds the word(s): {", ".join(self.here["absent"])}; the hits match only the other words'
            if not self.here["hits"]:
                place["note"] = "no page holds these words"
            if self.here["hits"] and self.aggregate:  # collecting is for questions that ask for a set or a count
                ops_extra = {"COLLECT": (f"Go through the {COLLECT_MAX} recommendations across the searched guidelines whose wording best matches "
                                         "this search and take every one that answers the question or belongs to the asked list "
                                         "(for questions about all, which, how many: these answers are spread over many pages).")}
            else:
                ops_extra = {}
        for i, e in enumerate(self.evidence):
            targets["DROP"][f"e{i + 1}"] = {"drop": i, "label": f'evidence [{i + 1}]: {cut(e["text"], 200)}', "value": ""}

        ops: dict[str, str] = {}
        if kind == "search":
            ops.update(ops_extra)
        if targets["OPEN"]:
            ops["OPEN"] = "Open an element: a guideline, a page, a search hit or a graded version."
        if targets["TAKE"]:
            ops["TAKE"] = "Take a recommendation or line of this page as evidence for the answer."
        if targets["DROP"]:
            ops["DROP"] = "Put back a piece of evidence that does not belong to the answer."
        sweepable = [k for k, t in targets["TAKE"].items() if t["take"]["kind"] in ("recommendation", "table row")]
        if len(sweepable) >= SWEEP_MIN:
            ops["SWEEP"] = (f"Go through all {len(sweepable)} recommendations of this page and take every one that answers the question "
                            "or belongs to the list it asks for (for questions about all, which, how many).")
        if kind == "page":
            p = self.graph.page(self.here["id"])
            if p["next"]:
                ops["NEXT_PAGE"] = f'Turn to page {p["number"] + 1}.'
            if p["previous"]:
                ops["PREVIOUS_PAGE"] = f'Turn to page {p["number"] - 1}.'
        if self.path:
            ops["BACK"] = f"Go back to {self.label(self.path[-1])}."
        doc = self.current_document()
        if doc:
            ops["SEARCH_GUIDELINE"] = f'Search the text of "{cut(self.doc_title(doc), 120)}" (a text model writes the query).'
        ops["SEARCH_LIBRARY"] = "Search the text of all guidelines (a text model writes the query)."
        check_state = (len(self.read_pages), tuple(e["key"] for e in self.evidence))
        if check_state != self.checked_key:  # a new page or new evidence: the answer may be checked again
            self.checked_key, self.answer_checks = check_state, 0
        breadth = not self.aggregate or (len(self.queries) >= 2 and any(h.get("action", "").startswith(("COLLECT", "SWEEP")) for h in self.history))
        if self.evidence and self.answer_checks < MAX_ANSWER_CHECKS and breadth:
            ops["ANSWER"] = "Answer now: the evidence taken answers the whole question or shows its premise is wrong."
        elif self.evidence and self.aggregate and not breadth:
            place["note_breadth"] = "this question asks for every element of a kind or several documents: answer only after at least two searches and a COLLECT or SWEEP"
        if self.absent_searches >= 2:
            # two library-wide searches reported a question word that no page of the library holds: the index itself
            # says the guidelines do not cover it, whatever was read (an unanswerable question otherwise burns every move
            # on rewordings of the same search)
            ops["NOT_COVERED"] = f"The library holds no page with: {', '.join(self.absent_words[:6])}; the guidelines do not cover the question."
        elif len(self.read_pages) >= 3 and len(self.queries) >= 2 and (len(self.history) >= MAX_MOVES // 2 or self.absent_seen):
            ops["NOT_COVERED"] = "The guidelines do not answer the question; the places where the answer should be were read."

        if self.stalled():
            progress = {"OPEN", "TAKE", "SWEEP", "COLLECT", "NEXT_PAGE", "PREVIOUS_PAGE", "SEARCH_GUIDELINE", "SEARCH_LIBRARY", "ANSWER", "NOT_COVERED"}
            ops = {op: text for op, text in ops.items() if op in progress}
            for k in [k for k, t in targets["OPEN"].items() if t["go"]["kind"] == "page" and t["go"]["id"] in self.read_pages]:
                del targets["OPEN"][k]
            if not targets["OPEN"]:
                ops.pop("OPEN", None)
            if len(self.history) >= MAX_MOVES // 2:
                ops.setdefault("NOT_COVERED", "The guidelines do not answer the question; the places where the answer should be were read.")
            place["note_stall"] = f"the last {STALL} moves read nothing new and took nothing: only moves that read a new page, take evidence, search or finish are offered"
        state = {
            "question": self.question,
            **place,
            "elements": elements,
            "evidence_taken": [f'[{i + 1}] {self.evidence_text(e)}' for i, e in enumerate(self.evidence)],
            "recent_actions": self.history[-HISTORY:],
            **({"still_missing": self.missing} if self.missing else {}),
            "moves_left": MAX_MOVES - len(self.history),
        }
        return state, elements, {"ops": ops, "targets": {op: t for op, t in targets.items() if op in ops}}

    def stalled(self) -> bool:
        """Whether the last STALL moves neither read a new page nor changed the evidence (jev-ultrafast: blocked after three
        actions without a page change)."""
        if len(self.history) < STALL:
            return False
        recent = self.history[-STALL:]
        return all(h.get("progress") is False for h in recent)

    def evidence_text(self, e: dict, limit: int = 400) -> str:
        grade = f" ({grade_text(e['cor'], e['loe'])})" if e["kind"] in ("recommendation", "table row") else " (no grade: a line of text)"
        if e["kind"] == "recommendation" and strength_text(e):
            grade = grade[:-1] + f"; {strength_text(e)})"
        if limit > 400 and e["kind"] == "recommendation":
            titles = {self.index.title_of(v["page"]) for v in self.graph.same_versions(e["text"]) if v["id"] != e["key"]} - {e["documentTitle"]}
            if titles:
                grade += f" [the same recommendation also appears in: {'; '.join(sorted(titles)[:6])}]"
        context = f' [context only, not evidence: the preceding line reads "{cut(e["context"], 300)}"]' if limit > 400 and e.get("context") else ""
        if e.get("unfit"):
            context += " [the answer check judged this piece as not fitting the question: do not use it]"
        return f'{e["documentTitle"]}, page {e["number"]}, {e["kind"]}{grade}: {cut(e["text"], limit)}{context}'

    # --- one decision ----------------------------------------------------------------------------------------------
    def choose(self, state: dict, space: dict) -> dict:
        ops, targets = space["ops"], space["targets"]
        questions = {"operation": {"type": "choice", "instructions": NEXT_ACTION, "criteria": dict(ops)}}
        for op, cands in targets.items():
            if len(cands) >= 2:
                questions[op.lower() + "_target"] = {
                    "type": "choice", "instructions": f"{NEXT_ACTION}\n\nOperation assumed: {op}. {TARGET}",
                    "criteria": {k: cut(f'[{k}] {c["label"]}', 140) for k, c in cands.items()}}  # values are in the state
        answers, usage = classify(state, questions)
        self.usd += usage["usd"]
        op_answer = validate_choice(answers.get("operation") or {}, ops)
        op = op_answer["choice"]
        target, target_probs = None, {}
        if op in targets:
            if len(targets[op]) == 1:
                target = next(iter(targets[op]))
            else:
                t = validate_choice(answers.get(op.lower() + "_target") or {}, targets[op])
                target, target_probs = t["choice"], t["probabilities"]
        return {"operation": op, "target": target, "operation_probabilities": op_answer["probabilities"],
                "target_probabilities": dict(sorted(target_probs.items(), key=lambda kv: -kv[1])[:5]), "usage": usage}

    def collect(self, state: dict) -> str:
        """One request with a yes/no question per recommendation that best matches the search, across its pages; every
        one judged fitting is taken. The counterpart of SWEEP for a whole search instead of one page."""
        here = self.here
        taken_keys = {e["key"] for e in self.evidence}
        cands = [r for r in self.index.search_recommendations(here["query"], here["scope"], COLLECT_MAX * 2) if r["id"] not in taken_keys][:COLLECT_MAX]
        if not cands:
            return "nothing left to collect for this search"
        pages = {}
        questions = {}
        for n, r in enumerate(cands):
            pg = pages.get(r["page"]) or self.graph.page(r["page"])
            pages[r["page"]] = pg
            questions[f"fit_{n}"] = {"type": "bool", "instructions": f"{SWEEP} Candidate [{n}] ({pg['documentTitle']}, page {pg['number']}; {grade_text(r['cor'], r['loe'])}; {strength_text(r)}): {cut(r['text'], 500)}",
                                     "criteria": {"true": "Yes: it states an answer element for the asked situation or belongs to the asked list.",
                                                  "false": "No: it is about something else, another group, or does not state what is asked."}}
        answers, usage = classify({k: v for k, v in state.items() if k != "elements"}, questions)
        self.usd += usage["usd"]
        taken, probs = [], {}
        for n, r in enumerate(cands):
            a = answers.get(f"fit_{n}") or {}
            prob = a.get("noul", a.get("probability"))
            if not isinstance(prob, (int, float)):
                continue
            probs[n] = round(prob, 3)
            if prob >= SWEEP_TAKE:
                pg = pages[r["page"]]
                about = next((x["about"] for x in pg["recommendations"] if x["id"] == r["id"]), "")
                self.evidence.append({"kind": "recommendation", "key": r["id"], "text": r["text"], "cor": r["cor"], "loe": r["loe"], "about": about,
                                      "modality": r.get("modality", ""), "polarity": r.get("polarity", ""),
                                      "page": pg["id"], "number": pg["number"], "document": pg["document"], "documentTitle": pg["documentTitle"]})
                self.read_pages.add(pg["id"])
                taken.append(n)
        self.trace.append({"type": "collect", "query": here["query"], "candidates": len(cands), "taken": taken, "probabilities": probs, "usage": usage})
        return f"collected over {len(cands)} matching recommendations from {len(pages)} pages, took {len(taken)}"

    def sweep(self, state: dict, space: dict) -> str:
        """One request with a yes/no question per recommendation of the page; every one judged fitting is taken."""
        cands = {k: t for k, t in space["targets"]["TAKE"].items() if t["take"]["kind"] in ("recommendation", "table row")}
        keys = list(cands)[:SWEEP_MAX]
        questions = {f"fit_{k}": {"type": "bool", "instructions": f"{SWEEP} Candidate [{k}]: {cut(cands[k]['label'], 500)} ({cands[k]['value']})",
                                  "criteria": {"true": "Yes: it states an answer element for the asked situation or belongs to the asked list.",
                                               "false": "No: it is about something else, another group, or does not state what is asked."}}
                     for k in keys}
        answers, usage = classify({k: v for k, v in state.items() if k != "elements"}, questions)
        self.usd += usage["usd"]
        p = self.graph.page(self.here["id"])
        taken, probs = [], {}
        for k in keys:
            a = answers.get(f"fit_{k}") or {}
            prob = a.get("noul", a.get("probability"))
            if not isinstance(prob, (int, float)):
                continue
            probs[k] = round(prob, 3)
            if prob >= SWEEP_TAKE:
                self.evidence.append({**cands[k]["take"], "page": p["id"], "number": p["number"], "document": p["document"],
                                      "documentTitle": p["documentTitle"]})
                taken.append(k)
        self.trace.append({"type": "sweep", "candidates": len(keys), "taken": taken, "probabilities": probs, "usage": usage})
        return f"swept {len(keys)} recommendations, took {len(taken)}"

    # --- execution -------------------------------------------------------------------------------------------------
    def go(self, here: dict) -> str:
        self.path.append(self.here)
        return self.arrive(here)

    def arrive(self, here: dict) -> str:
        self.here = here
        if here["kind"] == "document":
            self.opened_docs.add(here["id"])
            return f"{len(self.graph.toc(here['id']))} pages listed"
        if here["kind"] == "page":
            self.read_pages.add(here["id"])
            self.page_visits.append(here["id"])
            self.opened_docs.add(self.graph.page(here["id"])["document"])
            p = self.graph.page(here["id"])
            graded = sum(1 for r in p["recommendations"] if r["cor"] or r["loe"])
            return f'{len(p["recommendations"])} recommendations ({graded} graded), {len(lines_of(p["text"]))} lines'
        return ""

    def search(self, scope: str | None) -> str:
        content = {"question": self.question, "scope": self.doc_title(scope) if scope else "all guidelines",
                   "place": self.label(self.here), "evidence_taken": [self.evidence_text(e) for e in self.evidence],
                   "recent_actions": self.history[-HISTORY:], **({"still_missing": self.missing} if self.missing else {})}
        out, usage = write_json(QUERY, content, {"query"}, max_tokens=4000, effort=QUERY_EFFORT)  # a short rewrite: less reasoning than the answer
        self.usd += usage["usd"]
        query = cut(str(out["query"]), 120)
        self.queries.append(query)
        hits = self.index.search(query, scope)
        absent = self.index.absent(query, scope)
        self.absent_seen = self.absent_seen or (bool(absent) and scope is None)
        if absent and scope is None:
            self.absent_searches += 1
            self.absent_words += [w for w in absent if w not in self.absent_words]
        self.path.append(self.here)
        self.here = {"kind": "search", "query": query, "scope": scope, "hits": hits, "absent": absent}
        self.trace.append({"type": "writer", "task": "query", "query": query, "usage": usage})
        return f'searched "{query}": {len(hits)} pages' + (f'; no page holds: {", ".join(absent)}' if absent else "")

    def no_evidence_text(self) -> str:
        """What the walk can say at its move limit without evidence: what the index knows (words no page holds) or
        what was searched and read without finding a statement."""
        if self.absent_words:
            return (f"No guideline in the library addresses the question: no page holds {', '.join(self.absent_words[:6])} "
                    f"({self.absent_searches} searches of all guidelines).")
        return (f"No guideline statement about the question was found in {len(self.read_pages)} pages read and "
                f"{len(self.queries)} searches; the walk ended at its move limit without evidence.")

    def write_answer(self, final: bool) -> dict | None:
        """The text model writes the answer from the evidence; an incomplete answer sends the reader on, twice at most."""
        per_piece = 1500 if len(self.evidence) <= 20 else 500  # many pieces (COLLECT): shorter each, so the writer's input stays bounded
        content = {"question": self.question, "evidence": [f"[{i + 1}] {self.evidence_text(e, per_piece)}" for i, e in enumerate(self.evidence)]}
        content["language"] = "write the answer in English" if re.search(r"\b(the|what|which|how|does|do|is|are)\b", self.question, re.I) else "write the answer in the language of the question"
        if getattr(self, "precision_hint", False):  # Walker V5: the writer answers without reasoning
            content["precision"] = ("give exactly what the question asks: when it asks for a limit, bound, threshold or percentage, quote the piece that states that exact value for the asked group and endpoint, even if other pieces state a related value more often; when no piece states it, the status is incomplete, never a neighbouring value; when it compares two groups or guidelines, state the asked class and the patients covered for each")
        asked_class, asked_level = asks_grade(self.question)
        if asked_class and not asked_level:  # the question asks for classes: the writer adds no levels of evidence on its own
            content["asked"] = "the class of recommendation; the question does not ask for a level of evidence, so give the classes only"
        out, usage = write_json(ANSWER, content, {"status", "missing", "answer", "answer_items", "count", "unfit"}, max_tokens=12000)
        self.usd += usage["usd"]
        self.trace.append({"type": "writer", "task": "answer", "output": out, "usage": usage})
        status = out["status"] if out["status"] in ("answered", "contradicted", "not_covered", "incomplete") else "answered"
        unfit = sorted({int(x) for x in (out.get("unfit") or []) if str(x).isdigit() and 1 <= int(x) <= len(self.evidence)}, reverse=True)
        # the navigator chose ANSWER but the writer finds the evidence short: the pieces it rejects are put back and
        # the reader goes on; at most MAX_ANSWER_CHECKS checks for the same evidence
        used = [e for e in self.evidence if not e.get("unfit")]
        if status == "contradicted" and not final and not any(e.get("cor") or e.get("loe") for e in used):
            # a premise is shown wrong only with the graded recommendation that states what the guideline says instead
            status, out["missing"] = "incomplete", out.get("missing") or "the graded recommendation (with its class or level) that states what the guideline says instead of the premise"
        if status == "answered" and not final and self.answer_checks < MAX_ANSWER_CHECKS:
            # grade guard: the question asks for a class or level, a piece used carries none, and the same guideline states
            # the fully graded version of that statement on another page: the reader fetches it instead of answering
            # "not given" (what the writer does by reasoning when its effort is high, here without any model call)
            fuller = self.fuller_versions(used)
            if fuller:
                e, g = fuller[0]
                gp = self.graph.page(g["page"])
                status = "incomplete"
                out["missing"] = f"the fully graded version ({grade_text(g['cor'], g['loe'])}, {gp['documentTitle']}, page {gp['number']}) of: {cut(e['text'], 160)}"
                self.trace.append({"type": "guard", "guard": "grade", "piece": e["key"], "page": g["page"]})
        if status in ("incomplete", "not_covered") and not final:
            self.answer_checks += 1
            for n in unfit:  # kept, so the navigator still sees them, but marked: the writer and the answer ignore them
                self.evidence[n - 1]["unfit"] = True
            self.missing = cut(str(out["missing"]), 300) or "the evidence does not answer the question"
            note = f"; {len(unfit)} pieces marked as not fitting the question" if unfit else ""
            return {"result": f"answer check: still missing {self.missing}{note}"}
        items = out.get("answer_items")
        count = out.get("count")
        return {"finish": "not covered" if status in ("not_covered", "incomplete") else status, "answer": str(out["answer"]),
                "answer_items": [str(x) for x in items] if isinstance(items, list) else [],
                "count": count if isinstance(count, int) else None}

    def fuller_versions(self, used: list[dict]) -> list[tuple[dict, dict]]:
        """For a question asking a class or level: the pieces used that lack it, each with the fully graded version of the
        same statement in the same document on another page (the matching of graded_pages), when the graph holds one
        and it is not taken yet."""
        asked_class, asked_level = asks_grade(self.question)
        out = []
        if not (asked_class or asked_level):
            return out
        taken = {e["key"] for e in used}
        for e in used:
            lacks = (asked_class and not e.get("cor")) or (asked_level and not e.get("loe"))
            if e["kind"] not in ("recommendation", "table row", "line") or not lacks:
                continue
            document = self.graph.page(e["page"])["document"]
            for g in self.graph.graded_versions(e["text"], 0.5)[:3]:
                if g["id"] in taken or g["page"] == e["page"] or not (g["cor"] and g["loe"]):
                    continue
                if self.graph.page(g["page"])["document"] == document:
                    out.append((e, g))
                    break
        return out

    def finish(self, how: str, answer: str, answer_items: list | None = None, count: int | None = None) -> None:
        sources = "\n".join(f"[{i + 1}] {self.evidence_text(e)}" for i, e in enumerate(self.evidence))
        self.finished = {"result": how, "answer": answer + (f"\n\nEvidence:\n{sources}" if sources else ""),
                         "answer_items": answer_items or [], "count": count, "evidence": [dict(e) for e in self.evidence]}

    def execute(self, decision: dict, space: dict) -> str:
        op, target = decision["operation"], decision["target"]
        action = space["targets"].get(op, {}).get(target) if target else None
        if op == "OPEN":
            return self.go(action["go"])
        if op == "TAKE":
            p = self.graph.page(self.here["id"])
            self.evidence.append({**action["take"], "page": p["id"], "number": p["number"], "document": p["document"],
                                  "documentTitle": p["documentTitle"]})
            return f'took: {cut(action["take"]["text"], 160)}'
        if op == "SWEEP":
            return self.sweep(decision["state"], space)
        if op == "COLLECT":
            return self.collect(decision["state"])
        if op == "DROP":
            dropped = self.evidence.pop(action["drop"])
            return f'put back: {cut(dropped["text"], 120)}'
        if op in ("NEXT_PAGE", "PREVIOUS_PAGE"):
            p = self.graph.page(self.here["id"])
            return self.go({"kind": "page", "id": p["next"] if op == "NEXT_PAGE" else p["previous"]})
        if op == "BACK":
            return self.arrive(self.path.pop()) or "back"
        if op == "SEARCH_GUIDELINE":
            return self.search(self.current_document())
        if op == "SEARCH_LIBRARY":
            return self.search(None)
        if op in ("ANSWER", "NOT_COVERED"):
            if not self.evidence:
                self.finish("not covered", "The guidelines in the library do not answer this question.")
                return "finished: not covered"
            out = self.write_answer(final=op == "NOT_COVERED")
            if "finish" in out:
                self.finish(out["finish"], out["answer"], out["answer_items"], out["count"])
                return f'finished: {out["finish"]}'
            return out["result"]
        raise ValueError(f"unknown operation {op}")

    def describe(self, decision: dict, space: dict) -> str:
        op, target = decision["operation"], decision["target"]
        if target:
            return f'{op} [{target}] {cut(space["targets"][op][target]["label"], 160)}'
        return f'{op}: {space["ops"][op]}'

    def classify_question(self) -> None:
        """One yes/no question at the start: does the question ask for every element of a kind, several documents or a count?"""
        answers, usage = classify({"question": self.question}, {"aggregate": {
            "type": "bool",
            "instructions": "Does the question ask for a set of elements (all or which interventions, drugs, tests, recommendations, "
                            "documents or guidelines that ...) or for how many there are?",
            "criteria": {"true": "Yes: it asks for every element of a kind, for the documents that state something, or for a count.",
                         "false": "No: it asks for specific facts: a class, a level, a value or a statement, even for two situations or two "
                                  "guidelines compared with each other."}}})
        self.usd += usage["usd"]
        prob = (answers.get("aggregate") or {}).get("noul", 0)
        self.aggregate = isinstance(prob, (int, float)) and prob >= 0.5
        self.trace.append({"type": "question", "aggregate": self.aggregate, "p": round(prob, 3) if isinstance(prob, (int, float)) else None, "usage": usage})

    def step(self) -> None:
        if self.aggregate is None:
            self.classify_question()
        state, elements, space = self.observe()
        decision = self.choose(state, space)
        decision["state"] = state
        action = self.describe(decision, space)
        place = self.label(self.here)
        before = (len(self.read_pages), len(self.evidence), len(self.queries))
        result = self.execute(decision, space)
        progress = (len(self.read_pages), len(self.evidence), len(self.queries)) != before or self.finished is not None
        self.history.append({"move": len(self.history) + 1, "at": place, "action": action, "result": result, "progress": progress})
        self.trace.append({"type": "step", "move": len(self.history), "place": place, "elements": len(elements),
                           "operations": list(space["ops"]), **{k: v for k, v in decision.items() if k != "state"}, "action": action, "result": result})

    def run(self) -> dict:
        while self.finished is None:
            try:
                if len(self.history) >= MAX_MOVES:
                    if self.evidence:
                        out = self.write_answer(final=True)
                        self.finish("unfinished", out["answer"], out["answer_items"], out["count"])
                    else:
                        self.finish("unfinished", self.no_evidence_text())
                    break
                self.step()
            except ModelError as e:
                self.trace.append({"type": "error", "error": str(e)})
                self.finish("error", f"The walk stopped: {e}")
        return {**self.finished, "moves": len(self.history), "usd": round(self.usd, 5)}
