"""The guideline knowledge graph (final graph 28.10, sha256 2152f227…f982) read over SPARQL from QLever.

The queries are the switch walker's (sparql.ts) character for character, so its on-disk cache of results (SHA-256 of
prefixes plus query) is reused: a run on cached data needs no graph server. One query is new: every recommendation
with its grade, for finding the graded copy of an ungraded recommendation.
"""
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
from pathlib import Path

from .text import clean, cut, heading_of_path, lines_of, normalize_page, outline_of, words_of

PREFIXES = """PREFIX y: <urn:mhh:final:20261028:>
PREFIX x: <urn:mhh:final:20261018:>
PREFIX i: <urn:mhh:integration:20261011:>
PREFIX d: <http://digistrucmed.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
HERE = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = HERE.parent / "2026-10-31_clef_harness" / "harness" / "cache" / "sparql"
SLOW_SECONDS = float(os.environ.get("GRAPH_SLOW_SECONDS", 5))  # a query slower than this leaves a log line
# a page is five queries and a walk reads a dozen pages: the queries that do not depend on one another go out together
# (single queries only run on this pool, so it cannot wait on itself)
_QUERIES = ThreadPoolExecutor(int(os.environ.get("GRAPH_PARALLEL", 16)), thread_name_prefix="walker-graph")
ROLE_ORDER = ["population", "condition", "intervention", "comparator", "outcome"]


class GraphError(Exception):
    pass


def _local(iri: str) -> str:
    import re

    return urllib.parse.unquote(re.sub(r"^.*[/#:]", "", iri)).replace("_", " ")


def _num(v):
    return None if v in (None, "") else float(v) if "." in v else int(v)


def _str(v):
    return None if v in (None, "") else v


def _top_headings(headings, n):
    return [cut(h, 90) for h in dict.fromkeys(h for h in headings if h)][:n]


def about_of(rows) -> dict[str, str]:
    """Each recommendation's roles as one line: who first, then the situation, then what."""
    by_rec: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        roles = by_rec.setdefault(row.get("r", ""), {})
        texts = roles.setdefault(row.get("rt", "other"), [])
        text = clean(row.get("rx", ""))
        if text and text not in texts:
            texts.append(text)

    def rank(t):
        return ROLE_ORDER.index(t) if t in ROLE_ORDER else len(ROLE_ORDER)

    return {
        rec: cut("; ".join(f"{t}: {', '.join(x)}" for t, x in sorted(roles.items(), key=lambda kv: rank(kv[0])) if t != "unspecified"), 360)
        for rec, roles in by_rec.items()
    }


