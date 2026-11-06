/* Graph Walker page. Left: the trace, every step of the walk in plain German with its details (evidence, UMLS links,
   sources, cost). Right: the path through the knowledge graph in five stages (guidelines, topics, statements, UMLS
   concepts, answer), connected by lines as the walker takes them. Events come from POST /api/chat/walker
   (app/walkerchat.py); a director plays them one after the other at presentation pace. */
'use strict';
(() => {
const $ = id => document.getElementById(id);
const fmt = n => Number(n).toLocaleString('de-DE');
const usd = n => Number(n).toLocaleString('de-DE', {minimumFractionDigits: 4, maximumFractionDigits: 4}) + ' $';
const sec = n => Number(n).toLocaleString('de-DE', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + ' s';
const pct = p => Math.round(p * 100) + ' %';
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const sleep = ms => new Promise(r => setTimeout(r, ms));
let TEMPO = 1;  // 1: presentation pace; .3: close to real time
const wait = ms => sleep(ms * TEMPO);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const short = (s, n) => (s = String(s || '')).length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
const KIND = {recommendation: 'Empfehlung', finding: 'Befund', value: 'Wert', definition: 'Definition', criterion: 'Kriterium', warning: 'Warnhinweis',
  rationale: 'Begründung', procedure: 'Vorgehen', background: 'Hintergrund', other: 'Aussage', 'table row': 'Tabellenzeile', line: 'Textstelle'};
const MODEL = {'@cf/cloudflare/clef-flash': 'clef-flash', '@cf/cloudflare/clef': 'clef'};
const EXAMPLES = [
  'Wie unterscheiden sich in der AHA/ACC/HFSA-Leitlinie die Empfehlungsklassen für SGLT2-Hemmer bei symptomatischer HFrEF und bei HFmrEF?',
  'Wie unterscheiden sich AHA/ACC/HFSA 2022 und ESC 2021 bei der Empfehlung von DOAKs gegenüber Vitamin-K-Antagonisten bei Herzinsuffizienz?',
  'Wie lange vor dem Hautschnitt soll bei der Implantation eines Schrittmachers Vancomycin gegeben werden?',
  'Über welchen Zeitraum wurden nach Ultrafiltration bei akut dekompensierter Herzinsuffizienz weniger Wiederaufnahmen berichtet?',
  'Was empfehlen die Leitlinien zu Carbocystein bei Herzinsuffizienz?',
];

let L = null;  // the graph: guidelines, topics, statements (keys, topic), UMLS concepts
let S = {};    // the state of one walk
let busy = false, trace = null;

/* ---------------------------------------------------------------- labels */
const docName = di => L.docs[di]?.de || L.docs[di]?.title || '';
const docMeta = di => { const d = L.docs[di]; return [d.body, d.year].filter(Boolean).join(' · '); };
const topicName = ti => L.topics[ti]?.de || L.topics[ti]?.name || '';
const conceptName = c => c.de || c.name;
const statementText = p => p.text_de || p.text;
const grade = p => [p.cls ? 'Klasse ' + p.cls : '', p.lvl ? 'Evidenz ' + p.lvl : ''].filter(Boolean).join(' · ');
const at = ev => ev && ev.at != null ? ` <span class="at">nach ${sec(ev.at)}</span>` : '';

/* ---------------------------------------------------------------- the five columns */
const col = n => document.querySelector(`.col[data-c="${n}"]`);
function stage(n) { for (let i = 1; i <= 5; i++) { col(i).classList.toggle('on', i === n); col(i).classList.toggle('done', i < n); } }
function caption(kicker, title, sub) {
  const c = document.querySelector('.g-cap'); c.classList.remove('swap'); void c.offsetWidth; c.classList.add('swap');
  $('g-kicker').textContent = kicker; $('g-title').textContent = title; $('g-sub').textContent = sub;
}
function node(id, cls, html, p) {
  const e = document.createElement('div'); e.className = 'node ' + cls; e.id = id; e.innerHTML = html;
  if (p != null) { e.insertAdjacentHTML('beforeend', `<span class="pc">${pct(p)}</span><div class="bar"><i></i></div>`); requestAnimationFrame(() => requestAnimationFrame(() => { e.querySelector('.bar i').style.width = pct(p).replace(' ', ''); })); }
  return e;
}
function lockCard(state, title, text) {
  const icon = state === 'done' ? '<path d="m5 12.5 4.5 4.5L19 7.5"/>' : state === 'on' ? '<path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m13 7 4 4"/>' : '<rect x="5" y="11" width="14" height="9" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>';
  $('c5').innerHTML = `<div class="lock ${state}" id="n-ans"><svg viewBox="0 0 24 24">${icon}</svg><b>${title}</b><span>${text}</span></div>`;
}

// how many rows of a given height (in rem) fit into a column body, so nothing is cut off at the bottom
const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
function fits(id, rowRem, reserve = 0) { const b = $(id), h = b.parentElement.getBoundingClientRect().bottom - b.getBoundingClientRect().top - reserve; return Math.max(2, Math.floor(h / (rowRem * remPx()))); }

/* ---------------------------------------------------------------- edges between the columns */
let edges = [];
function link(a, b, cls) { if (!edges.some(e => e.a === a && e.b === b)) edges.push({a, b, cls, fresh: true}); else edges.find(e => e.a === a && e.b === b).cls = cls; }
function drawEdges() {
  const svg = $('edges'), box = $('cols').getBoundingClientRect(); let html = '';
  for (const e of edges) {
    const A = document.getElementById(e.a), B = document.getElementById(e.b); if (!A || !B) continue;
    const ra = A.getBoundingClientRect(), rb = B.getBoundingClientRect();
    if (!ra.height || !rb.height) continue;
    const x1 = ra.right - box.left, y1 = ra.top - box.top + ra.height / 2, x2 = rb.left - box.left, y2 = rb.top - box.top + rb.height / 2, mx = (x1 + x2) / 2;
    const len = Math.hypot(x2 - x1, y2 - y1) * 1.2;
    html += `<path class="${e.cls}${e.fresh ? ' new' : ''}" style="--len:${len};stroke-dasharray:${e.cls === 'um' ? '3 4' : len}" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>`;
    e.fresh = false;
  }
  svg.innerHTML = html;
}
const relayout = () => requestAnimationFrame(() => requestAnimationFrame(drawEdges));
window.addEventListener('resize', relayout);

/* ---------------------------------------------------------------- the trace */
const feed = $('feed');
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
function step(cls, title, body = '') { const s = el('div', 'st ' + cls, `<h3>${title}</h3>${body}`); trace.appendChild(s); down(); return s; }
const down = () => requestAnimationFrame(() => feed.scrollTo({top: feed.scrollHeight, behavior: 'smooth'}));
function evidenceBox(p, n) {
  const b = el('div', 'box ev');
  b.innerHTML = `<div class="k">Beleg ${n}${p.kind ? ' · ' + (KIND[p.kind] || 'Aussage') : ''}</div>${esc(statementText(p))}
    <div class="m">${esc(short(docName(p.doc), 70))} · Seite ${p.page}${grade(p) ? ' · ' + grade(p) : ''}</div>
    ${p.quote ? `<details><summary>Originalzitat</summary><q>${esc(p.quote)}</q></details>` : ''}`;
  b.style.animationDelay = (n * 90 * TEMPO) + 'ms';
  return b;
}

/* ---------------------------------------------------------------- the director */
function reset() {
  S = {docScores: {}, kept: [], topics: [], recs: 0, statements: 0, taken: [], concepts: [], awaiting: false, empty: false};
  edges = []; $('edges').innerHTML = '';
  ['c1', 'c2', 'c3', 'c4'].forEach(i => $(i).innerHTML = ''); $('hist').innerHTML = ''; $('hist').classList.remove('on');
  $('c1-n').textContent = 'noch nicht geprüft'; $('c2-n').textContent = '–'; $('c3-n').textContent = '–'; $('c4-n').textContent = '–';
  $('c5-n').textContent = 'Sprachmodell gesperrt';
  lockCard('', 'Noch gesperrt', 'Ein Sprachmodell darf erst schreiben, wenn die Belege feststehen.');
  ['m-time', 'm-usd', 'm-dec', 'm-llm'].forEach(i => $(i).textContent = '–'); stage(0);
}
function retract() {  // the first check found the evidence incomplete: the walker searches on, nothing was written yet
  S.awaiting = false; const ws = $('st-write');
  if (ws) { ws.removeAttribute('id'); ws.classList.remove('busy', 'llm'); ws.querySelector('h3').textContent = 'Belege reichen noch nicht';
    ws.querySelector('p').innerHTML = 'Die Prüfung ergab, dass noch etwas fehlt. <b>Der Walker sucht gezielt weiter</b>, bevor eine Antwort geschrieben wird.'; }
  edges = edges.filter(e => e.cls !== 'ans'); relayout();
  $('c5-n').textContent = 'Sprachmodell gesperrt'; lockCard('', 'Noch gesperrt', 'Die Belege reichen noch nicht. Der Walker sucht weiter.');
  caption('Weitere Runde', 'Die Belege reichen noch nicht', 'Der Walker öffnet weitere Leitlinien und Themen.');
}

async function play(ev) {
  if (S.awaiting && ['scores', 'taken', 'round', 'guidelines'].includes(ev.t)) retract();
  switch (ev.t) {
    case 'loading':
      caption('Vorbereitung', 'Wissensgraph wird geladen', `Beim ersten Aufruf lädt der Server Graph und Index (${ev.seconds} s).`); return;
    case 'start': {
      reset(); $('intro')?.remove();
      feed.appendChild(el('div', 'q', esc(ev.question))); trace = el('div', 'trace'); feed.appendChild(trace); down();
      caption('Schritt 1 von 5', 'Die Frage wird eingeordnet', 'Ein Bewertungsmodell prüft, ob nach einem einzelnen Sachverhalt oder nach einer Aufzählung gefragt ist.');
      return wait(500);
    }
    case 'question':
      step('', 'Frage eingeordnet' + at(ev), `<p>${ev.aggregate ? 'Gefragt ist eine <b>Aufzählung</b>. Der Walker sammelt alle passenden Stellen.' : 'Gefragt ist ein <b>konkreter Sachverhalt</b>. Der Walker sucht die genaue Stelle.'}</p>`);
      return wait(700);
    case 'scores': return scores(ev);
    case 'guidelines': return guidelines(ev);
    case 'taken': return taken(ev);
    case 'round':
      if (ev.n > 1) S.round = ev.n;
      for (const di of ev.docs) if (!S.kept.includes(di)) { S.kept.push(di); showDoc(di, S.docScores[di] ?? 0, true); }
      if (ev.other?.length) S.recEvidence = ev.other.length;
      relayout(); return;
    case 'writing': return writing(ev);
    case 'answer': return answer(ev);
    case 'done': return done(ev);
    case 'error': (trace || feed).appendChild(el('div', 'err', esc(ev.message))); caption('Fehler', 'Der Walk wurde abgebrochen', ev.message); down(); return;
  }
}

function showDoc(di, p, kept) {
  let n = $('n-d' + di);
  if (!n) { n = node('n-d' + di, '', `<div class="t">${esc(docName(di))}</div><div class="s">${esc(docMeta(di))}</div>`, p); $('c1').appendChild(n); }
  n.classList.toggle('kept', !!kept); if (kept) n.classList.remove('dim');
  const rows = [...$('c1').querySelectorAll('.node')];  // at most 7 rows: the kept ones stay, the weakest of the others go
  while (rows.length > Math.min(7, fits('c1', 3.55))) { const drop = rows.reverse().find(r => !r.classList.contains('kept')); if (!drop) break; drop.remove(); rows.splice(rows.indexOf(drop), 1); }
  return n;
}

async function scores(ev) {
  const items = ev.items;
  if (ev.level === 'guidelines') {
    stage(1); items.forEach(([di, p]) => S.docScores[di] = p);
    const sorted = items.slice().sort((a, b) => b[1] - a[1]);
    $('c1-n').textContent = `${fmt(items.length)} geprüft`;
    caption('Schritt 2 von 5', `${fmt(items.length)} Leitlinien werden bewertet`, `Das Bewertungsmodell ${MODEL[ev.scorer] || ''} schätzt für jede Leitlinie, ob dort die Antwort stehen kann.`);
    // every guideline as one bar, sorted: the picture of "all were checked"
    const h = $('hist'); h.innerHTML = sorted.map(([di, p]) => `<i data-d="${di}" style="height:2%"></i>`).join(''); h.classList.add('on');
    requestAnimationFrame(() => requestAnimationFrame(() => [...h.children].forEach((b, k) => { b.style.height = clamp(sorted[k][1] * 100, 3, 100) + '%'; })));
    sorted.slice(0, 6).forEach(([di, p], k) => setTimeout(() => showDoc(di, p, false), k * 110 * TEMPO));
    step('', 'Alle Leitlinien bewertet' + at(ev), `<div class="num"><b>${fmt(items.length)}</b><span>Leitlinien geprüft</span></div><p>Jede Leitlinie erhält eine Wahrscheinlichkeit. Das Bewertungsmodell schreibt keinen Text.</p>`);
    return wait(1800);
  }
  if (ev.level === 'topics') {
    stage(2); const di = ev.doc, s = items.slice().sort((a, b) => b[1] - a[1]);
    let kept = s.slice(0, 3).filter(x => x[1] >= .3); if (!kept.length) kept = s.slice(0, 1);
    S.topicsChecked = (S.topicsChecked || 0) + items.length;
    const g = el('div', 'grp', esc(short(docName(di), 60))); $('c2').appendChild(g);
    const cap = Math.min(9, fits('c2', 3.35, remPx() * 2.4 * ($('c2').querySelectorAll('.grp').length))), room = Math.max(0, cap - $('c2').querySelectorAll('.node').length), shown = kept.slice(0, room);  // only as many topic rows as fit
    S.hiddenTopics = (S.hiddenTopics || 0) + kept.length - shown.length;
    if (!shown.length) g.remove();
    shown.forEach(([ti, p], k) => { S.topics.push(ti); setTimeout(() => { $('c2').insertBefore(node('n-t' + ti, 'kept', `<div class="t">${esc(topicName(ti))}</div>`, p), g.nextSibling && k ? $('n-t' + kept[k - 1][0]).nextSibling : g.nextSibling); link('n-d' + di, 'n-t' + ti, 'path'); relayout(); }, k * 140 * TEMPO); });
    kept.slice(shown.length).forEach(([ti]) => S.topics.push(ti)); updateTopicNote(); relayout();
    $('c2-n').textContent = `${fmt(S.topicsChecked)} geprüft · ${S.topics.length} geöffnet`;
    caption('Schritt 3 von 5', `Themen in „${short(docName(di), 60)}“`, `${items.length} Themen dieser Leitlinie werden bewertet. Nur die passenden werden geöffnet.`);
    let st = trace.querySelector('.st.topics');
    if (!st) st = step('topics', 'Themen geöffnet' + at(ev), '<p>Jede Leitlinie ist im Graphen nach Themen gegliedert. Der Walker öffnet nur die passenden.</p>');
    const b = el('div', 'box'); b.innerHTML = `<div class="k">${esc(short(docName(di), 70))}</div>${kept.map(([ti, p]) => `${esc(topicName(ti))} <span class="tag">${pct(p)}</span>`).join('<br>')}<div class="m">${items.length} Themen geprüft</div>`;
    st.appendChild(b); down();
    return wait(1100);
  }
  if (ev.level === 'recommendations') { S.recs += ev.candidates; return; }
  if (ev.level === 'statements') {
    stage(3); S.statements += ev.candidates;
    if (!S.topicsChecked && !$('c2-note')) {  // short guidelines have no topic level: their statements are read directly
      $('c2-n').textContent = 'nicht nötig';
      const n = el('div', 'empty-note', 'Die gewählten Leitlinien sind kurz. Der Walker liest ihre Aussagen direkt, ohne Themen-Stufe.'); n.id = 'c2-note'; $('c2').appendChild(n);
    }
    $('c3-n').textContent = `${fmt(S.statements)} geprüft`;
    caption('Schritt 4 von 5', `${fmt(ev.candidates)} Aussagen werden gelesen`, 'Jede Aussage ist ein einzelner, in sich verständlicher Satz einer Leitlinie. Jede wird einzeln gegen die Frage geprüft.');
    showCandidates(ev.top || [], 'cand');
    step('', 'Aussagen bewertet' + at(ev), `<div class="num"><b>${fmt(ev.candidates)}</b><span>Aussagen in den geöffneten Themen</span></div><p>Das schnelle Bewertungsmodell prüft alle. Rechts stehen die besten Kandidaten.</p>`);
    return wait(1700);
  }
  if (ev.level === 'rerank') {
    let keep = items.filter(x => x[1] >= .6); if (keep.length < 5) keep = keep.concat(items.filter(x => x[1] < .6 && x[1] >= .25).slice(0, 5 - keep.length));
    S.keep = keep.length;
    $('c3-n').textContent = `${fmt(S.statements)} geprüft → ${fmt(items.length)} nachgeprüft → ${fmt(keep.length)} bestehen`;
    caption('Schritt 4 von 5', 'Strenge Zweitprüfung', `Ein stärkeres Bewertungsmodell (${MODEL[ev.scorer] || ''}) prüft die ${fmt(items.length)} besten Kandidaten erneut: Steht hier wirklich das Gefragte?`);
    showCandidates(ev.top || [], 'cand');
    step('', 'Zweitprüfung' + at(ev), `<div class="num"><b>${fmt(items.length)}</b><span class="arr">→</span><b>${fmt(keep.length)}</b><span>bestehen</span></div><p>Ähnliche, aber nicht zutreffende Aussagen scheiden hier aus.</p>`);
    if (!keep.length) { S.empty = true; caption('Schritt 4 von 5', 'Keine Aussage besteht die Prüfung', 'In den Leitlinien steht keine Stelle, die das Gefragte wirklich sagt. Der Walker rät nicht.'); }
    return wait(1600);
  }
}
function updateTopicNote() {
  let n = $('c2-more'); if (!S.hiddenTopics) return;
  if (!n) { n = el('div', 'empty-note'); n.id = 'c2-more'; }
  n.textContent = `+ ${S.hiddenTopics} weitere Themen geöffnet`; $('c2').appendChild(n);
}
function showCandidates(top, cls) {
  const box = $('c3'); box.innerHTML = '';
  edges = edges.filter(e => !e.b.startsWith('n-s'));
  top.slice(0, Math.min(5, fits('c3', 4.3))).forEach((p, k) => {
    const n = node('n-s' + p.i, cls, `<div class="t">${esc(statementText(p))}</div><div class="s">${esc(short(docName(p.doc), 44))} · S. ${p.page}</div>`, p.p);
    n.style.animationDelay = (k * 80 * TEMPO) + 'ms'; box.appendChild(n);
    link($('n-t' + p.topic) ? 'n-t' + p.topic : 'n-d' + p.doc, 'n-s' + p.i, '');
  });
  if (!top.length) box.appendChild(el('div', 'empty-note', 'Keine Kandidaten.'));
  relayout();
}

async function guidelines(ev) {
  S.kept = ev.kept.slice();
  const keep = new Set(ev.kept);
  ev.kept.forEach(di => showDoc(di, S.docScores[di] ?? 0, true));
  document.querySelectorAll('#c1 .node').forEach(n => n.classList.toggle('dim', !keep.has(+n.id.slice(3))));
  // kept guidelines first, the rest below them
  [...ev.kept].reverse().forEach(di => $('c1').prepend($('n-d' + di)));
  document.querySelectorAll('#hist i').forEach(b => b.classList.toggle('k', keep.has(+b.dataset.d)));
  $('c1-n').textContent = `${fmt(Object.keys(S.docScores).length)} geprüft · ${ev.kept.length} gewählt`;
  caption('Schritt 2 von 5', `${ev.kept.length} Leitlinien passen zur Frage`, 'Der Walker geht nur dort weiter, wo die Antwort stehen kann. Alle anderen Leitlinien bleiben zu.');
  const st = trace.lastChild, b = el('div', 'box');
  b.innerHTML = `<div class="k">Gewählt</div>${ev.kept.map(di => `${esc(short(docName(di), 80))} <span class="tag">${pct(S.docScores[di] ?? 0)}</span>`).join('<br>')}`;
  st.appendChild(b); down(); relayout();
  return wait(1200);
}

async function taken(ev) {
  const items = ev.items.slice().sort((a, b) => (b.p ?? 0) - (a.p ?? 0)); S.taken = items;
  stage(4);
  $('c3').innerHTML = ''; edges = edges.filter(e => !e.b.startsWith('n-s'));
  const capS = Math.min(5, fits('c3', 6.2, remPx() * 1.6));
  items.slice(0, capS).forEach((p, k) => {
    const tags = [p.kind ? `<span class="tag g">${KIND[p.kind] || 'Aussage'}</span>` : '', grade(p) ? `<span class="tag">${grade(p)}</span>` : ''].join('');
    const n = node('n-s' + p.i, 'sel', `<div class="t">${esc(statementText(p))}</div><div class="s">${esc(short(docName(p.doc), 44))} · S. ${p.page}</div>${tags ? `<div class="tags">${tags}</div>` : ''}`);
    n.style.animationDelay = (k * 110 * TEMPO) + 'ms'; $('c3').appendChild(n);
    link($('n-t' + p.topic) ? 'n-t' + p.topic : 'n-d' + p.doc, 'n-s' + p.i, 'ev');
  });
  if (items.length > capS) $('c3').appendChild(el('div', 'empty-note', `+ ${items.length - capS} weitere Belege`));
  $('c3-n').textContent = `${fmt(S.statements)} geprüft → ${items.length} Belege`;
  relayout();
  const st = step('ev', `${items.length} ${items.length === 1 ? 'Beleg' : 'Belege'} ausgewählt` + at(ev), `<p>${items.length ? 'Nur diese Sätze gehen später an das Sprachmodell. Jeder steht wörtlich in der Leitlinie.' : 'Keine Aussage hat die Prüfung bestanden. Das Sprachmodell bekommt nichts, woraus es eine Behauptung machen könnte.'}</p>`);
  items.slice(0, 4).forEach((p, k) => st.appendChild(evidenceBox(p, k + 1)));
  if (items.length > 4) st.appendChild(el('div', 'more', `+ ${items.length - 4} weitere Belege`));
  caption('Schritt 4 von 5', items.length ? `${items.length} ${items.length === 1 ? 'Beleg' : 'Belege'} ausgewählt` : 'Keine Belege', items.length ? 'Jeder Beleg ist ein wörtlicher Satz einer Leitlinie, mit Seite und Empfehlungsgrad.' : 'Der Walker hat in den Leitlinien keine passende Stelle gefunden.');
  down(); await wait(1600);

  // UMLS: the medical concepts the evidence is about
  const byC = new Map();
  items.forEach(p => (p.concepts || []).forEach(c => { const e = byC.get(c.ci) || {c, props: []}; e.props.push(p.i); byC.set(c.ci, e); }));
  const list = [...byC.values()].sort((a, b) => b.props.length - a.props.length).slice(0, 5);
  if (!list.length) return;
  $('c4-n').textContent = `${list.length} verknüpft`;
  caption('Schritt 4 von 5', 'Verknüpfung mit der medizinischen Terminologie', 'Die Belege sind an Begriffe des UMLS-Metathesaurus gebunden. So erkennt der Graph denselben Begriff auch unter anderem Namen und in anderen Leitlinien.');
  const us = step('um', 'UMLS-Konzepte gefunden' + at(ev), '<p>Jeder Begriff hat im UMLS-Metathesaurus eine feste Kennung (CUI). Über sie ist der Beleg mit allen anderen Stellen im Graphen verbunden, die denselben Begriff meinen.</p>');
  for (let k = 0; k < list.length; k++) {
    const {c, props} = list[k], meta = L.concepts[c.ci] || {}, docs = conceptDocs(c.ci);
    $('c4').appendChild(node('n-u' + c.ci, 'um', `<div class="t">${esc(conceptName(c))}</div><div class="s">${esc(c.cui)}</div>`));
    props.forEach(i => link('n-s' + i, 'n-u' + c.ci, 'um'));
    const b = el('div', 'box um'); b.innerHTML = `<div class="k">UMLS-Link</div><span class="nm">${esc(conceptName(c))}</span> <span class="code">${esc(c.cui)}</span>
      ${c.de && c.de !== c.name ? `<div class="en">UMLS-Bezeichnung: ${esc(c.name)}</div>` : ''}
      <div class="m">in ${props.length} der Belege · im ganzen Graphen ${fmt(meta.n || 0)} Aussagen in ${fmt(docs)} Leitlinien</div>`;
    us.appendChild(b); relayout(); down();
    await wait(520);
  }
}
let CDOCS = null;
function conceptDocs(ci) {  // in how many guidelines a concept occurs
  if (!CDOCS) { CDOCS = {}; for (let i = 0; i < L.pc.length; i++) { const d = L.topics[L.topic[i]].doc; for (const c of L.pc[i]) (CDOCS[c] ||= new Set()).add(d); } }
  return CDOCS[ci]?.size || 0;
}

async function writing(ev) {
  S.awaiting = true; stage(5);
  const n = S.taken.length;
  $('c5-n').textContent = 'Sprachmodell aktiv';
  lockCard('on', 'Sprachmodell formuliert', n ? `Es erhält nur die ${n} Belege, keine anderen Quellen und nicht sein Allgemeinwissen.` : 'Es erhält keine Belege und kann deshalb nur sagen, dass nichts gefunden wurde.');
  S.taken.forEach(p => link('n-s' + p.i, 'n-ans', 'ans')); relayout();
  caption('Schritt 5 von 5', 'Erst jetzt schreibt ein Sprachmodell', 'Bis hierher wurde kein Wort generiert. Das Modell formuliert die Antwort ausschließlich aus den ausgewählten Belegen.');
  const s = step('llm busy', 'Antwort wird formuliert' + at(ev), '<p>Das Sprachmodell DeepSeek V4.1 Flash bekommt die Frage und die Belege. Jede Aussage der Antwort muss auf einen Beleg verweisen.</p>'); s.id = 'st-write';
  return wait(2200);
}

async function answer(ev) {
  S.awaiting = false; if (ev.status === 'not covered') S.empty = true;
  const ws = $('st-write'); if (ws) { ws.classList.remove('busy'); ws.querySelector('h3').innerHTML = 'Antwort formuliert' + at(ev); }
  const srcs = ev.sources || [], byN = Object.fromEntries(srcs.map(s => [s.n, s]));
  const cited = new Set([...ev.text.matchAll(/\[(\d+)\]/g)].map(m => +m[1]));
  $('c5-n').textContent = 'fertig';
  lockCard('done', ev.status === 'not covered' ? 'Nicht in den Leitlinien' : 'Antwort mit Belegen',
    ev.status === 'not covered' ? 'Der Walker nennt die Lücke, statt zu raten.' : `${cited.size} ${cited.size === 1 ? 'Quelle' : 'Quellen'} im Text, jede mit Seite und Originalzitat.`);
  const box = el('div', 'answer'), tx = el('div', 'tx'), src = el('div', 'srcbox');
  box.innerHTML = `<div class="k"><svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/></svg>${ev.status === 'not covered' ? 'Antwort: nicht in den Leitlinien' : 'Antwort aus den Belegen'}</div>`;
  box.append(tx, src); trace.parentNode.appendChild(box);
  let n = 0;
  for (const part of ev.text.split(/(\[\d+\])/g).filter(Boolean)) {
    const m = part.match(/^\[(\d+)\]$/);
    if (m) { const s = byN[+m[1]], c = el('span', 'cite w', m[1]); c.style.animationDelay = (n++ * 22 * TEMPO) + 'ms'; if (s) c.onclick = () => showSource(s, c, box, src); tx.appendChild(c); }
    else part.split(/(\s+)/).forEach(w => { const sp = el('span', 'w', esc(w)); sp.style.animationDelay = (n++ * 22 * TEMPO) + 'ms'; tx.appendChild(sp); });
  }
  caption('Fertig', ev.status === 'not covered' ? 'Das steht nicht in den Leitlinien' : 'Die Antwort steht',
    ev.status === 'not covered' ? 'Ohne Beleg gibt es keine Behauptung. Ein üblicher Chatbot würde hier eine Antwort formulieren.' : 'Jede Zahl in eckigen Klammern führt zur Quelle. Ein Klick zeigt sie im Verlauf und markiert sie im Graphen.');
  down(); return wait(Math.min(2600, n * 22 + 300));
}
function showSource(s, btn, box, src) {
  box.querySelectorAll('.cite').forEach(c => c.classList.toggle('on', c === btn));
  src.innerHTML = ''; src.appendChild(evidenceBox({...s, text_de: s.text_de, kind: s.kind, doc: s.doc, page: s.page, cls: s.cor, lvl: s.loe}, s.n)); down();
  document.querySelectorAll('.node.flash').forEach(n => n.classList.remove('flash'));
  const n = s.i != null && $('n-s' + s.i); if (n) { n.classList.add('flash'); setTimeout(() => n.classList.remove('flash'), 2400); }
}

function done(ev) {
  stage(6);
  $('m-time').textContent = sec(ev.seconds); $('m-usd').textContent = usd(ev.usd); $('m-dec').textContent = fmt(ev.decision_calls); $('m-llm').textContent = fmt(ev.language_model_calls);
  const box = feed.querySelector('.answer:last-of-type');
  if (box) box.appendChild(el('div', 'cost', `<span>Dauer <b>${sec(ev.seconds)}</b></span><span>davon Graph-Walk <b>${sec(ev.graph_seconds)}</b></span><span>Kosten <b>${usd(ev.usd)}</b></span><span><b>${fmt(ev.decision_calls)}</b> Bewertungen</span><span><b>${fmt(ev.language_model_calls)}</b> Sprachmodell-Aufruf${ev.language_model_calls === 1 ? '' : 'e'}</span>`));
  down();
}

/* ---------------------------------------------------------------- stream */
const queue = []; let playing = false;
async function pump() {
  if (playing) return; playing = true;
  while (queue.length) { try { await play(queue.shift()); } catch (e) { console.error(e); } }
  playing = false; if (!busy) $('send').disabled = false;
}
async function ask(question) {
  question = question.trim(); if (busy || !question || !L) return;
  busy = true; $('send').disabled = true; $('q').value = ''; $('q').style.height = '';
  try {
    const res = await fetch('/api/chat/walker', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TIB-Chat': '1'}, body: JSON.stringify({messages: [{role: 'user', text: question}]})});
    if (res.status === 401) { location.href = '/'; return; }
    const rd = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done: end} = await rd.read(); if (end) break;
      buf += dec.decode(value, {stream: true}); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2); if (line.startsWith('data:')) { queue.push(JSON.parse(line.slice(5))); pump(); } }
    }
  } catch (e) { queue.push({t: 'error', message: 'Die Verbindung wurde unterbrochen.'}); pump(); }
  busy = false; pump();
}

