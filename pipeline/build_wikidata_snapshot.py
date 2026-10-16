"""Wikidata facts for every graph concept with a link, fetched once: app/data/wikidata_snapshot.json.gz

  python pipeline/build_wikidata_snapshot.py

the app reads only this snapshot, it never queries Wikidata while someone uses it. a dated snapshot with the revision
of every item makes what the cards show reproducible. Wikidata content is CC0.

1. resolve, as app/wikidata.py describes: the concept's UMLS CUI (from the graph, else the CUI of the final MHH graph,
   selected or candidate) -> the item with that CUI (P2892, one SPARQL query per 200 CUIs). several items for one CUI:
   the graph's own QID if it is among them, else the lowest Q number. also fetched: the graph's QIDs and the Wikidata
   items the CLEF selector chose in the final MHH graph
2. every resolved item through wbgetentities (50 per request): labels and descriptions in the five app languages,
   English Wikipedia, image (P18), identifiers and the relations the cards show. truthy statements only, as wdt: in
   SPARQL: preferred rank where there is one, else normal; deprecated never
3. labels of the related items (side effects, interactions, ...) in the five languages
"""
import datetime as dt
import gzip
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from wikidata import IDENTIFIERS, LINKS, RELATIONS, USER_AGENT  # noqa: E402  the same properties the app shows

OUT = ROOT / 'app' / 'data' / 'wikidata_snapshot.json.gz'
SPARQL = 'https://query.wikidata.org/sparql'
API = 'https://www.wikidata.org/w/api.php'
LANGS = ('en', 'de', 'es', 'ru', 'hi')
PROPS = {p for _, p, _ in RELATIONS} | {p for _, p, _ in IDENTIFIERS} | {'P18'}


def get(url, params, tries=12):
  """one api request. network errors, and maxlag should a server send it, are retried with growing waits from 5 s"""
  req = urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
  for attempt in range(tries):
    try:
      with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
      if 'error' in data:
        if data['error'].get('code') == 'maxlag':
          raise RuntimeError('maxlag')
        raise ValueError(data['error'])  # a real error, no use retrying
      return data
    except ValueError:
      raise
    except Exception as e:
      if attempt == tries - 1:
        raise
      wait = min(120, 5 * 2 ** min(attempt, 5))
      print(f'  {type(e).__name__} {e}, waiting {wait} s', flush=True)
      time.sleep(wait)


def chunks(xs, n):
  xs = list(xs)
  return [xs[i:i + n] for i in range(0, len(xs), n)]


def qnum(q):
  return int(q[1:]) if q[1:].isdigit() else 10 ** 12


def cui_items(cuis):
  found = {}
  for batch in chunks(sorted(cuis), 200):
    values = ' '.join(f'"{c}"' for c in batch)
    rows = get(SPARQL, {'query': f'SELECT ?cui ?item WHERE {{ VALUES ?cui {{ {values} }} ?item wdt:P2892 ?cui }}', 'format': 'json'})
    for r in rows['results']['bindings']:
      found.setdefault(r['cui']['value'], set()).add(r['item']['value'].rsplit('/', 1)[-1])
    time.sleep(0.5)
  return {c: sorted(v, key=qnum) for c, v in found.items()}


def entities(qids, props, langs=LANGS):
  out = {}
  for batch in chunks(sorted(qids, key=qnum), 50):
    data = get(API, {'action': 'wbgetentities', 'format': 'json', 'ids': '|'.join(batch), 'props': props,
                     'languages': '|'.join(langs), 'sitefilter': 'enwiki'})
    # no maxlag: it holds back edits while the query service lags, a read of entities comes from the database itself
    for qid, e in data.get('entities', {}).items():
      if 'missing' not in e:
        out[qid] = e
    for frm, to in (data.get('redirects') or {}).items() if isinstance(data.get('redirects'), dict) else []:
      if to in out:
        out[frm] = out[to]
    time.sleep(0.2)
  return out


def truthy(statements):
  live = [s for s in statements if s.get('rank') != 'deprecated' and s['mainsnak'].get('snaktype') == 'value']
  preferred = [s for s in live if s.get('rank') == 'preferred']
  return [s['mainsnak']['datavalue']['value'] for s in (preferred or live)]


def text(e, field):
  return {l: v['value'] for l, v in (e.get(field) or {}).items()}


