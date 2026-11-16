/* Graph Walker page. Left: the trace, every step of the walk in plain German with its details (evidence, UMLS links,
   sources, cost). Right: the path through the knowledge graph in five stages (guidelines, topics, statements, UMLS
   concepts, answer), connected by lines as the walker takes them. Events come from POST /api/chat/walker
   (app/walkerchat.py); a director plays them one after the other at presentation pace.

   Language: German or English (?lang=, else the browser's; an embedding page sends {walker: 'lang', value} before each walk).

   Embedded (/graph-walker?embed=1, an iframe in Guido's chat panel and in his video call's shared screen): only the path
   is shown, and the events come from the embedding page over postMessage instead of an own request:
     in   {walker: 'event', ev}   one event of the walk      {walker: 'tempo', value}   playback pace (1 = presentation)
          {walker: 'lang', value}     'de' | 'en'
     out  {walker: 'ready'}       the graph is loaded        {walker: 'played', t}      the director finished showing event t
   Messages are accepted from and sent to the page's own origin only. */
'use strict';
(() => {
const $ = id => document.getElementById(id);
const PARAMS = new URLSearchParams(location.search), EMBED = PARAMS.has('embed');
// language of the page: 'de' or 'en'. ?lang=, else the browser's (German only for German browsers); an embedding page sets it per walk
let LANG = ['de', 'en'].includes(PARAMS.get('lang')) ? PARAMS.get('lang') : (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en';
const tt = (de, en) => LANG === 'de' ? de : en;
const loc = () => LANG === 'de' ? 'de-DE' : 'en-GB';
const fmt = n => Number(n).toLocaleString(loc());
const usd = n => Number(n).toLocaleString(loc(), {minimumFractionDigits: 4, maximumFractionDigits: 4}) + ' $';
const sec = n => Number(n).toLocaleString(loc(), {minimumFractionDigits: 1, maximumFractionDigits: 1}) + ' s';
const plural = (n, one, many) => n === 1 ? one : many;
const pct = p => Math.round(p * 100) + ' %';
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const sleep = ms => new Promise(r => setTimeout(r, ms));
if (EMBED) document.documentElement.classList.add('embed');
function clampTempo(v, fallback) { v = Number(v); return v >= .1 && v <= 2 ? v : fallback; }
let TEMPO = clampTempo(PARAMS.get('tempo'), 1);  // 1: presentation pace; .3: close to real time
const wait = ms => sleep(ms * TEMPO);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const short = (s, n) => (s = String(s || '')).length > n ? s.slice(0, n - 1).trimEnd() + '…' : s;
const KIND_DE = {recommendation: 'Empfehlung', finding: 'Befund', value: 'Wert', definition: 'Definition', criterion: 'Kriterium', warning: 'Warnhinweis',
  rationale: 'Begründung', procedure: 'Vorgehen', background: 'Hintergrund', other: 'Aussage', 'table row': 'Tabellenzeile', line: 'Textstelle'};
const KIND_EN = {recommendation: 'Recommendation', finding: 'Finding', value: 'Value', definition: 'Definition', criterion: 'Criterion', warning: 'Warning',
  rationale: 'Rationale', procedure: 'Procedure', background: 'Background', other: 'Statement', 'table row': 'Table row', line: 'Passage'};
const kind = k => (LANG === 'de' ? KIND_DE : KIND_EN)[k] || tt('Aussage', 'Statement');
const modelName = id => String(id || '').split('/').pop();  // '@cf/cloudflare/clef-flash' -> 'clef-flash', 'google/gemini-decision-1' -> 'gemini-decision-1'
const EXAMPLES_DE = [
  'Wie unterscheiden sich in der AHA/ACC/HFSA-Leitlinie die Empfehlungsklassen für SGLT2-Hemmer bei symptomatischer HFrEF und bei HFmrEF?',
  'Wie unterscheiden sich AHA/ACC/HFSA 2022 und ESC 2021 bei der Empfehlung von DOAKs gegenüber Vitamin-K-Antagonisten bei Herzinsuffizienz?',
  'Wie lange vor dem Hautschnitt soll bei der Implantation eines Schrittmachers Vancomycin gegeben werden?',
  'Über welchen Zeitraum wurden nach Ultrafiltration bei akut dekompensierter Herzinsuffizienz weniger Wiederaufnahmen berichtet?',
  'Was empfehlen die Leitlinien zu Carbocystein bei Herzinsuffizienz?',
];
const EXAMPLES_EN = [
  'How do the AHA/ACC/HFSA guideline\'s classes of recommendation differ for SGLT2 inhibitors in symptomatic HFrEF and in HFmrEF?',
  'How do AHA/ACC/HFSA 2022 and ESC 2021 differ in recommending DOACs over vitamin K antagonists in heart failure?',
  'How long before skin incision should vancomycin be given when implanting a pacemaker?',
  'Over what period were fewer readmissions reported after ultrafiltration in acutely decompensated heart failure?',
  'What do the guidelines recommend about carbocisteine in heart failure?',
];
const examples = () => LANG === 'de' ? EXAMPLES_DE : EXAMPLES_EN;

let L = null;  // the graph: guidelines, topics, statements (keys, topic), UMLS concepts
let S = {};    // the state of one walk
let busy = false, trace = null;

/* ---------------------------------------------------------------- labels */
const docName = di => (LANG === 'de' && L.docs[di]?.de) || L.docs[di]?.title || '';
const docMeta = di => { const d = L.docs[di]; return [d.body, d.year].filter(Boolean).join(' · '); };
const topicName = ti => (LANG === 'de' && L.topics[ti]?.de) || L.topics[ti]?.name || '';
const conceptName = c => (LANG === 'de' && c.de) || c.name;
const statementText = p => (LANG === 'de' && p.text_de) || p.text;
const grade = p => [p.cls ? tt('Klasse ', 'Class ') + p.cls : '', p.lvl ? tt('Evidenz ', 'Level ') + p.lvl : ''].filter(Boolean).join(' · ');
const at = ev => ev && ev.at != null ? ` <span class="at">${tt('nach', 'after')} ${sec(ev.at)}</span>` : '';

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
  b.innerHTML = `<div class="k">${tt('Beleg', 'Evidence')} ${n}${p.kind ? ' · ' + kind(p.kind) : ''}</div>${esc(statementText(p))}
    <div class="m">${esc(short(docName(p.doc), 70))} · ${tt('Seite', 'page')} ${p.page}${grade(p) ? ' · ' + grade(p) : ''}</div>
    ${p.quote ? `<details><summary>${tt('Originalzitat', 'Original quote')}</summary><q>${esc(p.quote)}</q></details>` : ''}`;
  b.style.animationDelay = (n * 90 * TEMPO) + 'ms';
  return b;
}

/* ---------------------------------------------------------------- the director */
function reset() {
  S = {docScores: {}, kept: [], topics: [], recs: 0, statements: 0, taken: [], concepts: [], awaiting: false, empty: false};
  edges = []; $('edges').innerHTML = '';
  ['c1', 'c2', 'c3', 'c4'].forEach(i => $(i).innerHTML = ''); $('hist').innerHTML = ''; $('hist').classList.remove('on');
  $('c1-n').textContent = tt('noch nicht geprüft', 'not checked yet'); $('c2-n').textContent = '–'; $('c3-n').textContent = '–'; $('c4-n').textContent = '–';
  $('c5-n').textContent = tt('Sprachmodell gesperrt', 'language model locked');
  lockCard('', tt('Noch gesperrt', 'Still locked'), tt('Ein Sprachmodell darf erst schreiben, wenn die Belege feststehen.', 'A language model may only write once the evidence is fixed.'));
  ['m-time', 'm-usd', 'm-dec', 'm-llm'].forEach(i => $(i).textContent = '–'); stage(0);
}
function retract() {  // the first check found the evidence incomplete: the walker searches on, nothing was written yet
  S.awaiting = false; const ws = $('st-write');
  if (ws) { ws.removeAttribute('id'); ws.classList.remove('busy', 'llm'); ws.querySelector('h3').textContent = tt('Belege reichen noch nicht', 'Evidence not sufficient yet');
    ws.querySelector('p').innerHTML = tt('Die Prüfung ergab, dass noch etwas fehlt. <b>Der Walker sucht gezielt weiter</b>, bevor eine Antwort geschrieben wird.', 'The check found something missing. <b>The walker keeps searching</b> before any answer is written.'); }
  edges = edges.filter(e => e.cls !== 'ans'); relayout();
  $('c5-n').textContent = tt('Sprachmodell gesperrt', 'language model locked'); lockCard('', tt('Noch gesperrt', 'Still locked'), tt('Die Belege reichen noch nicht. Der Walker sucht weiter.', 'The evidence is not sufficient yet. The walker keeps searching.'));
  caption(tt('Weitere Runde', 'Another round'), tt('Die Belege reichen noch nicht', 'Evidence not sufficient yet'), tt('Der Walker öffnet weitere Leitlinien und Themen.', 'The walker opens more guidelines and topics.'));
}

async function play(ev) {
  if (S.awaiting && ['scores', 'taken', 'round', 'guidelines'].includes(ev.t)) retract();
  switch (ev.t) {
    case 'loading':
      caption(tt('Vorbereitung', 'Preparing'), tt('Wissensgraph wird geladen', 'Loading the knowledge graph'), tt(`Beim ersten Aufruf lädt der Server Graph und Index (${ev.seconds} s).`, `On the first request the server loads graph and index (${ev.seconds} s).`)); return;
    case 'start': {
      reset(); $('intro')?.remove();
      feed.appendChild(el('div', 'q', esc(ev.question))); trace = el('div', 'trace'); feed.appendChild(trace); down();
      caption(tt('Schritt 1 von 5', 'Step 1 of 5'), tt('Die Frage wird eingeordnet', 'Classifying the question'), tt('Ein Bewertungsmodell prüft, ob nach einem einzelnen Sachverhalt oder nach einer Aufzählung gefragt ist.', 'A scoring model checks whether a single fact or a list is asked for.'));
      return wait(500);
    }
    case 'question':
      step('', tt('Frage eingeordnet', 'Question classified') + at(ev), `<p>${ev.aggregate ? tt('Gefragt ist eine <b>Aufzählung</b>. Der Walker sammelt alle passenden Stellen.', 'A <b>list</b> is asked for. The walker collects all matching passages.') : tt('Gefragt ist ein <b>konkreter Sachverhalt</b>. Der Walker sucht die genaue Stelle.', 'A <b>specific fact</b> is asked for. The walker looks for the exact passage.')}</p>`);
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
    case 'error': (trace || feed).appendChild(el('div', 'err', esc(ev.message))); caption(tt('Fehler', 'Error'), tt('Der Walk wurde abgebrochen', 'The walk was aborted'), ev.message); down(); return;
    case 'fallback':  // embedded: the walker could not answer, the embedding page answers another way
      reset(); placeholders(); idleCaption(); return;
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
    $('c1-n').textContent = tt(`${fmt(items.length)} geprüft`, `${fmt(items.length)} checked`);
    caption(tt('Schritt 2 von 5', 'Step 2 of 5'), tt(`${fmt(items.length)} Leitlinien werden bewertet`, `Scoring ${fmt(items.length)} guidelines`), tt(`Das Bewertungsmodell ${modelName(ev.scorer)} schätzt für jede Leitlinie, ob dort die Antwort stehen kann.`, `The scoring model ${modelName(ev.scorer)} estimates for each guideline whether the answer can be there.`));
    // every guideline as one bar, sorted: the picture of "all were checked"
    const h = $('hist'); h.innerHTML = sorted.map(([di, p]) => `<i data-d="${di}" style="height:2%"></i>`).join(''); h.classList.add('on');
    requestAnimationFrame(() => requestAnimationFrame(() => [...h.children].forEach((b, k) => { b.style.height = clamp(sorted[k][1] * 100, 3, 100) + '%'; })));
    sorted.slice(0, 6).forEach(([di, p], k) => setTimeout(() => showDoc(di, p, false), k * 110 * TEMPO));
    step('', tt('Alle Leitlinien bewertet', 'All guidelines scored') + at(ev), `<div class="num"><b>${fmt(items.length)}</b><span>${tt('Leitlinien geprüft', 'guidelines checked')}</span></div><p>${tt('Jede Leitlinie erhält eine Wahrscheinlichkeit. Das Bewertungsmodell schreibt keinen Text.', 'Each guideline gets a probability. The scoring model writes no text.')}</p>`);
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
    $('c2-n').textContent = tt(`${fmt(S.topicsChecked)} geprüft · ${S.topics.length} geöffnet`, `${fmt(S.topicsChecked)} checked · ${S.topics.length} opened`);
    caption(tt('Schritt 3 von 5', 'Step 3 of 5'), tt(`Themen in „${short(docName(di), 60)}“`, `Topics in “${short(docName(di), 60)}”`), tt(`${items.length} Themen dieser Leitlinie werden bewertet. Nur die passenden werden geöffnet.`, `Scoring ${items.length} topics of this guideline. Only the fitting ones are opened.`));
    let st = trace.querySelector('.st.topics');
    if (!st) st = step('topics', tt('Themen geöffnet', 'Topics opened') + at(ev), `<p>${tt('Jede Leitlinie ist im Graphen nach Themen gegliedert. Der Walker öffnet nur die passenden.', 'Each guideline is organised by topics in the graph. The walker opens only the fitting ones.')}</p>`);
    const b = el('div', 'box'); b.innerHTML = `<div class="k">${esc(short(docName(di), 70))}</div>${kept.map(([ti, p]) => `${esc(topicName(ti))} <span class="tag">${pct(p)}</span>`).join('<br>')}<div class="m">${items.length} ${tt('Themen geprüft', 'topics checked')}</div>`;
    st.appendChild(b); down();
    return wait(1100);
  }
  if (ev.level === 'recommendations') { S.recs += ev.candidates; return; }
  if (ev.level === 'statements') {
    stage(3); S.statements += ev.candidates;
    if (!S.topicsChecked && !$('c2-note')) {  // short guidelines have no topic level: their statements are read directly
      $('c2-n').textContent = tt('nicht nötig', 'not needed');
      const n = el('div', 'empty-note', tt('Die gewählten Leitlinien sind kurz. Der Walker liest ihre Aussagen direkt, ohne Themen-Stufe.', 'The chosen guidelines are short. The walker reads their statements directly, without a topic level.')); n.id = 'c2-note'; $('c2').appendChild(n);
    }
    $('c3-n').textContent = tt(`${fmt(S.statements)} geprüft`, `${fmt(S.statements)} checked`);
    caption(tt('Schritt 4 von 5', 'Step 4 of 5'), tt(`${fmt(ev.candidates)} Aussagen werden gelesen`, `Reading ${fmt(ev.candidates)} statements`), tt('Jede Aussage ist ein einzelner, in sich verständlicher Satz einer Leitlinie. Jede wird einzeln gegen die Frage geprüft.', 'Each statement is a single, self-contained sentence of a guideline. Each is checked against the question on its own.'));
    showCandidates(ev.top || [], 'cand');
    step('', tt('Aussagen bewertet', 'Statements scored') + at(ev), `<div class="num"><b>${fmt(ev.candidates)}</b><span>${tt('Aussagen in den geöffneten Themen', 'statements in the opened topics')}</span></div><p>${tt('Das schnelle Bewertungsmodell prüft alle. Rechts stehen die besten Kandidaten.', 'The fast scoring model checks them all. The best candidates are on the right.')}</p>`);
    return wait(1700);
  }
  if (ev.level === 'rerank') {
    let keep = items.filter(x => x[1] >= .6); if (keep.length < 5) keep = keep.concat(items.filter(x => x[1] < .6 && x[1] >= .25).slice(0, 5 - keep.length));
    S.keep = keep.length;
    $('c3-n').textContent = tt(`${fmt(S.statements)} geprüft → ${fmt(items.length)} nachgeprüft → ${fmt(keep.length)} bestehen`, `${fmt(S.statements)} checked → ${fmt(items.length)} rechecked → ${fmt(keep.length)} pass`);
    caption(tt('Schritt 4 von 5', 'Step 4 of 5'), tt('Strenge Zweitprüfung', 'Strict second check'), tt(`Das Bewertungsmodell ${modelName(ev.scorer)} prüft die ${fmt(items.length)} besten Kandidaten mit einer strengeren Frage erneut: Steht hier wirklich das Gefragte?`, `The scoring model ${modelName(ev.scorer)} rechecks the ${fmt(items.length)} best candidates with a stricter question: does this really state what was asked?`));
    showCandidates(ev.top || [], 'cand');
    step('', tt('Zweitprüfung', 'Second check') + at(ev), `<div class="num"><b>${fmt(items.length)}</b><span class="arr">→</span><b>${fmt(keep.length)}</b><span>${tt('bestehen', 'pass')}</span></div><p>${tt('Ähnliche, aber nicht zutreffende Aussagen scheiden hier aus.', 'Similar but not fitting statements drop out here.')}</p>`);
    if (!keep.length) { S.empty = true; caption(tt('Schritt 4 von 5', 'Step 4 of 5'), tt('Keine Aussage besteht die Prüfung', 'No statement passes the check'), tt('In den Leitlinien steht keine Stelle, die das Gefragte wirklich sagt. Der Walker rät nicht.', 'No passage in the guidelines really says what was asked. The walker does not guess.')); }
    return wait(1600);
  }
}
function updateTopicNote() {
  let n = $('c2-more'); if (!S.hiddenTopics) return;
  if (!n) { n = el('div', 'empty-note'); n.id = 'c2-more'; }
  n.textContent = tt(`+ ${S.hiddenTopics} weitere Themen geöffnet`, `+ ${S.hiddenTopics} more topics opened`); $('c2').appendChild(n);
}
function showCandidates(top, cls) {
  const box = $('c3'); box.innerHTML = '';
  edges = edges.filter(e => !e.b.startsWith('n-s'));
  top.slice(0, Math.min(5, fits('c3', 4.3))).forEach((p, k) => {
    const n = node('n-s' + p.i, cls, `<div class="t">${esc(statementText(p))}</div><div class="s">${esc(short(docName(p.doc), 44))} · ${tt('S.', 'p.')} ${p.page}</div>`, p.p);
    n.style.animationDelay = (k * 80 * TEMPO) + 'ms'; box.appendChild(n);
    link($('n-t' + p.topic) ? 'n-t' + p.topic : 'n-d' + p.doc, 'n-s' + p.i, '');
  });
  if (!top.length) box.appendChild(el('div', 'empty-note', tt('Keine Kandidaten.', 'No candidates.')));
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
  $('c1-n').textContent = tt(`${fmt(Object.keys(S.docScores).length)} geprüft · ${ev.kept.length} gewählt`, `${fmt(Object.keys(S.docScores).length)} checked · ${ev.kept.length} chosen`);
  caption(tt('Schritt 2 von 5', 'Step 2 of 5'), tt(`${ev.kept.length} Leitlinien passen zur Frage`, `${ev.kept.length} ${plural(ev.kept.length, 'guideline fits', 'guidelines fit')} the question`), tt('Der Walker geht nur dort weiter, wo die Antwort stehen kann. Alle anderen Leitlinien bleiben zu.', 'The walker continues only where the answer can be. All other guidelines stay closed.'));
  const st = trace.lastChild, b = el('div', 'box');
  b.innerHTML = `<div class="k">${tt('Gewählt', 'Chosen')}</div>${ev.kept.map(di => `${esc(short(docName(di), 80))} <span class="tag">${pct(S.docScores[di] ?? 0)}</span>`).join('<br>')}`;
  st.appendChild(b); down(); relayout();
  return wait(1200);
}

async function taken(ev) {
  const items = ev.items.slice().sort((a, b) => (b.p ?? 0) - (a.p ?? 0)); S.taken = items;
  stage(4);
  $('c3').innerHTML = ''; edges = edges.filter(e => !e.b.startsWith('n-s'));
  const capS = Math.min(5, fits('c3', 6.2, remPx() * 1.6));
  items.slice(0, capS).forEach((p, k) => {
    const tags = [p.kind ? `<span class="tag g">${kind(p.kind)}</span>` : '', grade(p) ? `<span class="tag">${grade(p)}</span>` : ''].join('');
    const n = node('n-s' + p.i, 'sel', `<div class="t">${esc(statementText(p))}</div><div class="s">${esc(short(docName(p.doc), 44))} · S. ${p.page}</div>${tags ? `<div class="tags">${tags}</div>` : ''}`);
    n.style.animationDelay = (k * 110 * TEMPO) + 'ms'; $('c3').appendChild(n);
    link($('n-t' + p.topic) ? 'n-t' + p.topic : 'n-d' + p.doc, 'n-s' + p.i, 'ev');
  });
  if (items.length > capS) $('c3').appendChild(el('div', 'empty-note', tt(`+ ${items.length - capS} weitere Belege`, `+ ${items.length - capS} more pieces of evidence`)));
  $('c3-n').textContent = tt(`${fmt(S.statements)} geprüft → ${items.length} Belege`, `${fmt(S.statements)} checked → ${items.length} evidence`);
  relayout();
  const chosen = tt(`${items.length} ${items.length === 1 ? 'Beleg' : 'Belege'} ausgewählt`, `${items.length} ${plural(items.length, 'piece', 'pieces')} of evidence chosen`);
  const st = step('ev', chosen + at(ev), `<p>${items.length ? tt('Nur diese Sätze gehen später an das Sprachmodell. Jeder steht wörtlich in der Leitlinie.', 'Only these sentences go to the language model later. Each stands verbatim in the guideline.') : tt('Keine Aussage hat die Prüfung bestanden. Das Sprachmodell bekommt nichts, woraus es eine Behauptung machen könnte.', 'No statement passed the check. The language model gets nothing it could build a claim on.')}</p>`);
  items.slice(0, 4).forEach((p, k) => st.appendChild(evidenceBox(p, k + 1)));
  if (items.length > 4) st.appendChild(el('div', 'more', tt(`+ ${items.length - 4} weitere Belege`, `+ ${items.length - 4} more pieces of evidence`)));
  caption(tt('Schritt 4 von 5', 'Step 4 of 5'), items.length ? chosen : tt('Keine Belege', 'No evidence'), items.length ? tt('Jeder Beleg ist ein wörtlicher Satz einer Leitlinie, mit Seite und Empfehlungsgrad.', 'Each piece of evidence is a verbatim sentence of a guideline, with page and grade.') : tt('Der Walker hat in den Leitlinien keine passende Stelle gefunden.', 'The walker found no fitting passage in the guidelines.'));
  down(); await wait(1600);

  // UMLS: the medical concepts the evidence is about
  const byC = new Map();
  items.forEach(p => (p.concepts || []).forEach(c => { const e = byC.get(c.ci) || {c, props: []}; e.props.push(p.i); byC.set(c.ci, e); }));
  const list = [...byC.values()].sort((a, b) => b.props.length - a.props.length).slice(0, 5);
  if (!list.length) return;
  $('c4-n').textContent = tt(`${list.length} verknüpft`, `${list.length} linked`);
  caption(tt('Schritt 4 von 5', 'Step 4 of 5'), tt('Verknüpfung mit der medizinischen Terminologie', 'Linking to medical terminology'), tt('Die Belege sind an Begriffe des UMLS-Metathesaurus gebunden. So erkennt der Graph denselben Begriff auch unter anderem Namen und in anderen Leitlinien.', 'The evidence is bound to terms of the UMLS Metathesaurus, so the graph recognises the same term under another name and in other guidelines.'));
  const us = step('um', tt('UMLS-Konzepte gefunden', 'UMLS concepts found') + at(ev), `<p>${tt('Jeder Begriff hat im UMLS-Metathesaurus eine feste Kennung (CUI). Über sie ist der Beleg mit allen anderen Stellen im Graphen verbunden, die denselben Begriff meinen.', 'Each term has a fixed identifier (CUI) in the UMLS Metathesaurus. Through it the evidence is connected to every other place in the graph that means the same term.')}</p>`);
  for (let k = 0; k < list.length; k++) {
    const {c, props} = list[k], meta = L.concepts[c.ci] || {}, docs = conceptDocs(c.ci);
    $('c4').appendChild(node('n-u' + c.ci, 'um', `<div class="t">${esc(conceptName(c))}</div><div class="s">${esc(c.cui)}</div>`));
    props.forEach(i => link('n-s' + i, 'n-u' + c.ci, 'um'));
    const b = el('div', 'box um'); b.innerHTML = `<div class="k">${tt('UMLS-Link', 'UMLS link')}</div><span class="nm">${esc(conceptName(c))}</span> <span class="code">${esc(c.cui)}</span>
      ${LANG === 'de' && c.de && c.de !== c.name ? `<div class="en">UMLS-Bezeichnung: ${esc(c.name)}</div>` : ''}
      <div class="m">${tt(`in ${props.length} der Belege · im ganzen Graphen ${fmt(meta.n || 0)} Aussagen in ${fmt(docs)} Leitlinien`, `in ${props.length} of the pieces of evidence · in the whole graph ${fmt(meta.n || 0)} statements in ${fmt(docs)} guidelines`)}</div>`;
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
  $('c5-n').textContent = tt('Sprachmodell aktiv', 'language model active');
  lockCard('on', tt('Sprachmodell formuliert', 'Language model is writing'), n ? tt(`Es erhält nur die ${n} Belege, keine anderen Quellen und nicht sein Allgemeinwissen.`, `It receives only the ${n} pieces of evidence, no other sources and no general knowledge.`) : tt('Es erhält keine Belege und kann deshalb nur sagen, dass nichts gefunden wurde.', 'It receives no evidence and can therefore only say that nothing was found.'));
  S.taken.forEach(p => link('n-s' + p.i, 'n-ans', 'ans')); relayout();
  caption(tt('Schritt 5 von 5', 'Step 5 of 5'), tt('Erst jetzt schreibt ein Sprachmodell', 'Only now does a language model write'), tt('Bis hierher wurde kein Wort generiert. Das Modell formuliert die Antwort ausschließlich aus den ausgewählten Belegen.', 'Up to here not a word was generated. The model writes the answer solely from the chosen evidence.'));
  const s = step('llm busy', tt('Antwort wird formuliert', 'Writing the answer') + at(ev), `<p>${tt('Das Sprachmodell DeepSeek V4.1 Flash bekommt die Frage und die Belege. Jede Aussage der Antwort muss auf einen Beleg verweisen.', 'The language model DeepSeek V4.1 Flash receives the question and the evidence. Every statement of the answer must point to a piece of evidence.')}</p>`); s.id = 'st-write';
  return wait(2200);
}

async function answer(ev) {
  S.awaiting = false; if (ev.status === 'not covered') S.empty = true;
  const ws = $('st-write'); if (ws) { ws.classList.remove('busy'); ws.querySelector('h3').innerHTML = tt('Antwort formuliert', 'Answer written') + at(ev); }
  const srcs = ev.sources || [], byN = Object.fromEntries(srcs.map(s => [s.n, s]));
  const cited = new Set([...ev.text.matchAll(/\[(\d+)\]/g)].map(m => +m[1]));
  $('c5-n').textContent = tt('fertig', 'done');
  lockCard('done', ev.status === 'not covered' ? tt('Nicht in den Leitlinien', 'Not in the guidelines') : tt('Antwort mit Belegen', 'Answer with evidence'),
    ev.status === 'not covered' ? tt('Der Walker nennt die Lücke, statt zu raten.', 'The walker names the gap instead of guessing.') : tt(`${cited.size} ${cited.size === 1 ? 'Quelle' : 'Quellen'} im Text, jede mit Seite und Originalzitat.`, `${cited.size} ${plural(cited.size, 'source', 'sources')} in the text, each with page and original quote.`));
  const box = el('div', 'answer'), tx = el('div', 'tx'), src = el('div', 'srcbox');
  box.innerHTML = `<div class="k"><svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/></svg>${ev.status === 'not covered' ? tt('Antwort: nicht in den Leitlinien', 'Answer: not in the guidelines') : tt('Antwort aus den Belegen', 'Answer from the evidence')}</div>`;
  box.append(tx, src); trace.parentNode.appendChild(box);
  let n = 0;
  for (const part of ev.text.split(/(\[\d+\])/g).filter(Boolean)) {
    const m = part.match(/^\[(\d+)\]$/);
    if (m) { const s = byN[+m[1]], c = el('span', 'cite w', m[1]); c.style.animationDelay = (n++ * 22 * TEMPO) + 'ms'; if (s) c.onclick = () => showSource(s, c, box, src); tx.appendChild(c); }
    else part.split(/(\s+)/).forEach(w => { const sp = el('span', 'w', esc(w)); sp.style.animationDelay = (n++ * 22 * TEMPO) + 'ms'; tx.appendChild(sp); });
  }
  caption(tt('Fertig', 'Done'), ev.status === 'not covered' ? tt('Das steht nicht in den Leitlinien', 'This is not in the guidelines') : tt('Die Antwort steht', 'The answer is ready'),
    ev.status === 'not covered' ? tt('Ohne Beleg gibt es keine Behauptung. Ein üblicher Chatbot würde hier eine Antwort formulieren.', 'Without evidence there is no claim. An ordinary chatbot would write an answer here.') : tt('Jede Zahl in eckigen Klammern führt zur Quelle. Ein Klick zeigt sie im Verlauf und markiert sie im Graphen.', 'Each number in square brackets leads to the source. A click shows it in the history and marks it in the graph.'));
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
  if (box) box.appendChild(el('div', 'cost', tt(`<span>Dauer <b>${sec(ev.seconds)}</b></span><span>davon Graph-Walk <b>${sec(ev.graph_seconds)}</b></span><span>Kosten <b>${usd(ev.usd)}</b></span><span><b>${fmt(ev.decision_calls)}</b> Bewertungen</span><span><b>${fmt(ev.language_model_calls)}</b> Sprachmodell-Aufruf${ev.language_model_calls === 1 ? '' : 'e'}</span>`,
      `<span>Duration <b>${sec(ev.seconds)}</b></span><span>of which graph walk <b>${sec(ev.graph_seconds)}</b></span><span>Cost <b>${usd(ev.usd)}</b></span><span><b>${fmt(ev.decision_calls)}</b> scorings</span><span><b>${fmt(ev.language_model_calls)}</b> language-model ${plural(ev.language_model_calls, 'call', 'calls')}</span>`)));
  down();
}

/* ---------------------------------------------------------------- stream */
const queue = []; let playing = false;
async function pump() {
  if (playing) return; playing = true;
  while (queue.length) {
    const ev = queue.shift();
    try { await play(ev); } catch (e) { console.error(e); }
    if (EMBED) parent.postMessage({walker: 'played', t: ev.t}, location.origin);
  }
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
  } catch (e) { queue.push({t: 'error', message: tt('Die Verbindung wurde unterbrochen.', 'The connection was interrupted.')}); pump(); }
  busy = false; pump();
}

/* ---------------------------------------------------------------- page */
function setup() {
  const q = $('q');
  fillExamples();
  $('composer').onsubmit = e => { e.preventDefault(); ask(q.value); };
  q.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q.value); } });
  q.addEventListener('input', () => { q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 120) + 'px'; });
  $('fs').onclick = () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {});
  document.addEventListener('fullscreenchange', () => { document.documentElement.classList.toggle('fs', !!document.fullscreenElement); setTimeout(relayout, 150); });
  document.addEventListener('keydown', e => { if (e.key.toLowerCase() === 'f' && !/TEXTAREA|INPUT/.test(document.activeElement.tagName)) $('fs').click(); });
  $('tempo').onclick = () => { TEMPO = TEMPO === 1 ? .3 : 1; tempoLabel(); };
  new ResizeObserver(relayout).observe($('cols'));
}
function fillExamples() {
  const box = $('examples'); box.innerHTML = '';
  examples().forEach(t => { const b = el('button', 'ex', esc(t)); b.type = 'button'; b.onclick = () => ask(t); box.appendChild(b); });
}
function tempoLabel() { $('tempo-t').textContent = TEMPO === 1 ? tt('Präsentation', 'Presentation') : tt('Echtzeit', 'Real time'); $('tempo').title = tt('Wiedergabe: ', 'Playback: ') + (TEMPO === 1 ? tt('Präsentationstempo', 'presentation pace') : tt('Echtzeit', 'real time')); }
function idleCaption() { caption(tt('Wissensgraph der Leitlinien', 'Knowledge graph of the guidelines'), tt('Bereit für eine Frage', 'Ready for a question'), tt('Der Walker geht Stufe für Stufe vom Ganzen zum Detail. Jede Stufe wird hier sichtbar.', 'The walker goes step by step from the whole to the detail. Each step becomes visible here.')); }
// the fixed texts of walker.html: German in the file, English by exact text (text nodes and a few attributes)
const STATIC_EN = {
  'Leitlinien-Antworten mit Beleg für jeden Satz': 'Guideline answers with evidence for every sentence', 'Präsentation': 'Presentation', 'Vollbild': 'Full screen',
  'So arbeitet der Graph Walker': 'How the Graph Walker works', 'Erst lesen und belegen, dann formulieren': 'Read and cite first, then write',
  'Ein üblicher Chatbot formuliert sofort aus dem Gedächtnis eines Sprachmodells. Der Graph Walker sucht zuerst im Wissensgraphen der Leitlinien die Stellen, die die Frage beantworten. Erst danach formuliert ein Sprachmodell die Antwort, und zwar nur aus diesen Stellen.':
    'An ordinary chatbot answers at once from the memory of a language model. The Graph Walker first searches the knowledge graph of the guidelines for the passages that answer the question. Only then does a language model write the answer, and only from those passages.',
  'Auswählen statt erfinden': 'Select instead of invent', 'Bis zur Antwort entscheiden nur Bewertungsmodelle über „passt“ oder „passt nicht“. Sie erzeugen keinen Text.': 'Until the answer, only scoring models decide “fits” or “does not fit”. They generate no text.',
  'Jeder Satz mit Quelle': 'Every sentence with a source', 'Leitlinie, Seite und Originalzitat sind bei jeder Aussage der Antwort hinterlegt.': 'Guideline, page and original quote are attached to every statement of the answer.',
  'Keine Antwort ohne Beleg': 'No answer without evidence', 'Findet der Walker keine passende Stelle, sagt er das, statt zu raten.': 'If the walker finds no fitting passage, it says so instead of guessing.',
  'Beispielfragen': 'Example questions', 'Wissensgraph der Leitlinien': 'Knowledge graph of the guidelines', 'Bereit für eine Frage': 'Ready for a question',
  'Der Walker geht Stufe für Stufe vom Ganzen zum Detail. Jede Stufe wird hier sichtbar.': 'The walker goes step by step from the whole to the detail. Each step becomes visible here.',
  'Leitlinien': 'Guidelines', 'noch nicht geprüft': 'not checked yet', 'alle Leitlinien, nach Passung sortiert': 'all guidelines, sorted by fit', 'Themen': 'Topics', 'Aussagen': 'Statements',
  'UMLS-Konzepte': 'UMLS concepts', 'Antwort': 'Answer', 'Sprachmodell gesperrt': 'language model locked', 'Dauer': 'Duration', 'Kosten': 'Cost', 'Bewertungen': 'Scorings',
  'Sprachmodell-Aufrufe': 'Language-model calls', 'gewählter Pfad': 'chosen path', 'Beleg': 'Evidence', 'UMLS-Verknüpfung': 'UMLS link', 'Wissensgraph wird geladen …': 'Loading the knowledge graph …',
  'Verlauf': 'History', 'Zurück zu TIB Chat': 'Back to TIB Chat', 'Wiedergabe: Präsentationstempo': 'Playback: presentation pace', 'Wiedergabetempo': 'Playback pace', 'Vollbild (F)': 'Full screen (F)',
  'Frage zu den Herz-Leitlinien eingeben …': 'Ask about the heart guidelines …', 'Frage': 'Question', 'Frage senden': 'Send question', 'Pfad durch den Wissensgraphen': 'Path through the knowledge graph',
  'Leitlinien-Antworten': 'Guideline answers'};
