"""Removing HTML tags from the OCR'd guideline pages without removing guideline text.

The pages are Markdown with HTML tables. A pattern like <[^>]+> treats every "<" as the start of a tag, so clinical
text such as "AHI < 15/h ... LVEF > 40 %" lost everything between the two signs; 424 of the 2,780 pages lost text
that way, up to whole paragraphs. A tag here is "<", an optional "/", a tag name starting with a letter, optional
attributes, and ">". Everything else stays text; entities (&#x3C;, &gt;) are decoded by the callers that want plain
text.
"""
import re

TAG = re.compile(r'</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>')


def strip_tags(text, repl=' '):
  """text with real HTML tags replaced by `repl`; a "<" or ">" in the text itself is kept"""
  return TAG.sub(repl, text or '')