class Graph:
    def __init__(self, endpoint: str | None = None, cache: Path | None = None):
        self.endpoint = endpoint or os.environ.get("GRAPH_ENDPOINT", "http://localhost:7001/")
        self.cache = Path(cache or os.environ.get("SPARQL_CACHE", DEFAULT_CACHE))
        self._lock = threading.RLock()  # memoized loaders call one another
        self._memo: dict[str, object] = {}
        self._gates: dict[object, threading.Lock] = {}  # one per value being loaded: other values load meanwhile

    # --- transport -------------------------------------------------------------------------------------------------
    def select(self, query: str) -> list[dict[str, str]]:
        text = PREFIXES + query
        file = self.cache / f"{hashlib.sha256(text.encode()).hexdigest()}.json"
        if file.exists():
            return json.loads(file.read_text(encoding="utf-8"))
        body = urllib.parse.urlencode({"query": text}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/sparql-results+json"})
        for attempt in range(3):
            asked = time.time()
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    bindings = json.load(r)["results"]["bindings"]
                if time.time() - asked > SLOW_SECONDS:  # a slow query leaves a log line, like a slow model call
                    print(f"walker graph: query slow, {time.time() - asked:.1f} s: {' '.join(query.split())[:120]}", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                print(f"walker graph: query failed after {time.time() - asked:.1f} s, attempt {attempt + 1}: {type(e).__name__}", flush=True)
                if attempt == 2:
                    raise GraphError(f"SPARQL failed: {e}") from None
        rows = [{k: v["value"] for k, v in b.items()} for b in bindings]
        self.cache.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
        return rows

    def _once(self, key, make):
        with self._lock:
            if key in self._memo:
                return self._memo[key]
            gate = self._gates.setdefault(key, threading.Lock())
        with gate:  # the same value is loaded once; different values load side by side
            with self._lock:
                if key in self._memo:
                    return self._memo[key]
            value = make()
            with self._lock:
                self._memo[key] = value
                self._gates.pop(key, None)
            return value

    def pages(self, pids) -> None:
        """Load several pages side by side, so that page() answers from memory afterwards. A page that fails here
        fails again, with its error, where it is read."""
        todo = [p for p in dict.fromkeys(pids) if ("page", p) not in self._memo]
        if len(todo) < 2:
            return

        def load(pid):
            try:
                self.page(pid)
            except GraphError:
                pass
        with ThreadPoolExecutor(min(8, len(todo)), thread_name_prefix="walker-pages") as pool:
            list(pool.map(load, todo))

    # --- library ---------------------------------------------------------------------------------------------------
    def documents(self) -> list[dict]:
        def make():
            rows = self.select("""SELECT ?d (SAMPLE(?t) AS ?title) (SAMPLE(?k) AS ?key) (SAMPLE(?yr) AS ?year)
  (SAMPLE(?b) AS ?body) (SAMPLE(?ty) AS ?type) (SAMPLE(?l) AS ?lang) (SAMPLE(?n) AS ?pages)
WHERE { ?d a y:Document ; y:documentKey ?k ; y:pageCount ?n .
  OPTIONAL { ?d y:title ?t } OPTIONAL { ?d y:year ?yr } OPTIONAL { ?d y:issuingBody ?b }
  OPTIONAL { ?d y:documentType ?ty } OPTIONAL { ?d y:language ?l } }
GROUP BY ?d""")
            docs = [{"id": r.get("d", ""), "title": clean(r.get("title") or r.get("key", "")), "year": _num(r.get("year")),
                     "body": _str(r.get("body")), "type": _str(r.get("type")), "language": _str(r.get("lang")),
                     "pages": int(r.get("pages") or 0)} for r in rows]
            return sorted(docs, key=lambda d: d["title"])
        return self._once("documents", make)

    def toc(self, document: str) -> list[dict]:
        def make():
            iri = f"<{document}>"
            texts = self.select(f"SELECT ?pg ?no ?t WHERE {{ {iri} d:hasPage ?pg . ?pg i:technicalPageNumber ?no ; i:originalOcr ?t }}")
            paths = self.select(f"SELECT ?pg ?start ?h WHERE {{ {iri} d:hasPage ?pg . ?ps y:onPage ?pg ; y:pageCharStart ?start ; y:headerPath ?h }}")
            counts = self.select(f"""SELECT ?pg (COUNT(DISTINCT ?r) AS ?n) WHERE {{ {iri} d:hasPage ?pg . ?ps y:onPage ?pg .
  ?r a y:Recommendation ; y:inPassage ?ps }} GROUP BY ?pg""")
            recs = {r.get("pg", ""): int(r.get("n") or 0) for r in counts}
            by_page: dict[str, list] = {}
            for r in paths:
                by_page.setdefault(r.get("pg", ""), []).append((int(float(r.get("start") or 0)), heading_of_path(r.get("h", ""))))
            raw = []
            for r in texts:
                page, text = r.get("pg", ""), r.get("t", "")
                passage = [h for _, h in sorted(by_page.get(page, []))]
                raw.append((page, text, int(r.get("no") or 0), list(dict.fromkeys(outline_of(text) + passage))))
            seen: dict[str, int] = {}
            for _, _, _, hs in raw:
                for h in hs:
                    seen[h] = seen.get(h, 0) + 1
            limit = max(3, len(raw) * 0.2)
            out = [{"page": p, "number": n, "headings": _top_headings([h for h in hs if seen[h] <= limit], 4),
                    "recommendations": recs.get(p, 0), "opening": cut((lines_of(t) or [""])[0], 140)} for p, t, n, hs in raw]
            return sorted(out, key=lambda e: e["number"])
        return self._once(("toc", document), make)

    def page(self, pid: str) -> dict:
        def make():
            iri = f"<{pid}>"
            q_meta = (f"""SELECT ?doc (SAMPLE(?title) AS ?dt) (SAMPLE(?k) AS ?key) ?no ?t WHERE {{
  ?doc a y:Document ; d:hasPage {iri} ; y:documentKey ?k . {iri} i:technicalPageNumber ?no ; i:originalOcr ?t .
  OPTIONAL {{ ?doc y:title ?title }} }} GROUP BY ?doc ?no ?t""")
            q_recs = (f"""SELECT ?r (SAMPLE(?txt) AS ?text) (SAMPLE(?c) AS ?cor) (SAMPLE(?l) AS ?loe) (SAMPLE(?m) AS ?mod)
  (SAMPLE(?p) AS ?pol) (SAMPLE(?h) AS ?head) (MIN(?s) AS ?start)
WHERE {{ ?ps y:onPage {iri} ; y:pageCharStart ?s . ?r a y:Recommendation ; y:inPassage ?ps ; y:statementText ?txt ;
  y:modality ?m ; y:polarity ?p . OPTIONAL {{ ?ps y:headerPath ?h }}
  OPTIONAL {{ ?r y:classOfRecommendationRaw ?c }} OPTIONAL {{ ?r y:levelOfEvidenceRaw ?l }} }}
GROUP BY ?r ORDER BY ?start""")
            q_concepts = (f"""SELECT ?c (SAMPLE(?lab) AS ?label) (SUM(?m) AS ?mentions) WHERE {{
  ?link x:page {iri} ; x:concept ?c ; x:mentionCount ?m . OPTIONAL {{ ?c rdfs:label ?lab }} }}
GROUP BY ?c ORDER BY DESC(?mentions)""")
            q_roles = (f"""SELECT ?r ?rt ?rx WHERE {{ ?ps y:onPage {iri} . ?r a y:Recommendation ; y:inPassage ?ps ; y:role ?m .
  ?m y:roleType ?rt ; y:text ?rx }}""")
            meta, rec_rows, concept_rows, role_rows = _QUERIES.map(self.select, [q_meta, q_recs, q_concepts, q_roles])
            if not meta:
                raise GraphError(f"no page {pid}")
            head = meta[0]
            doc, number = head.get("doc", ""), int(head.get("no") or 0)
            neighbours = self.select(f"""SELECT ?pg ?no WHERE {{ <{doc}> d:hasPage ?pg . ?pg i:technicalPageNumber ?no .
  FILTER(?no = {number - 1} || ?no = {number + 1}) }}""")
            at = {int(r["no"]): r["pg"] for r in neighbours}
            about = about_of(role_rows)
            text = head.get("t", "")
            recs = [{"id": r.get("r", ""), "text": clean(r.get("text", "")), "cor": _str(r.get("cor")), "loe": _str(r.get("loe")),
                     "modality": r.get("mod", ""), "polarity": r.get("pol", ""),
                     "heading": heading_of_path(r.get("head", "")), "about": about.get(r.get("r", ""), "")} for r in rec_rows]
            concepts = [{"id": r.get("c", ""), "label": clean(r.get("label") or _local(r.get("c", ""))),
                         "mentions": int(float(r.get("mentions") or 0))} for r in concept_rows]
            return {"id": pid, "document": doc, "documentTitle": clean(head.get("dt") or head.get("key", "")), "number": number,
                    "text": text, "headings": _top_headings(outline_of(text) + [r["heading"] for r in recs], 6),
                    "recommendations": recs, "concepts": concepts, "previous": at.get(number - 1), "next": at.get(number + 1)}
        return self._once(("page", pid), make)

    def texts(self) -> list[dict]:
        """Every page's normalized text, for search; read once."""
        def make():
            rows = self.select("""SELECT ?pg ?no ?doc (SAMPLE(?title) AS ?dt) (SAMPLE(?k) AS ?key) ?t WHERE {
  ?doc a y:Document ; d:hasPage ?pg ; y:documentKey ?k . ?pg i:technicalPageNumber ?no ; i:originalOcr ?t .
  OPTIONAL { ?doc y:title ?title } } GROUP BY ?pg ?no ?doc ?t""")
            pages = [{"page": r.get("pg", ""), "document": r.get("doc", ""), "documentTitle": clean(r.get("dt") or r.get("key", "")),
                      "number": int(r.get("no") or 0), "text": normalize_page(r.get("t", ""))} for r in rows]
            return sorted(pages, key=lambda p: (p["documentTitle"], p["number"]))
        return self._once("texts", make)

    def recommendations(self) -> list[dict]:
        """Every recommendation of the recommendation layer with its page and grade (new query, not in the old cache)."""
        def make():
            rows, size = [], 1000
            while True:  # paged: replies above about 1 MB break off over the IAP tunnel
                part = self.select(f"""SELECT ?r ?pg ?text ?cor ?loe ?mod ?pol WHERE {{
  ?r a y:Recommendation ; y:inPassage ?ps ; y:statementText ?text ; y:modality ?mod ; y:polarity ?pol . ?ps y:onPage ?pg .
  OPTIONAL {{ ?r y:classOfRecommendationRaw ?cor }} OPTIONAL {{ ?r y:levelOfEvidenceRaw ?loe }} }}
ORDER BY ?r ?pg LIMIT {size} OFFSET {len(rows)}""")
                rows += part
                if len(part) < size:
                    break
            rows = list({(r.get("r"), r.get("pg")): r for r in rows}.values())
            return [{"id": r.get("r", ""), "page": r.get("pg", ""), "text": clean(r.get("text", "")),
                     "cor": _str(r.get("cor")), "loe": _str(r.get("loe")), "modality": r.get("mod", ""), "polarity": r.get("pol", "")} for r in rows]
        return self._once("recommendations", make)

    def page_concepts(self) -> list[dict]:
        """Every page-concept link of the 18.10 layer (x:graph:page-concepts): the page, the concept's label and how many
        detected mentions on the page are linked to it (selected UMLS concept on both sides)."""
        def make():
            rows, size = [], 5000
            while True:  # paged, as recommendations()
                part = self.select(f"""SELECT ?pg ?c ?lab ?m WHERE {{ ?link x:page ?pg ; x:concept ?c ; x:mentionCount ?m .
  ?c rdfs:label ?lab }} ORDER BY ?link ?lab LIMIT {size} OFFSET {len(rows)}""")
                rows += part
                if len(part) < size:
                    break
            return [{"page": r.get("pg", ""), "concept": r.get("c", ""), "label": clean(r.get("lab", "")),
                     "mentions": int(float(r.get("m") or 0))} for r in rows]
        return self._once("page_concepts", make)

    def same_versions(self, text: str, threshold: float = 0.8) -> list[dict]:
        """Every recommendation (graded or not) that says the same as `text`, on any page, best first."""
        allrecs = self._once("allwords", lambda: [(set(words_of(r["text"])), r) for r in self.recommendations()])
        words = set(words_of(text))
        if not words:
            return []
        scored = [(len(words & w) / len(words | w), r) for w, r in allrecs if w]
        return [r for j, r in sorted(scored, key=lambda x: -x[0]) if j >= threshold]

    def graded_versions(self, text: str, threshold: float = 0.6) -> list[dict]:
        """The graded recommendations that say the same as `text` (word overlap, Jaccard >= threshold), best first."""
        graded = self._once("graded", lambda: [(set(words_of(r["text"])), r) for r in self.recommendations() if r["cor"] or r["loe"]])
        words = set(words_of(text))
        if not words:
            return []
        scored = [(len(words & w) / len(words | w), r) for w, r in graded if w]
        return [r for j, r in sorted(scored, key=lambda x: -x[0]) if j >= threshold]
