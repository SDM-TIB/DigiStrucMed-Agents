"""Text as the walker shows it: page OCR normalized to lines, headings, words. A port of the switch walker's text.ts."""
import html
import re

_HYPHEN = re.compile(r"([a-zß-ÿ])-\s*\n\s*([a-zß-ÿ])")
_SPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    """OCR line breaks and PDF hyphenation joined, whitespace collapsed."""
    return _SPACE.sub(" ", _HYPHEN.sub(r"\1\2", text or "")).strip()


def cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a word boundary, with an ellipsis when cut."""
    if len(text) <= limit:
        return text
    head = text[: max(0, limit - 1)]
    space = head.rfind(" ")
    return (head[:space] if space > limit * 0.5 else head).rstrip() + "…"


def _strip_tags(markup: str) -> str:
    return clean(re.sub(r"<[^>]*>", " ", markup))


def _table_rows(match: re.Match) -> str:
    rows = []
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", match.group(0), flags=re.I):
        cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>([\s\S]*?)</t[hd]>", row, flags=re.I)]
        line = " | ".join(c for c in cells if c)
        if line:
            rows.append(line)
    return "\n".join(rows)


def normalize_page(text: str) -> str:
    """Each HTML table row becomes one line of its cells joined by " | "; inline tags go; entities are decoded."""
    tables = re.sub(r"<table[\s\S]*?</table>", _table_rows, text or "", flags=re.I)
    return html.unescape(re.sub(r"</?(?:b|i|u|em|strong|sup|sub|span|br|p|div)[^>]*>", "", tables, flags=re.I))


_BOILERPLATE = re.compile(
    r"downloaded from|onlinelibrary\.wiley|terms and conditions|creative commons|all rights reserved|^https?://|^doi:"
    r"|^\d{6,}, \d{4}|^©|^\(?\s*\)?$|^page \d+( of \d+)?$",
    re.I,
)
_BULLETS = re.compile(r"\s*[○•●▪◦■□➢►]\s*")
_RULE = re.compile(r"^[\s|:\-–—=_]+$")


def is_boilerplate(line: str) -> bool:
    return bool(_BOILERPLATE.search(line.strip()))


def lines_of(page: str) -> list[str]:
    """The lines a reader can point at: bullets split into items, table rows kept whole, furniture and repeats dropped."""
    out, seen = [], set()
    for raw in normalize_page(page).splitlines():
        if not raw.strip() or _RULE.match(raw):
            continue
        item = re.sub(r"^\s*(?:[*-]|\d+\.)\s+", "", raw)
        if "|" in item:
            cells = [c.strip() for c in re.sub(r"\|\s*$", "", re.sub(r"^\s*\|", "", item)).split("|")]
            parts = [" | ".join(c for c in cells if c)]
        else:
            parts = _BULLETS.split(item)
        for part in parts:
            line = clean(re.sub(r"\*\*|__|`", "", re.sub(r"^#+\s*", "", part)))
            if len(re.findall(r"\w", line)) < 3 or line in seen or is_boilerplate(line):
                continue
            seen.add(line)
            out.append(line)
    return out


def outline_of(page: str) -> list[str]:
    """Headings, bold lines and table or figure captions in order; else the first line that is not furniture."""
    out = []
    for raw in normalize_page(page).splitlines():
        line = raw.strip()
        m = (
            re.match(r"^#{1,6}\s+(.*)$", line)
            or re.match(r"^\*\*([^*]{3,200})\*\*\s*$", line)
            or re.match(r"^(?:\*\*)?((?:Table|Figure|Box|Abbildung|Tabelle)\s+\d+[^|]{0,180})", line, re.I)
        )
        if m:
            out.append(clean(m.group(1).replace("**", "")))
    if out:
        return list(dict.fromkeys(out))
    first = lines_of(page)[:1]
    return first


_NOISE = re.compile(r"^(?:COR|LOE|Recommendations?|Synopsis|Cause|Table \d+|\d+\.?)$|rowspan|colspan|^COR:|^LOE:", re.I)


def heading_of_path(path: str) -> str:
    """A passage's header path as a heading: its segments without grading columns, markup or bare numbers."""
    return " > ".join(s for s in (clean(x) for x in (path or "").split(" > ")) if s and not _NOISE.search(s))


def words_of(text: str) -> list[str]:
    """Lowercase words for matching: letters and digits only."""
    return re.findall(r"[^\W_]+", (text or "").lower())