def main():
  # 1. resolve
  graph_cui = {cid: e['kg']['cui'] for cid, e in LINKS.items() if e.get('kg', {}).get('cui')}
  mhh_cui = {cid: e['mhh']['umls']['cui'] for cid, e in LINKS.items() if e.get('mhh', {}).get('umls', {}).get('cui')}
  by_cui = cui_items(set(graph_cui.values()) | set(mhh_cui.values()))
  print(f'{len(by_cui)} CUIs with a Wikidata item', flush=True)
  cui_qid = {}
  for cid, e in LINKS.items():
    for cui in (graph_cui.get(cid), mhh_cui.get(cid)):
      if cui and cui in by_cui and cui not in cui_qid:
        own = e.get('kg', {}).get('qid')
        cui_qid[cui] = own if own in by_cui[cui] else by_cui[cui][0]
  qids = set(cui_qid.values()) | {e['kg']['qid'] for e in LINKS.values() if e.get('kg', {}).get('qid')}
  qids |= {e['mhh']['wikidata']['qid'] for e in LINKS.values()  # items the CLEF selector chose; candidates are never used
           if (e.get('mhh', {}).get('wikidata') or {}).get('status') == 'selected'}
  # 2. the items
  ents = entities(qids, 'info|labels|descriptions|claims|sitelinks')
  print(f'{len(ents)} of {len(qids)} items fetched', flush=True)
  # 3. labels of what they point to
  targets = set()
  for e in ents.values():
    for _, pid, _ in RELATIONS:
      targets.update(v['id'] for v in truthy(e.get('claims', {}).get(pid, [])) if isinstance(v, dict) and 'id' in v)
  names = entities(targets, 'labels')
  print(f'{len(names)} related items labelled', flush=True)

  items = {}
  for qid in sorted(qids, key=qnum):
    e = ents.get(qid)
    if not e:
      continue
    claims = e.get('claims', {})
    img = truthy(claims.get('P18', []))
    wiki = (e.get('sitelinks') or {}).get('enwiki', {}).get('title')
    ids = []
    for name, pid, link in IDENTIFIERS:
      for val in sorted({v for v in truthy(claims.get(pid, [])) if isinstance(v, str)}):
        ids.append({'label': name, 'value': val, 'href': link.format(val[2:] if pid == 'P715' else val) if link else None})
    relations = []
    for title, pid, tone in RELATIONS:
      vals = {v['id'] for v in truthy(claims.get(pid, [])) if isinstance(v, dict) and v.get('id', '').startswith('Q')}
      labelled = [(q, text(names.get(q, {}), 'labels')) for q in vals]
      labelled = [(q, l) for q, l in labelled if l.get('en')]  # items without an english label are noise
      if labelled:
        labelled.sort(key=lambda x: x[1]['en'].lower())
        relations.append({'title': title, 'property': pid, 'tone': tone, 'total': len(labelled),
                          'items': [{'qid': q, 'label': l['en'], 'labels': l, 'url': f'https://www.wikidata.org/wiki/{q}'} for q, l in labelled[:14]]})
    labels, descs = text(e, 'labels'), text(e, 'descriptions')
    items[qid] = {'qid': e.get('id', qid), 'url': f'https://www.wikidata.org/wiki/{e.get("id", qid)}',
                  'label': labels.get('en'), 'labels': labels, 'description': descs.get('en'), 'descriptions': descs,
                  'image': 'https://commons.wikimedia.org/wiki/Special:FilePath/' + urllib.parse.quote(img[0].replace(' ', '_')) if img else None,
                  'wikipedia': 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(wiki.replace(' ', '_')) if wiki else None,
                  'ids': ids, 'relations': relations, 'revision': e.get('lastrevid'), 'modified': e.get('modified')}
  snap = {'meta': {'created': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': 'https://www.wikidata.org',
                   'license': 'CC0 1.0', 'languages': list(LANGS), 'concepts_linked': len(LINKS), 'cuis_with_item': len(by_cui),
                   'items': len(items), 'related_items': len(names), 'builder': 'pipeline/build_wikidata_snapshot.py'},
          'cui_qid': dict(sorted(cui_qid.items())), 'items': items}
  with gzip.open(OUT, 'wt', encoding='utf-8') as fh:
    json.dump(snap, fh, ensure_ascii=False)
  print(snap['meta'], f'-> {OUT} ({OUT.stat().st_size // 1024} KiB)')


if __name__ == '__main__':
  main()