/* ---------------------------------------------------------------- page */
function setup() {
  const q = $('q');
  EXAMPLES.forEach(t => { const b = el('button', 'ex', esc(t)); b.type = 'button'; b.onclick = () => ask(t); $('examples').appendChild(b); });
  $('composer').onsubmit = e => { e.preventDefault(); ask(q.value); };
  q.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q.value); } });
  q.addEventListener('input', () => { q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 120) + 'px'; });
  $('fs').onclick = () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {});
  document.addEventListener('fullscreenchange', () => { document.documentElement.classList.toggle('fs', !!document.fullscreenElement); setTimeout(relayout, 150); });
  document.addEventListener('keydown', e => { if (e.key.toLowerCase() === 'f' && !/TEXTAREA|INPUT/.test(document.activeElement.tagName)) $('fs').click(); });
  $('tempo').onclick = () => { TEMPO = TEMPO === 1 ? .3 : 1; $('tempo-t').textContent = TEMPO === 1 ? 'Präsentation' : 'Echtzeit'; $('tempo').title = 'Wiedergabe: ' + (TEMPO === 1 ? 'Präsentationstempo' : 'Echtzeit'); };
  new ResizeObserver(relayout).observe($('cols'));
}
function placeholders() {  // before the first question: what each stage will do, with the real sizes of the graph
  const ph = (n, t) => `<div class="ph"><b>${n}</b><span>${t}</span></div>`;
  $('c1').innerHTML = ph(fmt(L.docs.length), 'Leitlinien. Für jede schätzt ein Bewertungsmodell, ob dort die Antwort stehen kann.');
  $('c2').innerHTML = ph(fmt(L.topics.length), 'Themen. Große Leitlinien öffnet der Walker nur an den passenden Themen.');
  $('c3').innerHTML = ph(fmt(L.keys.length), 'Aussagen. Einzelne Sätze der Leitlinien, jeder mit Seite und Originalzitat.');
  $('c4').innerHTML = ph(fmt(L.concepts.length), 'UMLS-Konzepte. Feste medizinische Begriffe, über die Aussagen verbunden sind.');
}
async function boot() {
  setup(); reset();
  const s = await fetch('/api/session').then(r => r.json()).catch(() => ({}));
  if (!s.authenticated) { location.href = '/'; return; }
  const res = await fetch('/api/walker/layout', {cache: 'no-store'});
  if (!res.ok) { $('loading-t').textContent = 'Die Daten des Graph Walkers sind auf diesem Server nicht installiert.'; return; }
  L = await res.json();
  const umlsLinks = L.pc.reduce((a, c) => a + c.length, 0);
  $('g-lib').innerHTML = [[L.docs.length, 'Leitlinien'], [L.topics.length, 'Themen'], [L.keys.length, 'Aussagen'], [L.concepts.length, 'UMLS-Konzepte']]
    .map(([n, t]) => `<div><dd>${fmt(n)}</dd><dt>${t}</dt></div>`).join('');
  $('g-lib').title = `${fmt(umlsLinks)} Verknüpfungen zwischen Aussagen und UMLS-Konzepten`;
  placeholders(); $('loading').classList.add('gone'); q.focus?.();
  const pre = new URLSearchParams(location.search).get('q'); if (pre) ask(pre);
}
const q = $('q');
boot();
})();