const ATTRS = ['title', 'aria-label', 'placeholder'], orig = new WeakMap();
function applyStatic() {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) {
    const key = orig.get(n) ?? n.nodeValue.trim();
    if (!(key in STATIC_EN)) continue;
    orig.set(n, key); n.nodeValue = n.nodeValue.replace(n.nodeValue.trim(), LANG === 'de' ? key : STATIC_EN[key]);
  }
  document.querySelectorAll('[title],[aria-label],[placeholder]').forEach(e => ATTRS.forEach(a => {
    if (!e.hasAttribute(a)) return;
    const k = e.getAttribute('data-de-' + a) ?? e.getAttribute(a);
    if (k in STATIC_EN) { e.setAttribute('data-de-' + a, k); e.setAttribute(a, LANG === 'de' ? k : STATIC_EN[k]); }
  }));
  document.documentElement.lang = LANG;
}
function libraryHead() {
  const umlsLinks = L.pc.reduce((a, c) => a + c.length, 0);
  $('g-lib').innerHTML = [[L.docs.length, tt('Leitlinien', 'Guidelines')], [L.topics.length, tt('Themen', 'Topics')], [L.keys.length, tt('Aussagen', 'Statements')], [L.concepts.length, tt('UMLS-Konzepte', 'UMLS concepts')]]
    .map(([n, t]) => `<div><dd>${fmt(n)}</dd><dt>${t}</dt></div>`).join('');
  $('g-lib').title = tt(`${fmt(umlsLinks)} Verknüpfungen zwischen Aussagen und UMLS-Konzepten`, `${fmt(umlsLinks)} links between statements and UMLS concepts`);
}
// switch the language between two walks (an embedding page sends it before each walk)
function setLang(l) {
  if (!['de', 'en'].includes(l) || l === LANG) return;
  LANG = l; applyStatic(); tempoLabel(); fillExamples();
  if (L) { libraryHead(); if (!playing && !queue.length) { reset(); placeholders(); idleCaption(); } }
}
function placeholders() {  // before the first question: what each stage will do, with the real sizes of the graph
  const ph = (n, t) => `<div class="ph"><b>${n}</b><span>${t}</span></div>`;
  $('c1').innerHTML = ph(fmt(L.docs.length), tt('Leitlinien. Für jede schätzt ein Bewertungsmodell, ob dort die Antwort stehen kann.', 'Guidelines. For each, a scoring model estimates whether the answer can be there.'));
  $('c2').innerHTML = ph(fmt(L.topics.length), tt('Themen. Große Leitlinien öffnet der Walker nur an den passenden Themen.', 'Topics. The walker opens large guidelines only at the fitting topics.'));
  $('c3').innerHTML = ph(fmt(L.keys.length), tt('Aussagen. Einzelne Sätze der Leitlinien, jeder mit Seite und Originalzitat.', 'Statements. Single sentences of the guidelines, each with page and original quote.'));
  $('c4').innerHTML = ph(fmt(L.concepts.length), tt('UMLS-Konzepte. Feste medizinische Begriffe, über die Aussagen verbunden sind.', 'UMLS concepts. Fixed medical terms through which statements are connected.'));
}
async function boot() {
  setup(); applyStatic(); tempoLabel(); reset();
  const s = await fetch('/api/session').then(r => r.json()).catch(() => ({}));
  if (!s.authenticated) { if (EMBED) $('loading-t').textContent = tt('Bitte erneut anmelden.', 'Please sign in again.'); else location.href = '/'; return; }
  const res = await fetch('/api/walker/layout', {cache: 'no-store'});
  if (!res.ok) { $('loading-t').textContent = tt('Die Daten des Graph Walkers sind auf diesem Server nicht installiert.', 'The Graph Walker data is not installed on this server.'); return; }
  L = await res.json();
  libraryHead(); placeholders(); $('loading').classList.add('gone');
  if (EMBED) {
    window.addEventListener('message', e => {
      if (e.origin !== location.origin || e.source !== parent) return;
      const m = e.data || {};
      if (m.walker === 'tempo') TEMPO = clampTempo(m.value, TEMPO);
      else if (m.walker === 'lang') setLang(m.value);
      else if (m.walker === 'event' && m.ev && typeof m.ev.t === 'string') { queue.push(m.ev); pump(); }
    });
    parent.postMessage({walker: 'ready'}, location.origin);
    return;
  }
  q.focus?.();
  const pre = PARAMS.get('q'); if (pre) ask(pre);
}
const q = $('q');
boot();
})();
