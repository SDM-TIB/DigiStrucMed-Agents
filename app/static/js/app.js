// TIB Chat frontend. one classic script, no build step. sections in order:
//   core . start screen . library . guido's live graph . views, settings, videos . search . chat
//   source overlay + graph explorer . guido's timeline . video call . screen share . live stage
// json-render lives in jsonrender.js (es module) and plugs in through window.JR.
'use strict';
const $ = s => document.querySelector(s);
// random id per browser, so the usage dashboard can tell devices apart. nothing else is stored
const DEVICE = localStorage.getItem('tib-device') || (() => { const d = Math.random().toString(36).slice(2, 10); localStorage.setItem('tib-device', d); return d; })();
const HEADERS = {'X-TIB-Chat': '1', 'X-Device': DEVICE, 'Content-Type': 'application/json'};

async function post(path, body) {
  const r = await fetch(path, {method: 'POST', headers: HEADERS, body: JSON.stringify(body || {})});
  const x = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== '/api/login') { showLogin(); throw Error('Please sign in again.'); }
  if (!r.ok) throw Error(x.error || 'Connection failed');
  return x;
}
const status = m => { $('#status').textContent = m || ''; };

/* auth */
function showLogin() { $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); $('#password').focus(); }
function showApp() { $('#login').classList.add('hidden'); $('#boot').classList.add('hidden'); clearTimeout(bootPoll); $('#app').classList.remove('hidden'); loadData(); pollGpu(); updateFsHint(); }

/* start screen: choose how long the GPU stays awake, start it, explain the agents while it boots */
let bootHold = 3600, bootPoll = 0, bootEntered = false;
const fmtHold = sec => sec < 3600 ? `${sec / 60} minutes` : sec === 3600 ? '1 hour' : `${sec / 3600} hours`;
const fmtClock = x => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, '0')}`;
async function showBoot(afterLogin) {
  let st = {status: 'offline'};
  try { st = await (await fetch('/api/gpu')).json(); } catch {}
  gpuState = st;
  if (st.status === 'online' && !afterLogin) return showApp();   // reload while it is already running
  $('#login').classList.add('hidden'); $('#app').classList.add('hidden'); $('#boot').classList.remove('hidden');
  const cold = Math.max(60, Math.round((st.last_cold_start_s || 220) / 10) * 10);
  document.querySelectorAll('.boot-cold').forEach(e => e.textContent = `${Math.round(cold / 60)} minutes`);
  bootEntered = false; bootTavus();
  if (st.status === 'waking') return bootWait();
  $('#boot-choose').classList.remove('hidden'); $('#boot-wait').classList.add('hidden');
  $('#boot-go-t').textContent = st.status === 'online' ? 'Keep them awake and enter' : 'Start the GPU'; setDial(dialIdx, true);
  setTimeout(() => $('#boot-go').focus(), 50);
}
// Duration dial: four snap points on a track (click, drag or arrow keys); shows until when the GPU stays up at least.
const DIAL = [[300, '5', 'minutes', 'Quick test'], [1800, '30', 'minutes', 'Quick look'], [3600, '1', 'hour', 'Demo or talk'], [7200, '2', 'hours', 'Workshop'], [10800, '3', 'hours', 'Full session']];
let dialIdx = 2;
$('#dial-stops').innerHTML = DIAL.map(([, n, u], i) => `<button data-i="${i}" tabindex="-1"><i></i>${n} ${u === 'minutes' ? 'min' : 'h'}</button>`).join('');
function setDial(i, instant) {
  dialIdx = Math.max(0, Math.min(DIAL.length - 1, i)); const [sec, n, unit, use] = DIAL[dialIdx], pct = 100 * dialIdx / (DIAL.length - 1);
  bootHold = sec;
  $('#dial-fill').style.width = pct + '%'; $('#dial-thumb').style.left = pct + '%';
  document.querySelectorAll('#dial-stops button').forEach((b, k) => { b.classList.toggle('on', k === dialIdx); b.classList.toggle('past', k < dialIdx); });
  const num = $('#dial-num');
  if (num.textContent !== n && !instant) { num.classList.remove('tick'); void num.offsetWidth; num.classList.add('tick'); }
  num.textContent = n; $('#dial-unit').textContent = unit; $('#dial-use').textContent = use;
  const t = new Date(Date.now() + ((gpuState.last_cold_start_s || 220) + sec) * 1000);
  $('#dial-until').textContent = t.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  const tr = $('#dial-track'); tr.setAttribute('aria-valuenow', dialIdx); tr.setAttribute('aria-valuetext', `${n} ${unit}`);
  document.querySelectorAll('.boot-hold').forEach(e => e.textContent = fmtHold(sec));
}
(function dialInput() {
  const tr = $('#dial-track'), at = x => { const r = tr.querySelector('.dial-rail').getBoundingClientRect(); return Math.round((x - r.left) / r.width * (DIAL.length - 1)); };
  let drag = false;
  tr.addEventListener('pointerdown', e => { drag = true; tr.setPointerCapture(e.pointerId); tr.classList.add('drag'); setDial(at(e.clientX)); });
  tr.addEventListener('pointermove', e => { if (drag) setDial(at(e.clientX)); });
  tr.addEventListener('pointerup', () => { drag = false; tr.classList.remove('drag'); });
  tr.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); setDial(dialIdx + 1); }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); setDial(dialIdx - 1); }
    if (e.key === 'Enter') $('#boot-go').click();
  });
  setInterval(() => { if (!$('#boot').classList.contains('hidden')) setDial(dialIdx, true); }, 30000);
})();
setTimeout(() => setDial(2, true));  // after gpuState exists
$('#boot-go').onclick = async () => {
  $('#boot-go').disabled = true;
  try { gpuState = await post('/api/gpu/wake', {hold_s: bootHold}); } catch (e) { gpuState = {status: 'offline'}; }
  $('#boot-go').disabled = false;
  if (gpuState.status === 'online') return showApp();
  bootWait();
};
$('#boot-skip2').onclick = $('#boot-luna').onclick = () => { switchAgent('luna'); showApp(); };
// remaining Tavus video minutes (estimated from the call history; Tavus has no balance endpoint)
async function tavusMinutes() {
  try { const x = await (await fetch('/api/tavus/usage')).json(); if (!x.ok) throw 0; return x; } catch { return null; }
}
function tavusText(x) { return `≈ <b>${x.remaining_min.toFixed(1)} of ${x.plan_min} min</b> video call time left · ${x.used_min} min used in ${x.calls} calls since ${new Date(x.since).toLocaleDateString('en-GB', {day: 'numeric', month: 'short'})}`; }
async function bootTavus() {
  const el = $('#boot-tavus'), x = await tavusMinutes();
  if (!x) { el.querySelector('span').textContent = 'Video call minutes unavailable'; return; }
  el.querySelector('span').innerHTML = tavusText(x); el.classList.toggle('low', x.remaining_min < 10); el.classList.toggle('out', x.remaining_min <= 0);
}
$('#boot-retry').onclick = () => { $('#boot-err').classList.add('hidden'); $('#boot-go').click(); };
$('#boot-enter').onclick = () => showApp();
function bootWait() {
  $('#boot-choose').classList.add('hidden'); $('#boot-wait').classList.remove('hidden'); $('#boot-wait').classList.remove('boot-wait-done');
  $('#boot-enter').classList.add('hidden'); $('#boot-err').classList.add('hidden'); bootTick();
}
async function bootTick() {
  clearTimeout(bootPoll);
  try { gpuState = await (await fetch('/api/gpu')).json(); } catch {}
  const st = gpuState, target = Math.max(60, Math.round((st.last_cold_start_s || 220) / 10) * 10);
  if (st.status === 'waking') {
    const t = st.waking_for_s || 0;
    $('#boot-state').textContent = 'Waking up'; $('#boot-wtitle').textContent = t < 25 ? 'Starting the GPU…' : t < 70 ? 'Loading Lino’s fine-tuned model…' : 'Warming up…';
    $('#boot-fill').style.width = Math.min(97, 100 * t / target) + '%';
    $('#boot-elapsed').textContent = fmtClock(t);
    $('#boot-eta').textContent = t < target ? `about ${fmtClock(target - t)} left · last start took ${fmtClock(target)}` : 'almost there…';
    $('#bs-1').classList.add('on'); $('#bs-1').classList.toggle('done', t > 25);
    $('#bs-2').classList.toggle('on', t > 25); $('#bs-2').classList.toggle('done', t > 70); $('#bs-3').classList.toggle('on', t > 70);
    bootPoll = setTimeout(bootTick, 1000);
  } else if (st.status === 'online') {
    $('#boot-wait').classList.add('boot-wait-done');
    $('#boot-fill').style.width = '100%'; ['#bs-1', '#bs-2', '#bs-3'].forEach(k => $(k).classList.add('on', 'done'));
    $('#boot-state').textContent = 'Ready'; $('#boot-wtitle').textContent = `Lino and Guido are awake for at least ${fmtHold(st.hold_s || bootHold)}`;
    $('#boot-eta').textContent = `started in ${fmtClock(st.cold_start_s || 0)}`;
    $('#boot-enter').classList.remove('hidden'); $('#boot-enter').focus();
    if (!bootEntered) { bootEntered = true; bootPoll = setTimeout(showApp, 2200); }
  } else {
    $('#boot-state').textContent = 'Not started'; $('#boot-err').classList.remove('hidden');
  }
}
$('#login-form').onsubmit = async e => {
  e.preventDefault(); $('#login-error').textContent = ''; $('#login-btn').disabled = true;
  try { await post('/api/login', {password: $('#password').value}); $('#password').value = ''; showBoot(true); }
  catch (err) { $('#login-error').textContent = err.message === 'Wrong password' ? 'That password is not correct.' : err.message; $('#password').select(); }
  finally { $('#login-btn').disabled = false; }
};
$('#logout').onclick = async () => { await post('/api/logout').catch(() => {}); showLogin(); };
fetch('/api/session', {headers: {'X-Device': DEVICE}}).then(r => r.json()).then(x => x.authenticated ? showBoot(false) : showLogin()).catch(showLogin);

/* full-screen hint: shown after every reload, hidden while in full screen */
let fsDismissed = false;
function isFullscreen() { return !!document.fullscreenElement || (innerHeight >= screen.height - 2 && innerWidth >= screen.width - 2); }
function updateFsHint() {
  const dismissed = fsDismissed;
  $('#fs-hint').classList.toggle('hidden', dismissed || isFullscreen() || $('#app').classList.contains('hidden'));
}
$('#fs-go').onclick = () => document.documentElement.requestFullscreen?.().catch(() => {});
$('#fs-x').onclick = () => { fsDismissed = true; updateFsHint(); };
document.addEventListener('fullscreenchange', updateFsHint); addEventListener('resize', updateFsHint);

/* data */
let loaded = false, LIB = [];
function renderLibrary(q) {
  const grid = $('#sources'); grid.innerHTML = '';
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const docs = LIB.filter(d => terms.every(t => (d.title + ' ' + (d.ref || '') + ' ' + d.lang).toLowerCase().includes(t)));
  if (!docs.length) { grid.innerHTML = '<p class="lib-empty">No documents match.</p>'; return; }
  docs.forEach((d, i) => {
    const b = document.createElement('button'); b.className = 'doc';
    if (i < 24) b.style.animation = `rise .4s ${i * 25}ms var(--ease) both`;
    b.innerHTML = `<span class="top"><span class="lang">${d.lang}</span>${d.ref ? `<span class="ref">${esc(d.ref)}</span>` : ''}<span>${d.pages.length} page${d.pages.length > 1 ? 's' : ''}</span></span>`
      + `<h4>${esc(d.title)}</h4><p>${esc(d.preview)}</p>`;
    b.onclick = () => openDocument(d);
    grid.append(b);
  });
}
$('#lib-q').addEventListener('input', e => renderLibrary(e.target.value));

function loadData() {
  if (loaded) return; loaded = true;
  fetch('/api/library').then(r => r.json()).then(x => {
    LIB = x.documents;
    $('#kb-docs').textContent = LIB.length.toLocaleString('en');
    $('#kb-pages').textContent = x.pages.toLocaleString('en');
    renderLibrary('');
  }).catch(() => {});
}

/* ---------------- guido's live knowledge graph (force-graph) ---------------- */
const KG = {fg: null, nodes: [], links: [], byId: new Map(), cited: new Set(), pathIds: new Set(), hop: 0, hover: null};
const KG_COL = {concept: '#4b63e0', rec: '#2fa36b', guide: '#8a90a0', seedRing: 'rgba(75,99,224,.28)', link: 'rgba(110,118,140,.35)', hot: '#4b63e0'};
function kgInit() {
  if (KG.fg || !window.ForceGraph) return;
  const el = $('#kg-canvas');
  KG.fg = ForceGraph()(el).backgroundColor('rgba(0,0,0,0)').maxZoom(3.2).minZoom(0.25).onEngineStop(() => { if (KG.camIds && KG.camIds.size) KG.fg.zoomToFit(900, KG.wave === -1 ? 40 : 60, n => KG.camIds.has(n.id)); }).autoPauseRedraw(false).cooldownTime(Infinity).cooldownTicks(260).d3VelocityDecay(0.28)
    .nodeId('id').linkSource('source').linkTarget('target')
    .linkColor(l => l.hot ? KG_COL.hot : KG_COL.link).linkWidth(l => l.hot ? 1.8 : 0.8)
    .linkDirectionalParticles(l => l.hot ? 3 : 0).linkDirectionalParticleWidth(3).linkDirectionalParticleSpeed(0.011)
    .linkDirectionalParticleColor(() => KG_COL.hot)
    .nodeCanvasObject(kgDraw).nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, kgR(n) + 3, 0, 2 * Math.PI); ctx.fill(); })
    .onNodeHover(n => { KG.hover = n || null; el.style.cursor = n ? 'pointer' : ''; kgTip(n); })
    .onNodeClick(n => kgTip(n, true));
  KG.fg.d3Force('charge').strength(n => n.kind === 'question' ? -600 : -220);
  KG.fg.d3Force('link').distance(l => l.role === 'question' ? 90 : l.role === 'source' ? 40 : 70);
  KG.fg.d3Force('collide', kgCollide(n => n.kind === 'question' ? 48 : KG.wave === -1 ? (KG.pathIds.has(n.id) ? 46 : 8) : 13));
  new ResizeObserver(() => KG.fg.width(el.clientWidth).height(el.clientHeight)).observe(el);
}
// Simple collision force (labels need room); O(n^2) is fine for < 150 nodes.
function kgCollide(radius) {
  let nodes = []; const rad = typeof radius === 'function' ? radius : n => n.kind === 'question' ? radius * 1.7 : radius / 2;
  const f = alpha => {
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j]; let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01;
      const min = rad(a) + rad(b);
      if (d < min) { const k = (min - d) / d * 0.5 * Math.min(1, alpha * 4 + 0.2); dx *= k; dy *= k;
        if (a.fx == null) { a.x -= dx; a.y -= dy; } if (b.fx == null) { b.x += dx; b.y += dy; } }
    }
  };
  f.initialize = n => { nodes = n; };
  return f;
}
function kgRecLabel(n, lim) {
  const m = n.label.match(/Class ([^·]+?)(?: · |$)/), e = n.label.match(/LoE (\S+)/);
  const grade = [m && m[1].trim(), e && e[1]].filter(Boolean).join('/');
  const t = (n.text || n.label).replace(/\s+/g, ' '), room = lim - (grade ? grade.length + 3 : 0);
  return (t.length > room ? t.slice(0, room - 1).trimEnd() + '…' : t) + (grade ? ' · ' + grade : '');
}
function kgR(n) { return n.kind === 'concept' ? (n.hop === 0 ? 9 : 6) : n.kind === 'rec' ? 5 : 4; }
function kgDraw(n, ctx, scale) {
  if (n.kind === 'question') {
    const fs = 13 / scale, txt = n.label.length > 46 ? n.label.slice(0, 45) + '…' : n.label;
    ctx.font = `600 ${fs}px Segoe UI, system-ui, sans-serif`;
    const w = ctx.measureText(txt).width + 22 / scale, h = fs * 2.2;
    ctx.fillStyle = '#1d2233'; ctx.beginPath(); ctx.roundRect ? ctx.roundRect(n.x - w / 2, n.y - h / 2, w, h, h / 2) : ctx.rect(n.x - w / 2, n.y - h / 2, w, h); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(txt, n.x, n.y + 0.5 / scale);
    return;
  }
  const born = Math.min(1, (performance.now() - n.born) / 600), r = kgR(n) * (0.4 + 0.6 * born);
  const dim = KG.wave === -1 && KG.pathIds.size && !KG.pathIds.has(n.id);
  ctx.globalAlpha = born * (dim ? 0.18 : 1);
  if (n.hop === 0 && n.kind === 'concept') { ctx.fillStyle = KG_COL.seedRing; ctx.beginPath(); ctx.arc(n.x, n.y, r + 6 + 2 * Math.sin(performance.now() / 380), 0, 2 * Math.PI); ctx.fill(); }
  if (KG.cited.has(n.id)) { ctx.strokeStyle = '#f2b136'; ctx.lineWidth = 3 / scale; ctx.beginPath(); ctx.arc(n.x, n.y, r + 3.5, 0, 2 * Math.PI); ctx.stroke(); }
  ctx.fillStyle = KG_COL[n.kind] || KG_COL.concept; ctx.beginPath();
  if (n.kind === 'rec') { const w = r * 1.7; ctx.roundRect ? ctx.roundRect(n.x - w / 2, n.y - w / 2, w, w, 2) : ctx.rect(n.x - w / 2, n.y - w / 2, w, w); }
  else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
  ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.2 / scale; ctx.stroke();
  const showLabel = KG.hover === n || (KG.wave === -1 ? KG.pathIds.has(n.id) : n.hop === 0 || n.wave === KG.wave);
  if (showLabel && born > 0.3) {
    const big = n.hop === 0 || KG.cited.has(n.id), fs = (big ? 13 : 11.5) / scale, lim = n.kind === 'rec' ? 38 : 28;
    const txt = n.kind === 'rec' ? kgRecLabel(n, lim) : n.label.length > lim ? n.label.slice(0, lim - 1) + '…' : n.label;
    ctx.font = `${big ? 650 : 550} ${fs}px Segoe UI, system-ui, sans-serif`;
    const w = ctx.measureText(txt).width, h = fs * 1.45, y = n.y + r + 4 / scale;
    ctx.fillStyle = KG.cited.has(n.id) ? 'rgba(255,246,222,.96)' : 'rgba(255,255,255,.92)';
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(n.x - w / 2 - 5 / scale, y, w + 10 / scale, h, 5 / scale) : ctx.rect(n.x - w / 2 - 5 / scale, y, w + 10 / scale, h); ctx.fill();
    ctx.strokeStyle = 'rgba(30,40,70,.10)'; ctx.lineWidth = 1 / scale; ctx.stroke();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = n.hop === 0 ? '#1d2233' : n.kind === 'rec' ? '#1f6b47' : '#2c3346';
    ctx.fillText(txt, n.x, y + h / 2 + 0.5 / scale);
  }
  ctx.globalAlpha = 1;
}
function kgTip(n, sticky) {
  const tip = $('#kg-tip');
  if (!n) { if (!tip.dataset.sticky) tip.classList.add('hidden'); return; }
  tip.dataset.sticky = sticky ? '1' : '';
  tip.innerHTML = n.kind === 'rec' ? `<b>${esc(n.label)}</b>${esc(n.text || '')}` : `<b>${esc(n.label)}</b>${n.kind === 'guide' ? 'Source guideline' : 'Concept in the knowledge graph'}`;
  tip.classList.remove('hidden');
}
// The camera follows the traversal: centre on the newest wave of nodes, then settle on them.
function kgCamera(ids) {
  clearTimeout(KG.camT); KG.camIds = ids;
  const go = (ms) => ids.size > 1 ? KG.fg.zoomToFit(ms, 80, n => ids.has(n.id)) : (() => { const n = KG.byId.get([...ids][0]); if (n) { KG.fg.centerAt(n.x, n.y, ms); KG.fg.zoom(2.2, ms); } })();
  KG.camT = setTimeout(() => { go(700); KG.camT = setTimeout(() => go(900), 1100); }, 250);
}
function kgReset(question) {
  kgInit(); if (KG.fg) KG.fg.d3Force('charge').strength(n => n.kind === 'question' ? -600 : -220);
  KG.nodes = []; KG.links = []; KG.byId.clear(); KG.cited.clear(); KG.pathIds = new Set(); KG.hop = 0; KG.wave = 0;
  if (question) { const q = {id: 'q', kind: 'question', label: question, hop: -1, born: performance.now(), x: 0, y: 0, fx: 0, fy: 0}; KG.nodes.push(q); KG.byId.set('q', q); }
  $('#kg-tip').classList.add('hidden'); $('#kg-tip').dataset.sticky = '';
  $('#kg-empty').classList.toggle('hidden', !!question); $('#kg-status').textContent = 'Starting traversal…';
  KG.fg && KG.fg.graphData({nodes: [...KG.nodes], links: [...KG.links]}); KG.fg.d3ReheatSimulation();
}
// Final view: the answer path as a clean layered layout (question → start concepts → cited recommendations →
// linked concepts), one recommendation per row so no label overlaps; the rest of the explored graph stays dimmed behind.
function kgAnswerLayout() {
  const P = [...KG.pathIds].map(id => KG.byId.get(id)).filter(Boolean);
  const seeds = P.filter(n => n.kind === 'concept' && n.hop === 0), recs = P.filter(n => n.kind === 'rec');
  // linked concepts: keep the 6 most connected to the cited recommendations, the rest fade into the background
  const deg = new Map(); KG.links.forEach(l => { if (!l.hot) return; [l.source.id || l.source, l.target.id || l.target].forEach(id => deg.set(id, (deg.get(id) || 0) + 1)); });
  const allOthers = P.filter(n => n.kind !== 'question' && !seeds.includes(n) && !recs.includes(n)).sort((a, b) => (deg.get(b.id) || 0) - (deg.get(a.id) || 0));
  const others = allOthers.slice(0, 6);
  allOthers.slice(6).forEach(n => KG.pathIds.delete(n.id));
  KG.links.forEach(l => { const a = l.source.id || l.source, b = l.target.id || l.target; if (l.hot && !(KG.pathIds.has(a) && KG.pathIds.has(b))) l.hot = false; });
  // laid out in screen pixels at zoom 1, sized to the panel, so labels (fixed screen size) never overlap
  const W = Math.max(320, KG.fg.width()), H = Math.max(320, KG.fg.height()), dx = Math.min(170, W * 0.24), rows = [];
  const pairs = a => { for (let i = 0; i < a.length; i += 2) rows.push(a.slice(i, i + 2)); };
  if (KG.byId.has('q')) rows.push([KG.byId.get('q')]);
  pairs(seeds); recs.forEach(n => rows.push([n])); pairs(others);
  const rh = Math.max(46, Math.min(96, (H - 110) / Math.max(1, rows.length - 1)));
  const zoom = Math.min(1, (H - 90) / ((rows.length - 1) * rh + 60), (W - 40) / (2 * dx + 280));  // shrink only if it cannot fit
  const target = new Map(), y0 = -(rows.length - 1) * rh / 2 - 10;
  let zig = 0;
  rows.forEach((row, i) => row.forEach((n, j) => {
    const x = row.length === 2 ? (j ? dx : -dx) : n.kind === 'rec' ? (zig++ % 2 ? dx * 0.35 : -dx * 0.35) : 0;
    target.set(n, [x, y0 + i * rh + (i > 0 ? 14 : 0)]);
  }));
  const from = new Map([...target.keys()].map(n => [n, [n.x, n.y]])), t0 = performance.now(), D = 900;
  const ease = t => 1 - Math.pow(1 - t, 3);
  KG.fg.d3ReheatSimulation(); KG.fg.centerAt(0, 0, D); KG.fg.zoom(zoom, D);
  (function step() {
    const k = ease(Math.min(1, (performance.now() - t0) / D));
    target.forEach(([tx, ty], n) => { const [fx0, fy0] = from.get(n); n.fx = fx0 + (tx - fx0) * k; n.fy = fy0 + (ty - fy0) * k; });
    if (k < 1 && KG.wave === -1) requestAnimationFrame(step);
  })();
}
function kgUpdate(ev) {
  if (!KG.fg) kgInit(); if (!KG.fg) return;
  $('#kg-empty').classList.add('hidden');
  if (ev.action === 'done') {
    KG.cited = new Set(ev.cited || []); KG.links.forEach(l => l.hot = false);
    KG.pathIds = new Set(KG.cited);
    const kind = id => (KG.byId.get(id) || {}).kind;
    KG.links.forEach(l => { const a = l.source.id || l.source, b = l.target.id || l.target;
      if ((KG.cited.has(a) || KG.cited.has(b)) && kind(a) !== 'guide' && kind(b) !== 'guide') { l.hot = true; KG.pathIds.add(a); KG.pathIds.add(b); } });
    KG.links.forEach(l => { const a = l.source.id || l.source, b = l.target.id || l.target;
      if (a === 'q' && KG.pathIds.has(b)) { l.hot = true; KG.pathIds.add('q'); } });
    if (!KG.cited.size) KG.pathIds = new Set();
    $('#kg-status').textContent = `${KG.nodes.length} nodes explored · ${KG.cited.size} recommendations used`;
    KG.wave = -1;
    KG.fg.graphData({nodes: [...KG.nodes], links: [...KG.links]});
    const focusSet = KG.pathIds.size ? KG.pathIds : new Set(KG.nodes.map(n => n.id));
    clearTimeout(KG.camT);
    if (KG.pathIds.size) { KG.camIds = null; kgAnswerLayout(); return; }
    KG.camIds = focusSet; KG.fg.d3ReheatSimulation();
    KG.camT = setTimeout(() => KG.fg.zoomToFit(900, 36, n => focusSet.has(n.id)), 1000); return;
  }
  KG.links.forEach(l => l.hot = false);
  const anchor = ev.focus && KG.byId.get(ev.focus);
  ev.nodes.forEach((n, i) => {
    const parent = anchor || KG.nodes[0];
    const node = {...n, born: performance.now() + i * 40, x: parent ? parent.x + (Math.random() - .5) * 30 : (Math.random() - .5) * 40, y: parent ? parent.y + (Math.random() - .5) * 30 : (Math.random() - .5) * 40};
    KG.nodes.push(node); KG.byId.set(n.id, node);
  });
  if (ev.action === 'seed' && KG.byId.has('q')) ev.nodes.forEach(n => KG.links.push({source: 'q', target: n.id, role: 'question', hot: true}));
  ev.links.forEach(l => { if (KG.byId.has(l.source) && KG.byId.has(l.target)) KG.links.push({source: l.source, target: l.target, role: l.role, hot: true}); });
  KG.hop = Math.max(KG.hop, ev.hop || 0); KG.wave = (KG.wave || 0) + 1;
  const fresh = new Set(ev.nodes.map(n => n.id)); if (ev.focus) fresh.add(ev.focus);
  KG.nodes.forEach(n => { if (fresh.has(n.id)) n.wave = KG.wave; });
  $('#kg-status').textContent = `${ev.action === 'seed' ? 'Found start concepts' : ev.action === 'hop2' ? 'Hop 2' : ev.action === 'hop1' ? 'Hop 1' : 'Expanding'} · ${KG.nodes.length} nodes`;
  KG.fg.graphData({nodes: [...KG.nodes], links: [...KG.links]}); KG.fg.d3ReheatSimulation();
  kgCamera(fresh);
}

/* views: chat / sources */
function view(name) {
  $('.window').classList.toggle('no-side', name !== 'chat');
  for (const v of ['chat', 'sources', 'videos', 'settings', 'search']) {
    $('#view-' + v).classList.toggle('hidden', v !== name);
    $('#nav-' + v).classList.toggle('active', v === name); $('#nav-' + v).toggleAttribute('aria-current', v === name);
  }
  if (name !== 'videos') closePlayer(true);
}
$('#nav-videos').onclick = () => { buildVideos(); view('videos'); };
$('#nav-settings').onclick = () => { view('settings'); loadSettings(); };
// Shut the Modal GPU down now (two-step confirm), instead of waiting for the idle timeout
let gpuOffArm = 0;
$('#gpu-off').onclick = async () => {
  const b = $('#gpu-off'), t = b.querySelector('span');
  if (!b.classList.contains('confirm')) { b.classList.add('confirm'); t.textContent = 'Click again to shut down'; clearTimeout(gpuOffArm);
    gpuOffArm = setTimeout(() => { b.classList.remove('confirm'); t.textContent = 'Shut down GPU'; }, 4000); return; }
  clearTimeout(gpuOffArm); b.disabled = true; t.textContent = 'Shutting down…';
  try { const x = await post('/api/gpu/shutdown'); gpuState = x; renderGpu();
    $('#set-lino-note').textContent = x.ok ? `GPU shut down${x.stopped ? ` (${x.stopped} container${x.stopped > 1 ? 's' : ''} stopped)` : ''}. Lino and Guido are asleep until the next wake.` : 'Marked asleep; Modal will stop the container after its idle window.'; }
  catch (e) { $('#set-lino-note').textContent = 'Could not shut down: ' + e.message; }
  b.disabled = false; b.classList.remove('confirm'); t.textContent = 'Shut down GPU';
  loadSettings();
};
async function loadSettings() {
  tavusMinutes().then(x => {
    if (!x) { $('#tv-note').textContent = 'Video call minutes are unavailable right now.'; return; }
    $('#tv-min').textContent = x.remaining_min.toFixed(1);
    $('#tv-gauge').style.setProperty('--pct', Math.max(0, Math.min(100, 100 * x.remaining_min / x.plan_min)));
    $('#tv-note').innerHTML = `${x.used_min} of ${x.plan_min} plan minutes used in ${x.calls} calls since ${new Date(x.since).toLocaleDateString('en-GB', {day: 'numeric', month: 'long'})}. Estimated from the call history (Tavus offers no balance API).`;
  });
  try {
    const x = await (await fetch('/api/settings')).json();
    if (x.modal && x.modal.ok) {
      $('#credit-pct').textContent = Math.round(x.modal.remaining_pct) + '%';
      $('#gauge').style.setProperty('--pct', x.modal.remaining_pct);
      $('#credit-note').textContent = `${Math.round(x.modal.used_pct)}% of this month's plan credits used. Resets on ${new Date(x.modal.resets).toLocaleDateString('en-GB', {day: 'numeric', month: 'long'})}.`;
    } else $('#credit-note').textContent = 'Credit information is unavailable right now.';
    const st = x.lino.status;
    $('#set-lino-dot').className = 'presence ' + (st === 'online' ? '' : st === 'waking' ? 'wake' : 'off');
    $('#set-lino-state').textContent = st === 'online' ? 'Online' : st === 'waking' ? 'Waking up' : 'Asleep';
    $('#gpu-off').classList.toggle('hidden', st === 'offline');
    if (st === 'online') { const m = Math.round(x.lino.awake_for_s / 60); $('#set-lino-note').textContent = `NVIDIA L4 GPU on Modal. Stays awake for ${Math.floor(m / 60) ? Math.floor(m / 60) + ' h ' : ''}${m % 60} min more.`; }
  } catch { $('#credit-note').textContent = 'Credit information is unavailable right now.'; }
}
const VIDEOS = [{"file": "MedicalVideo", "title": "Heart failure medicines and their side effects", "duration": "2:03", "lang": "EN", "tag": "Fine-tuned model prompt", "desc": "A doctor avatar walks patients through the main heart failure medicines, ARNI, ACE inhibitors, MRA and SGLT2 inhibitors, and explains which side effects to watch for, such as low blood pressure, cough or high potassium. Generated with Animaker from the prompt produced by our NeSyCoT fine-tuned model."}, {"file": "Heroes-MedicalVideo", "title": "The heart heroes", "duration": "2:16", "lang": "EN", "tag": "Storytelling version", "desc": "The same request told as a story: each medicine class becomes a superhero (ARNI, ACE, beta-blocker, MRA and SGLT2) that protects the heart in its own way. A playful format designed to make the four pillars of heart failure therapy memorable."}, {"file": "HFPlatform-Video", "title": "Medikamente bei Herzinsuffizienz", "duration": "4:30", "lang": "DE", "tag": "Medical platform reference", "desc": "Reference video from a medical patient platform for the same request, in German. Hero characters explain what each medicine does, for example relaxing blood vessels and relieving the heart, and which side effects can occur, from tiredness to high potassium."}];
function buildVideos() {
  if ($('#vid-grid').children.length) return;
  VIDEOS.forEach((v, i) => {
  const b = document.createElement('button'); b.className = 'vid'; b.style.animation = `rise .45s ${i * 70}ms var(--ease) both`;
  b.innerHTML = `<div class="shot" style="background-image:url(/static/videos/${v.file}.jpg)"><span class="lang">${v.lang}</span><span class="play"><svg viewBox="0 0 24 24"><path d="M7 4.5v15l12-7.5Z"/></svg></span><span class="dur">${v.duration}</span></div>`
    + `<div class="info"><span class="tag">${esc(v.tag)}</span><h4>${esc(v.title)}</h4><p>${esc(v.desc)}</p></div>`;
  b.onclick = () => openPlayer(v, b);
  $('#vid-grid').append(b);
  });
}
let playerFrom = null;
function playerClip(rect) {
  const pl = $('#player'), W = innerWidth, H = innerHeight;
  if (!rect) { ['--pt', '--pr', '--pb', '--pl'].forEach(k => pl.style.setProperty(k, '0px')); pl.style.setProperty('--pradius', '0px'); return; }
  pl.style.setProperty('--pt', rect.top + 'px'); pl.style.setProperty('--pl', rect.left + 'px');
  pl.style.setProperty('--pr', (W - rect.right) + 'px'); pl.style.setProperty('--pb', (H - rect.bottom) + 'px'); pl.style.setProperty('--pradius', '14px');
}
function openPlayer(v, from) {
  const pl = $('#player'), vid = $('#player-video'); playerFrom = from;
  $('#player-tag').textContent = v.tag; $('#player-title').textContent = v.title; $('#player-desc').textContent = v.desc;
  vid.poster = `/static/videos/${v.file}.jpg`; vid.src = `/static/videos/${v.file}.mp4`;
  pl.classList.remove('hidden', 'closing'); pl.style.transition = 'none'; playerClip(from.querySelector('.shot').getBoundingClientRect());
  pl.getBoundingClientRect(); pl.style.transition = '';
  setTimeout(() => playerClip(null), 30);
  setTimeout(() => vid.play().catch(() => {}), 350);
  $('#player-close').focus();
}
function closePlayer(instant) {
  const pl = $('#player'); if (pl.classList.contains('hidden')) return;
  const vid = $('#player-video'); vid.pause();
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  const done = () => { pl.classList.add('hidden'); vid.removeAttribute('src'); vid.load(); };
  if (instant || !playerFrom) return done();
  pl.classList.add('closing'); playerClip(playerFrom.querySelector('.shot').getBoundingClientRect()); setTimeout(done, 620);
}
$('#player-close').onclick = () => closePlayer();
addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#player').classList.contains('hidden') && !document.fullscreenElement) closePlayer(); });
$('#nav-chat').onclick = () => view('chat');
$('#nav-sources').onclick = () => view('sources');

/* global search: agents, conversations, documents, full-text pages (BM25), knowledge graph, videos */
const SRCH_IC = {
  chat: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.8 7L4 20l1-4.6A8 8 0 1 1 21 12Z"/></svg>',
  doc: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  page: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 10h16M4 14h10M4 18h7"/></svg>',
  c: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/></svg>',
  r: '<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>'};
let srchSeq = 0, srchTimer = 0;
function openSearch() { view('search'); loadData(); if (!$('#srch-try').children.length) {
  $('#srch-try').innerHTML = ['aspirin', 'SGLT2 inhibitors', 'HFrEF', 'Guido', 'side effects', 'Herzinsuffizienz'].map(t => `<button>${esc(t)}</button>`).join('');
  $('#srch-try').querySelectorAll('button').forEach(b => b.onclick = () => { $('#srch-q').value = b.textContent; runSearch(b.textContent); });
  runSearch(''); }
  setTimeout(() => $('#srch-q').focus(), 50); }
$('#nav-search').onclick = openSearch;
addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !$('#app').classList.contains('hidden')) { e.preventDefault(); openSearch(); $('#srch-q').select(); } });
$('#srch-q').addEventListener('input', e => { clearTimeout(srchTimer); srchTimer = setTimeout(() => runSearch(e.target.value), 200); });
$('#srch-q').addEventListener('keydown', e => { if (e.key === 'Enter') $('#srch-res .srch-it')?.click(); });
function srchTerms(q) { return q.toLowerCase().split(/\s+/).filter(t => t.length > 1); }
function srchMark(text, terms) {
  let h = esc(text); if (!terms.length) return h;
  const re = new RegExp('(' + terms.map(t => esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
  return h.replace(re, '<mark>$1</mark>');
}
function srchHas(text, terms) { const t = (text || '').toLowerCase(); return terms.every(x => t.includes(x)); }
function srchAround(text, terms, n = 160) {
  const t = text.replace(/\s+/g, ' '), i = terms.length ? t.toLowerCase().indexOf(terms[0]) : 0;
  const a = Math.max(0, i - 50); return (a ? '… ' : '') + t.slice(a, a + n) + (t.length > a + n ? ' …' : '');
}
async function runSearch(q) {
  const seq = ++srchSeq, terms = srchTerms(q), res = $('#srch-res');
  if (!terms.length) { res.innerHTML = '<p class="srch-empty">Search across Luna, Lino and Guido, your conversations, all guideline documents and pages, the knowledge graph and the videos.</p>'; return; }
  const groups = [];
  // local: agents, conversations, documents, videos
  const ag = Object.entries(AGENTS).filter(([, a]) => srchHas(a.name + ' ' + a.tagline, terms));
  if (ag.length) groups.push(['Agents', ag.map(([id, a]) => ({ic: `<span class="ic round" style="background-image:url(/static/img/${id}.jpg)"></span>`, title: a.name, text: a.tagline, go: () => { view('chat'); switchAgent(id); }}))]);
  const conv = [];
  Object.entries(AGENTS).forEach(([id, a]) => a.history.forEach(m => { if (m.text && srchHas(m.text, terms)) conv.push({ic: `<span class="ic">${SRCH_IC.chat}</span>`, title: (m.role === 'user' ? 'You' : a.name) + ' · chat with ' + a.name, text: srchAround(m.text, terms), go: () => { view('chat'); switchAgent(id); }}); }));
  if (conv.length) groups.push(['Conversations', conv.slice(0, 6)]);
  const docs = LIB.filter(d => srchHas(d.title + ' ' + (d.ref || ''), terms));
  if (docs.length) groups.push(['Documents', docs.slice(0, 6).map(d => ({ic: `<span class="ic">${SRCH_IC.doc}</span>`, title: d.title, text: d.preview, meta: `${d.ref ? d.ref + ' · ' : ''}${d.pages.length} pages · ${d.lang}`, go: () => openDocument(d)}))]);
  const vids = VIDEOS.filter(v => srchHas(v.title + ' ' + v.desc + ' ' + v.tag, terms));
  const vidGroup = vids.length ? ['Videos', vids.map(v => ({ic: `<span class="ic vid shot" style="background-image:url(/static/videos/${v.file}.jpg)"></span>`, title: v.title, text: v.desc, meta: `${v.tag} · ${v.duration} · ${v.lang}`, go: el => openPlayer(v, el)}))] : null;
  srchRender(groups.concat(vidGroup ? [vidGroup] : []), terms, true);
  // server: full-text pages + knowledge graph
  let x; try { x = await fetch('/api/search?q=' + encodeURIComponent(q)).then(r => r.json()); } catch { x = {pages: [], kg: []}; }
  if (seq !== srchSeq) return;
  const kg = x.kg.map(n => ({ic: `<span class="ic ${n.kind === 'rec' ? 'r' : 'c'}">${SRCH_IC[n.kind === 'rec' ? 'r' : 'c']}</span>`,
    title: n.kind === 'rec' ? n.text : n.label, text: n.kind === 'rec' ? '' : n.desc || '', meta: n.kind === 'rec' ? n.label : `Concept · ${n.n_recs || 0} recommendations`,
    go: () => { srcList = [{n: 'KG', rec_id: n.id, search: true, title: n.kind === 'rec' ? n.label : n.label, ref: 'Knowledge graph'}]; srcIdx = 0; renderSource(); $('#srcx').classList.remove('hidden'); }}));
  const pages = x.pages.map(p => ({ic: `<span class="ic">${SRCH_IC.page}</span>`, title: p.title, text: p.snippet, meta: `${p.ref ? p.ref + ' · ' : ''}page ${p.page}`,
    go: () => { const d = LIB.find(d => d.pages.some(([id]) => id === p.page_id)); if (!d) return; openDocument(d); srcIdx = Math.max(0, d.pages.findIndex(([id]) => id === p.page_id)); renderSource(); }}));
  const all = groups.slice();
  if (kg.length) all.push(['Knowledge graph', kg]);
  if (pages.length) all.push(['Guideline pages', pages]);
  if (vidGroup) all.push(vidGroup);
  srchRender(all, terms, false);
}
function srchRender(groups, terms, loading) {
  const res = $('#srch-res');
  if (!groups.length && !loading) { res.innerHTML = '<p class="srch-empty">Nothing found. Try a drug, a condition or a guideline.</p>'; return; }
  res.innerHTML = (loading ? '<div class="srch-load"></div>' : '') + groups.map(([name, items], gi) => `<section class="srch-grp" style="animation-delay:${gi * 40}ms"><h3>${name}<span>${items.length}</span></h3>` + items.map((it, i) =>
    `<button class="srch-it" data-g="${gi}" data-i="${i}">${it.ic}<span class="tx"><b>${srchMark(it.title, terms)}</b>${it.text ? `<p>${srchMark(it.text, terms)}</p>` : ''}${it.meta ? `<small>${esc(it.meta)}</small>` : ''}</span></button>`).join('') + '</section>').join('');
  res.querySelectorAll('.srch-it').forEach(b => b.onclick = () => groups[b.dataset.g][1][b.dataset.i].go(b));
}
$('#item-luna').onclick = () => switchAgent('luna');

/* ---------------- text chat ---------------- */
// Mahsa's best storyboard prompt (Documents/best_prompt.txt, verbatim). Attached via the "Video storyboard" pill;
// the visitor's own request is appended below it and both are sent to Lino as one user message.
const STORYBOARD_PROMPT = "You are Dr. Maya Patel, a board‑certified physician, medical educator, and storytelling specialist with over 20 years of clinical experience and a focus on health‑literacy. You bring a clinician’s insight, patient‑centered communication expertise, and a keen eye for visual storytelling to every storyboard you create.\n\nYou are a medical storyboard generator. Your entire response must be a single, pure JSON object—no surrounding prose, markdown fences, or explanations.\n\n**Output schema**\n- The JSON object must contain exactly two keys:\n  1. `title` – a string.\n  2. `scenes` – an array of scene objects.\n- Each scene object must contain **exactly** the following keys (all strings):\n  - `scene_number`\n  - `narration`\n  - `visual_description`\n  - `on_screen_text`\n\n**General rules**\n- Use the exact key names shown above; do not add, rename, or omit any keys.\n- The JSON must be syntactically valid (proper quotes, commas, braces).\n- Provide the number of scenes requested by the user (typically one). Do not add extra scenes.\n- Write the narration in simple, patient‑friendly language (≤ 8th‑grade reading level), using short sentences.\n- The `visual_description` should briefly describe an image a storyboard artist could draw.\n- The `on_screen_text` must contain the exact phrase(s) the user asked to highlight (e.g., drug names, button labels).\n- Do not include any additional text before or after the JSON object.\n\n**Content checklist (apply as needed)**\n- For drug‑group requests, list the exact groups requested and a concise, lay‑person explanation of why each helps.\n- For procedural or UI requests, describe only the elements the user asked for (e.g., “Download button”, “Print”, “Fill‑in fields”).\n- Avoid technical jargon, unrelated details, or invented medical information.\n\n**Validation**\n- Before finishing, mentally verify that the JSON can be parsed, all required keys are present, and the content matches the user’s request. If any check fails, regenerate the offending part.";
let attached = false;
function setAttach(on) {
  attached = !!on && agent === AGENTS.lino;
  $('#att').classList.toggle('hidden', !attached);
  $('#msg').placeholder = attached ? 'Describe the video, e.g. 3 scenes on heart failure medicines and their side effects'
    : `Ask ${agent.name}…`;
}
$('#att-x').onclick = () => { setAttach(false); $('#msg').focus(); };
// Lino's storyboard answer is JSON {title, scenes[]}: show it as scene cards when it parses.
function renderStoryboard(st) {
  const raw = st.raw, a = raw.indexOf('{'), b = raw.lastIndexOf('}');
  if (a < 0 || b <= a) return;
  let j; try { j = JSON.parse(raw.slice(a, b + 1)); } catch { return; }
  if (!j || !Array.isArray(j.scenes) || !j.scenes.length) return;
  st.answer.innerHTML = `<div class="sb"><div class="sb-head"><span>Storyboard</span><h4>${esc(String(j.title || 'Untitled'))}</h4></div>` + j.scenes.map((sc, i) =>
    `<div class="sb-scene"><span class="sb-n">${esc(String(sc.scene_number || i + 1))}</span><div>`
    + (sc.on_screen_text ? `<b class="sb-ost">${esc(String(sc.on_screen_text))}</b>` : '')
    + (sc.narration ? `<p><em>Narration</em>${esc(String(sc.narration))}</p>` : '')
    + (sc.visual_description ? `<p><em>Visual</em>${esc(String(sc.visual_description))}</p>` : '') + '</div></div>').join('')
    + `</div><div class="codeblock"><header><span>json</span><button class="cb-copy">${COPY_ICON}Copy</button></header><pre><code class="language-json">${esc(JSON.stringify(j, null, 2))}</code></pre></div>`;
  decorateCode(st.answer, true);
}
/* ---------- json-render glue: icons, non-React fallback renderer, card builders ---------- */
const JR_ICONS = new Set(["heart-pulse", "heart", "pill", "pill-bottle", "activity", "droplet", "droplets", "shield-alert", "shield-check", "stethoscope", "syringe", "thermometer", "brain", "scale", "apple", "dumbbell", "footprints", "moon", "cigarette-off", "wine-off", "salad", "clock", "calendar", "triangle-alert", "info", "circle-check", "circle-x", "ban", "hospital", "microscope", "flask-conical", "dna", "test-tube", "bed-double", "siren", "zap", "gauge", "trending-up", "trending-down", "list-checks", "book-open", "file-text", "network", "search", "sparkles", "user", "users", "baby", "leaf", "utensils", "bike", "timer", "hand-heart", "bandage", "ambulance"]);
window.jrIcon = n => `https://cdn.jsdelivr.net/npm/lucide-static@1.52.0/icons/${JR_ICONS.has(n) ? n : 'sparkles'}.svg`;
const wdThumb = u => u ? u.replace(/^http:/, 'https:') + (u.includes('?') ? '&' : '?') + 'width=240' : '';
const VIA = {umls_kg: 'Wikidata via UMLS CUI (P2892)', umls_mhh_candidate: 'Wikidata via MHH UMLS candidate', kg: 'Wikidata link from the knowledge graph'};
// Card bodies for the custom catalog components; written against a tiny hyperscript `h` so the same code runs
// inside json-render's React renderer (h = React.createElement) and in the plain-DOM fallback.
window.jrHTML = {
  concept(h, p, children) {
    return h('article', {className: 'jr-concept'},
      h('header', null, p.image ? h('span', {className: 'thumb', style: {backgroundImage: `url(${p.image})`}}) : null,
        h('div', null, h('h4', null, p.title), p.subtitle ? h('p', null, p.subtitle) : null,
          h('div', {className: 'jr-badges'}, (p.badges || []).map((b, i) => b.href ? h('a', {key: i, className: 'jr-badge ' + (b.kind || ''), href: b.href, target: '_blank', rel: 'noopener'}, b.text)
            : h('span', {key: i, className: 'jr-badge ' + (b.kind || ''), title: b.title || ''}, b.text))))),
      children, p.source ? h('div', {className: 'jr-src'}, p.source) : null);
  },
  ids(h, p) {
    return h('div', {className: 'jr-ids'}, (p.items || []).map((x, i) => x.href ? h('a', {key: i, href: x.href, target: '_blank', rel: 'noopener'}, x.label + ' ' + x.value)
      : h('span', {key: i}, x.label + ' ', h('b', null, x.value))));
  },
  relations(h, p) {
    return h('div', {className: 'jr-rel ' + (p.tone || '')}, h('h5', null, h('i'), p.title + (p.total > (p.items || []).length ? ` · ${p.total}` : '')),
      h('div', {className: 'chips'}, (p.items || []).map((x, i) => x.kgId
        ? h('button', {key: i, className: 'kg', title: `In our guideline graph · ${x.kgRecs || 0} recommendations`, style: {animationDelay: i * 25 + 'ms'},
            onClick: () => window.dispatchEvent(new CustomEvent('jr-open-concept', {detail: x.kgId}))}, x.label)
        : h('a', {key: i, href: x.url, target: '_blank', rel: 'noopener', style: {animationDelay: i * 25 + 'ms'}}, x.label)),
        p.total > (p.items || []).length ? h('span', {className: 'more'}, `+${p.total - p.items.length} on Wikidata`) : null));
  },
  excerpt(h, p) {
    const body = p.html ? h('div', {className: 'tbl', ref: el => el && jrTable(el, p.html, p.match)}) : h('blockquote', null, p.text);
    return h('article', {className: 'jr-ex'}, h('header', null, h('div', null, h('h4', null, p.title), h('small', null, `${p.ref} · page ${p.page}${p.grade ? ' · ' + p.grade : ''}`)),
      p.pageId ? h('button', {onClick: () => window.dispatchEvent(new CustomEvent('jr-open-page', {detail: p.pageId}))}, 'Open page') : null), body);
  }
};
function jrTable(el, html, match) {
  if (el.dataset.done === match) return; el.dataset.done = match;
  el.innerHTML = DOMPurify.sanitize(html, {ALLOWED_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'sup', 'sub', 'b', 'i', 'em', 'strong', 'br'], ALLOWED_ATTR: ['colspan', 'rowspan']});
  const key = (match || '').toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 40);
  const row = [...el.querySelectorAll('tr')].find(tr => tr.textContent.toLowerCase().replace(/[^a-z0-9]+/g, '').includes(key));
  if (row) { row.classList.add('hit'); setTimeout(() => el.scrollTo({top: Math.max(0, row.offsetTop - el.clientHeight / 2 + row.offsetHeight / 2), behavior: 'smooth'}), 400); }  // scroll only the table box
}
// plain-DOM fallback if the json-render CDN modules cannot load: same spec, same look
function jrFallback(el) {
  const h = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === 'className') n.className = v; else if (k === 'style') Object.entries(v).forEach(([a, b]) => a.startsWith('--') ? n.style.setProperty(a, b) : n.style[a] = b);
      else if (k === 'onClick') n.onclick = v; else if (k === 'ref') setTimeout(() => v(n)); else n.setAttribute(k, v);
    }
    kids.flat(9).forEach(c => c == null || c === false ? 0 : n.append(c instanceof Node ? c : String(c))); return n;
  };
  const icon = name => h('span', {className: 'jr-ic', style: {'--ic': `url(${jrIcon(name)})`}});
  const R = {
    Board: (p, k) => h('section', {className: 'jr-board'}, p.title ? h('header', null, h('span', {className: 'jr-spark'}), p.title) : null, h('div', {className: 'jr-items'}, k)),
    Point: p => h('div', {className: 'jr-point'}, icon(p.icon), h('div', null, h('b', null, p.title), p.text ? h('p', null, p.text) : null)),
    Stat: p => h('div', {className: 'jr-stat'}, icon(p.icon), h('strong', null, p.value), h('span', null, p.label)),
    Warning: p => h('div', {className: 'jr-warn'}, icon(p.icon || 'triangle-alert'), h('p', null, p.text)),
    Chips: p => h('div', {className: 'jr-chips'}, p.label ? h('span', {className: 'jr-lab'}, p.label) : null, (p.items || []).map(t => h('span', {className: 'jr-chip'}, String(t)))),
    ConceptCard: (p, k) => jrHTML.concept(h, p, k), IdList: p => jrHTML.ids(h, p), RelationList: p => jrHTML.relations(h, p),
    Excerpt: p => jrHTML.excerpt(h, p), Note: p => h('p', {className: 'jr-note'}, p.text)};
  const build = (spec, key, depth = 0) => {
    const e = spec.elements[key]; if (!e || depth > 8 || !R[e.type]) return null;
    return R[e.type](e.props || {}, (e.children || []).map(c => build(spec, c, depth + 1)));
  };
  return {set(spec) { el.replaceChildren(); if (spec && spec.root && spec.elements) { const n = build(spec, spec.root); if (n) el.append(n); } }, unmount() { el.replaceChildren(); }};
}
function jrMount(el) { return window.JR && window.JR.ready ? window.JR.mount(el) : jrFallback(el); }
// minimal SpecStream compiler for the fallback path (JSONL of RFC 6902 "add" patches)
function jrCompiler() {
  if (window.JR && window.JR.ready) return window.JR.compiler();
  const spec = {root: '', elements: {}}; let buf = '';
  const add = (path, value) => {
    const parts = path.split('/').slice(1).map(x => x.replace(/~1/g, '/').replace(/~0/g, '~')); let o = spec;
    for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]] ??= {};
    const last = parts[parts.length - 1]; if (Array.isArray(o) && last === '-') o.push(value); else o[last] = value;
  };
  return {push(chunk) { buf += chunk; const lines = buf.split('\n'); buf = lines.pop(); const newPatches = [];
    for (const l of lines) { try { const op = JSON.parse(l); if (op.op === 'add' || op.op === 'replace') { add(op.path, op.value); newPatches.push(op); } } catch {} }
    return {result: spec, newPatches}; }, getResult: () => spec};
}
// concept card spec from /api/kg/facts (live Wikidata + UMLS)
function conceptSpec(f) {
  const wd = f.wikidata, u = f.umls, badges = [];
  if (u) badges.push({text: 'UMLS ' + u.cui, kind: u.source === 'kg' ? '' : 'cand', title: (u.name ? u.name + (u.type ? ' · ' + u.type : '') + ' · ' : '') + (u.source === 'kg' ? 'normalization in the knowledge graph' : 'MHH retrieval top-1 candidate (unconfirmed)')});
  if (u && u.source !== 'kg') badges.push({text: 'candidate', kind: 'cand', title: 'MHH export 07.10: rank-1 UMLS retrieval candidate, not manually confirmed'});
  if (wd) badges.push({text: 'Wikidata ' + wd.qid, kind: 'wd', href: wd.url});
  if (wd && wd.wikipedia) badges.push({text: 'Wikipedia', kind: 'wd', href: wd.wikipedia});
  const el = {c: {type: 'ConceptCard', props: {title: (wd && wd.label) || f.label, subtitle: (wd && wd.description) || (u && u.name) || '', image: wdThumb(wd && wd.image), badges,
    source: wd ? `Live from Wikidata (${VIA[f.qid_via] || 'link'}) · fetched ${new Date(wd.fetched_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}` : (f.error || (u ? 'UMLS only · no Wikidata item for this CUI' : 'No UMLS or Wikidata link for this concept'))}, children: []}};
  if (wd && wd.ids.length) { el.ids = {type: 'IdList', props: {items: wd.ids.filter(x => x.label !== 'UMLS CUI').slice(0, 8)}, children: []}; el.c.children.push('ids'); }
  (wd ? wd.relations : []).forEach((r, i) => { el['r' + i] = {type: 'RelationList', props: {title: r.title, tone: r.tone, total: r.total, items: r.items.map(x => ({label: x.label, url: x.url, kgId: x.kg_id, kgRecs: x.kg_recs}))}, children: []}; el.c.children.push('r' + i); });
  return {root: 'c', elements: el};
}
const factsCache = new Map();
function getFacts(id) {
  if (!factsCache.has(id)) factsCache.set(id, fetch('/api/kg/facts?id=' + encodeURIComponent(id)).then(r => r.ok ? r.json() : null).catch(() => null)
    .then(f => { if (!f || f.error) factsCache.delete(id); return f; }));  // failed lookups are retried next time
  return factsCache.get(id);
}
const AGENTS = {
  luna: {name: 'Luna', tagline: 'Retrieval-augmented expert on heart guidelines', endpoint: '/api/chat/luna', video: true, history: [], thread: '#thread', preview: '#item-preview', item: '#item-luna', orb: ''},
  lino: {name: 'Lino', tagline: 'Fine-tuned with Neuro-Symbolic Chain-of-Thought (NeSyCoT)', endpoint: '/api/chat/lino', video: false, history: [], thread: '#thread-lino', preview: '#item-preview-lino', item: '#item-lino', orb: 'lino'},
  guido: {name: 'Guido', tagline: 'Finetuned LLM with access to the guideline knowledge graph', endpoint: '/api/chat/guido', video: true, history: [], thread: '#thread-guido', preview: '#item-preview-guido', item: '#item-guido', orb: 'guido'},
};
let agent = AGENTS.luna;
function switchAgent(id) {
  if (chatBusy) return;
  agent = AGENTS[id]; view('chat');
  Object.values(AGENTS).forEach(a => { $(a.thread).classList.toggle('hidden', a !== agent); $(a.item).classList.toggle('active', a === agent); });
  $('#head-name').textContent = agent.name; $('#head-tagline').textContent = agent.tagline;
  $('#head-orb').classList.toggle('lino', agent.orb === 'lino'); $('#head-orb').classList.toggle('guido', agent.orb === 'guido');
  $('#view-chat').classList.toggle('kg-on', agent === AGENTS.guido); if (agent === AGENTS.guido) kgInit();
  $('#start').classList.toggle('hidden', !agent.video);
  const SUG = agent === AGENTS.guido ? [
    ['Tell me about aspirin', 'What can you tell me about aspirin?'],
    ['Aspirin bleeding risks', 'What are the bleeding risks of aspirin combined with other antithrombotic drugs?'],
    ['HFrEF drug classes', 'Which drug classes are recommended for HFrEF, and with which class and level of evidence?']] : agent === AGENTS.lino ? [
    ['Video storyboard', STORYBOARD_PROMPT, 'attach'],
    ['Side effects of ACE inhibitors', 'What are the common side effects of ACE inhibitors?']] : [];
  $('#suggests').innerHTML = SUG.map(([lv, q, kind], i) => `<button class="sug${kind ? ' sug-att' : ''}" style="animation-delay:${i * 60}ms" data-i="${i}" title="${kind ? 'Attach Mahsa’s storyboard prompt, then describe the video' : esc(q)}">${kind ? '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9.5 5 2.5-5 2.5Z"/></svg>' : ''}${esc(lv)}</button>`).join('');
  $('#suggests').classList.toggle('hidden', !SUG.length);
  $('#suggests').querySelectorAll('.sug').forEach(b => b.onclick = () => { const [, q, kind] = SUG[b.dataset.i]; if (kind === 'attach') setAttach(true); else $('#msg').value = q; $('#msg').focus(); });
  setAttach(false);
  $('#msg').placeholder = agent === AGENTS.luna ? 'Ask Luna about heart guidelines…' : `Ask ${agent.name}…`;
  renderGpu(); $('#msg').focus(); scrollFeed();
}
$('#item-lino').onclick = () => switchAgent('lino');
$('#item-guido').onclick = () => switchAgent('guido');
let gpuState = {status: 'offline'}, gpuPoll = 0;
function renderGpu() {
  const st = gpuState.status, sel = agent === AGENTS.lino || agent === AGENTS.guido, who = agent === AGENTS.guido ? 'Guido' : 'Lino';
  document.body.classList.toggle('lino-asleep', st !== 'online');
  $('#lino-dot').className = 'presence ' + (st === 'online' ? '' : st === 'waking' ? 'wake' : 'off');
  $('#lino-time').textContent = st === 'online' ? 'online' : st === 'waking' ? 'waking' : 'offline';
  $('#guido-dot').className = $('#lino-dot').className; $('#guido-time').textContent = $('#lino-time').textContent;
  $('#wake-btn-t').textContent = `Ping ${who} to wake up`;
  $('#wake-orb').className = 'orb ' + (who === 'Guido' ? 'guido sleepy-guido' : 'lino sleepy-lino');
  if (sel) {
    $('#head-orb .presence').className = 'presence ' + (st === 'online' ? '' : st === 'waking' ? 'wake' : 'off');
    const left = Math.round((gpuState.awake_for_s || 0) / 60), h = Math.floor(left / 60), m = left % 60;
    const pill = $('#head-pill'); pill.className = 'rt-pill ' + st;
    pill.textContent = st === 'online' ? `Online · ${h ? h + ' h ' : ''}${m} min left` : st === 'waking' ? `Waking up · ${Math.floor(gpuState.waking_for_s || 0)} s` : 'Asleep';
  } else { $('#head-orb .presence').className = 'presence'; $('#head-pill').className = 'rt-pill hidden'; }
  const showPanel = sel && st !== 'online';
  $('#wake-panel').classList.toggle('hidden', !showPanel);
  $('#msg').disabled = $('#send').disabled = sel && st !== 'online';
  if (!showPanel) return;
  const card = $('#wake-card'); card.className = 'wake-card ' + (st === 'waking' ? 'waking' : 'off');
  $('#wake-state').textContent = st === 'waking' ? 'Waking up' : 'Offline';
  $('#wake-title').textContent = st === 'waking' ? `Waking ${who}…` : `${who} is asleep`;
  $('#wake-text').textContent = st === 'waking'
    ? 'His GPU is starting on Modal and the fine-tuned model is being loaded. This usually takes one to three minutes. After that it stays active as long as you use the page, no matter which agent you talk to.'
    : `${who} shares a GPU with ${who === 'Guido' ? 'Lino' : 'Guido'}, which sleeps when nobody is talking to them. Once awake, it stays active as long as you use the page, no matter which agent you talk to.`;
  $('#wake-btn').classList.toggle('hidden', st === 'waking');
  $('#wake-steps').classList.toggle('hidden', st !== 'waking'); $('#wake-time').classList.toggle('hidden', st !== 'waking');
  if (st === 'waking') {
    const t = gpuState.waking_for_s || 0;
    const target = Math.max(30, Math.round((gpuState.last_cold_start_s || 220) / 10) * 10), fmt = x => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, '0')}`;
    $('#wt-fill').style.width = Math.min(97, 100 * t / target) + '%';
    $('#wt-elapsed').textContent = fmt(t);
    $('#wt-eta').textContent = t < target ? `about ${fmt(target - t)} left · last start took ${fmt(target)}` : 'almost there…';
    $('#ws-1').classList.add('on'); $('#ws-2').classList.toggle('on', t > 20); $('#ws-3').classList.toggle('on', t > 60);
  }
}
async function pollGpu() {
  try { const r = await fetch('/api/gpu'); if (r.ok) gpuState = await r.json(); } catch {}
  renderGpu();
  clearTimeout(gpuPoll);
  gpuPoll = setTimeout(pollGpu, gpuState.status === 'waking' || agent === AGENTS.lino || agent === AGENTS.guido ? 2000 : 30000);
}
$('#wake-btn').onclick = async () => {
  $('#wake-btn').disabled = true;
  try { gpuState = await post('/api/gpu/wake'); } catch (e) { status(e.message); }
  $('#wake-btn').disabled = false; renderGpu(); pollGpu();
};
let chatBusy = false;
const esc = t => t.replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const ICON = {
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  read: '<svg viewBox="0 0 24 24"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5Z"/><path d="M13 4h5.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H13Z"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>'
};
function scrollFeed() { const f = $('#feed'); f.scrollTop = f.scrollHeight; }

// Wrap only text that is new since the last render in a blur-in span.
// Streamdown-style reveal: every new word fades from blur to sharp with a small stagger.
// Re-renders rebuild the DOM, so words still animating are re-wrapped with a negative delay
// and continue where they were instead of snapping to sharp.
function revealNew(root, st) {
  const now = performance.now();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = []; let total = 0, text = '';
  while (walker.nextNode()) { const n = walker.currentNode, v = n.nodeValue; nodes.push({n, a: total, b: total + v.length}); total += v.length; text += v; }
  st.anim = st.anim || [];
  if (total > st.shown) {
    const fresh = text.slice(st.shown);
    for (const m of fresh.matchAll(/\s*\S+\s*/g)) {
      const t = Math.max(now, st.nextT || 0); st.nextT = t + 24;
      st.anim.push({a: st.shown + m.index, b: st.shown + m.index + m[0].length, t});
    }
    st.shown = total;
  }
  st.anim = st.anim.filter(r => now - r.t < 700 && r.a < total);
  for (const {n, a, b} of nodes.reverse()) {
    const rs = st.anim.filter(r => r.b > a && r.a < b).sort((x, y) => y.a - x.a);
    for (const r of rs) {
      const s0 = Math.max(r.a, a) - a, s1 = Math.min(r.b, b) - a;
      n.splitText(s1); const mid = n.splitText(s0);
      const span = document.createElement('span'); span.className = 'blur-in';
      span.style.animationDelay = Math.round(r.t - now) + 'ms';
      mid.parentNode.insertBefore(span, mid); span.appendChild(mid);
    }
  }
  return st.shown;
}
function citeify(html) {
  return html.replace(/\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\](?![^<]*>)/g, (_, g) => g.match(/\d+/g).map(n => `<button class="cite" data-n="${n}" aria-label="Source ${n}">${n}</button>`).join(''));
}

function addAssistant() {
  const el = document.createElement('div'); el.className = 'msg-ai';
  el.innerHTML = '<div class="orb ' + agent.orb + '" aria-hidden="true"></div><div><div class="steps"></div><div class="answer"><div class="thinking"><i></i><i></i><i></i></div></div><div class="refs"></div></div>';
  $(agent.thread).append(el); scrollFeed();
  const st = {el, steps: el.querySelector('.steps'), answer: el.querySelector('.answer'), refs: el.querySelector('.refs'), raw: '', shown: 0, sources: {}, pending: null, frame: 0, lino: agent === AGENTS.lino};
  el.addEventListener('click', e => {
    const c = e.target.closest('.cite, .ref'); if (!c) return;
    showSource(st, +c.dataset.n);
  });
  return st;
}
function settle(st) {
  if (st.pending) { st.pending.classList.remove('pending'); st.pending.querySelector('span').innerHTML = ICON.check; st.pending = null; }
}
function step(st, kind, html, pending) {
  settle(st);
  const d = document.createElement('div'); d.className = 'step' + (pending ? ' pending' : '');
  d.innerHTML = `<span>${ICON[kind]}</span><span class="label">${html}</span>`;
  st.steps.append(d); st.pending = pending ? d : null; scrollFeed();
}
// Lino often answers with bare JSON (storyboards): fence it so it renders as a copy-ready code block.
function fenceJson(raw) {
  const t = raw.trimStart();
  if (/^```/.test(t) || !/^[\[{]/.test(t)) return raw;
  return '```json\n' + raw + (/\n$/.test(raw) ? '' : '\n') + '```';
}
const COPY_ICON = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>';
function decorateCode(root, final) {
  root.querySelectorAll('pre > code').forEach(code => {
    const pre = code.parentElement; if (pre.parentElement.classList.contains('codeblock')) return;
    const lang = ((code.className.match(/language-(\w+)/) || [])[1] || 'code');
    const box = document.createElement('div'); box.className = 'codeblock';
    box.innerHTML = `<header><span>${esc(lang)}</span><button class="cb-copy">${COPY_ICON}Copy</button></header>`;
    pre.replaceWith(box); box.append(pre);
  });
  if (final) root.querySelectorAll('.codeblock code.language-json').forEach(code => {
    let src = code.textContent; try { src = JSON.stringify(JSON.parse(src), null, 2); } catch {}  // pretty-print one-line JSON
    code.innerHTML = esc(src).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g,
      (m, str, colon, lit) => str ? `<span class="${colon ? 'k' : 's'}">${str}</span>${colon || ''}` : lit ? `<span class="b">${m}</span>` : `<span class="n">${m}</span>`);
  });
}
document.addEventListener('click', e => {
  const b = e.target.closest('.cb-copy'); if (!b) return;
  const code = b.closest('.codeblock').querySelector('code');
  navigator.clipboard.writeText(code.textContent).then(() => { b.innerHTML = COPY_ICON + 'Copied'; setTimeout(() => b.innerHTML = COPY_ICON + 'Copy', 1600); }).catch(() => {});
});
function render(st, final) {
  st.frame = 0;
  const html = DOMPurify.sanitize(citeify(marked.parse(st.lino ? fenceJson(st.raw) : st.raw)), {ADD_ATTR: ['data-n']});
  st.answer.innerHTML = html;
  revealNew(st.answer, st);
  if (st.lino) decorateCode(st.answer, final);
  scrollFeed();
}
let srcList = [], srcIdx = 0;
function openDocument(d) {
  srcList = d.pages.map(([id, page]) => ({n: page, page_id: id, title: d.title, ref: d.ref, page, doc: true}));
  srcIdx = 0; renderSource(); $('#srcx').classList.remove('hidden'); $('#srcx-close').focus();
}
function showSource(st, n) {
  const cited = [...st.el.querySelectorAll('.ref')].map(r => +r.dataset.n);
  srcList = (cited.length ? cited : Object.keys(st.sources).map(Number)).map(k => st.sources[k]).filter(Boolean);
  srcIdx = Math.max(0, srcList.findIndex(x => x.n === n));
  if (!srcList.length && st.sources[n]) srcList = [st.sources[n]];
  renderSource(); $('#srcx').classList.remove('hidden'); $('#srcx-close').focus();
}
function renderSource() {
  const s = srcList[srcIdx]; if (!s) return;
  $('#srcx-n').textContent = s.n;
  $('#srcx-title').textContent = s.ref ? s.ref + ' · ' + s.title : s.title;
  $('#srcx-sub').textContent = (s.page ? 'Page ' + s.page : 'Recommendation in the guideline knowledge graph') + (srcList.length > 1 ? ` · source ${srcIdx + 1} of ${srcList.length}` : '');
  $('#srcx-prev').disabled = srcIdx === 0; $('#srcx-next').disabled = srcIdx === srcList.length - 1;
  const page = $('#srcx-page'); page.innerHTML = '<span class="ph">Loading page…</span>'; page.scrollTop = 0;
  const img = new Image(); img.alt = `Page ${s.page} of ${s.title}`;
  img.onload = () => { if (srcList[srcIdx] === s) { page.innerHTML = ''; page.append(img); } };
  img.onerror = () => { if (srcList[srcIdx] === s) page.innerHTML = '<span class="ph">Page image unavailable</span>'; };
  page.classList.remove('kx-mode');
  if (s.rec_id) { $('#srcx-n').textContent = s.n; $('#srcx-sub').textContent = (s.search ? 'Guideline knowledge graph' : 'Recommendation in the guideline knowledge graph') + ' · click any node to explore' + (srcList.length > 1 ? ` · source ${srcIdx + 1} of ${srcList.length}` : ''); return kxShow(s); }
  if (s.page_id) img.src = '/api/page-image/' + encodeURIComponent(s.page_id);
  else page.innerHTML = `<div class="kg-src"><span>Guideline knowledge graph · ${esc(s.ref || 'recommendation')}</span><p>${esc(s.snippet || '')}</p></div>`;
  $('#srcx-n').textContent = s.doc ? 'p. ' + s.page : s.n;
  $('#srcx-sub').textContent = s.doc ? `Page ${srcIdx + 1} of ${srcList.length}` : $('#srcx-sub').textContent;
  $('.srcx-quote h4').textContent = s.doc ? 'Page text' : s.page_id ? 'Passage used by Luna' : 'Recommendation';
  if (s.doc) {
    $('#srcx-quote').className = 'plain'; $('#srcx-quote').textContent = 'Loading…';
    fetch('/api/page/' + encodeURIComponent(s.page_id)).then(r => r.json()).then(x => { if (srcList[srcIdx] === s) $('#srcx-quote').textContent = x.plain.trim(); }).catch(() => {});
  } else {
    const q = (s.snippet || '').replace(/\*\*/g, '');
    $('#srcx-quote').className = ''; $('#srcx-quote').innerHTML = q ? '<mark>' + esc(q) + '</mark>' : '';
  }
}
/* ---------- source explorer: the cited recommendation inside the full guideline graph ---------- */
const KX = {fg: null, nodes: [], links: [], byId: new Map(), linkKeys: new Set(), expanded: new Set(), sel: null, origin: null, used: new Set(), hover: null, nbrs: new Set(), req: 0};
const kxShort = n => { const t = n.kind === 'rec' ? (n.text || n.label) : n.label; return t.length > 34 ? t.slice(0, 33) + '…' : t; };
const kxCol = n => KG_COL[n.kind] || KG_COL.concept;
function kxShow(s) {
  const page = $('#srcx-page'); page.classList.add('kx-mode');
  page.innerHTML = `<div class="kx-canvas" id="kx-canvas"></div>
    <div class="kx-search"><input id="kx-q" placeholder="Search the guideline graph…" autocomplete="off"><div class="kx-res hidden" id="kx-res"></div></div>
    <div class="kx-hint"><span><i style="background:${KG_COL.concept}"></i>Concept</span><span><i class="r" style="background:${KG_COL.rec}"></i>Recommendation</span><span><i style="background:${KG_COL.guide}"></i>Guideline</span><span>Click a node to expand</span></div>
    <div class="kx-zoom"><button id="kx-in" aria-label="Zoom in">+</button><button id="kx-out" aria-label="Zoom out">−</button><button id="kx-fit" aria-label="Fit graph">⤢</button></div>`;
  if (KX.fg) { KX.fg._destructor && KX.fg._destructor(); }
  KX.nodes = []; KX.links = []; KX.byId.clear(); KX.linkKeys.clear(); KX.expanded.clear(); KX.sel = null; KX.nbrs = new Set(); KX.hover = null;
  KX.origin = s.rec_id; KX.used = new Set(s.search ? [] : srcList.map(x => x.rec_id).filter(Boolean));
  const el = $('#kx-canvas');
  KX.fg = ForceGraph()(el).backgroundColor('rgba(0,0,0,0)').maxZoom(4).minZoom(0.2).cooldownTicks(220).d3VelocityDecay(0.3)
    .nodeId('id').linkSource('source').linkTarget('target').autoPauseRedraw(false)
    .linkColor(l => kxHotLink(l) ? KG_COL.hot : KG_COL.link).linkWidth(l => kxHotLink(l) ? 1.6 : 0.8)
    .linkCanvasObjectMode(() => 'after').linkCanvasObject(kxLinkLabel)
    .nodeCanvasObject(kxDraw).nodePointerAreaPaint((n, c, ctx) => { ctx.fillStyle = c; ctx.beginPath(); ctx.arc(n.x, n.y, kgR(n) + 4, 0, 2 * Math.PI); ctx.fill(); })
    .onNodeHover(n => { KX.hover = n || null; el.style.cursor = n ? 'pointer' : 'grab'; })
    .onNodeClick(n => kxOpen(n.id))
    .onNodeDragEnd(n => { n.fx = n.x; n.fy = n.y; })
    .onEngineStop(() => { if (KX.fitIds && !KX.userMoved) KX.fg.zoomToFit(600, 70, n => KX.fitIds.has(n.id)); });
  KX.fg.d3Force('charge').strength(-260);
  KX.fg.d3Force('link').distance(l => l.role === 'source' ? 55 : 90);
  KX.fg.d3Force('collide', kgCollide(30));
  const fit = () => KX.fg.width(el.clientWidth).height(el.clientHeight); fit();
  new ResizeObserver(fit).observe(el);
  $('#kx-in').onclick = () => KX.fg.zoom(KX.fg.zoom() * 1.4, 300);
  $('#kx-out').onclick = () => KX.fg.zoom(KX.fg.zoom() / 1.4, 300);
  $('#kx-fit').onclick = () => KX.fg.zoomToFit(600, 50);
  let t; $('#kx-q').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => kxSearch(e.target.value), 180); });
  $('#kx-q').addEventListener('keydown', e => {
    if (e.key === 'Escape' && e.target.value) { e.stopPropagation(); e.target.value = ''; $('#kx-res').classList.add('hidden'); }
    if (e.key === 'Enter') $('#kx-res button')?.click();
  });
  kxOpen(s.rec_id, true);
}
function kxHotLink(l) { const a = l.source.id || l.source, b = l.target.id || l.target; return !!KX.sel && (a === KX.sel || b === KX.sel); }
function kxLinkLabel(l, ctx, scale) {
  if (!kxHotLink(l) || scale < 1.1 || !l.role || ['mention', 'source'].includes(l.role)) return;
  if (KX.nbrs.size > 12 && KX.hover !== l.source && KX.hover !== l.target) return;  // dense hubs: relation names only on hover
  const a = l.source, b = l.target; if (a.x == null || b.x == null) return;
  const fs = 10 / scale; ctx.font = `500 ${fs}px Segoe UI, system-ui, sans-serif`;
  const w = ctx.measureText(l.role).width + 8 / scale, x = (a.x + b.x) / 2, y = (a.y + b.y) / 2;
  ctx.fillStyle = 'rgba(238,241,252,.95)'; ctx.fillRect(x - w / 2, y - fs * 0.75, w, fs * 1.5);
  ctx.fillStyle = '#3b4ec0'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(l.role, x, y);
}
function kxDraw(n, ctx, scale) {
  const born = Math.min(1, (performance.now() - (n.born || 0)) / 500), sel = KX.sel === n.id;
  const r = (kgR(n) + (sel ? 3 : n.id === KX.origin ? 2 : 0)) * (0.4 + 0.6 * born);
  const focus = sel || KX.nbrs.has(n.id) || KX.hover === n || KX.used.has(n.id);
  ctx.globalAlpha = born * (focus ? 1 : 0.4);
  if (sel) { ctx.fillStyle = KG_COL.seedRing; ctx.beginPath(); ctx.arc(n.x, n.y, r + 7 + 2 * Math.sin(performance.now() / 380), 0, 2 * Math.PI); ctx.fill(); }
  if (KX.used.has(n.id)) { ctx.strokeStyle = '#f2b136'; ctx.lineWidth = 3 / scale; ctx.beginPath(); ctx.arc(n.x, n.y, r + 3.5, 0, 2 * Math.PI); ctx.stroke(); }
  ctx.fillStyle = kxCol(n); ctx.beginPath();
  if (n.kind === 'rec') { const w = r * 1.7; ctx.roundRect ? ctx.roundRect(n.x - w / 2, n.y - w / 2, w, w, 2) : ctx.rect(n.x - w / 2, n.y - w / 2, w, w); }
  else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
  ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = (KX.expanded.has(n.id) ? 2 : 1) / scale; ctx.stroke();
  const show = sel || n.id === KX.origin || KX.hover === n || KX.nbrs.has(n.id) || KX.used.has(n.id) || scale > 1.8;
  if (show && born > 0.3) {
    const big = sel || n.id === KX.origin, fs = (big ? 13 : 11) / scale, txt = kxShort(n);
    ctx.font = `${big ? 650 : 550} ${fs}px Segoe UI, system-ui, sans-serif`;
    const w = ctx.measureText(txt).width, h = fs * 1.45, y = n.y + r + 4 / scale;
    ctx.fillStyle = KX.used.has(n.id) ? 'rgba(255,246,222,.96)' : 'rgba(255,255,255,.93)';
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(n.x - w / 2 - 5 / scale, y, w + 10 / scale, h, 5 / scale) : ctx.rect(n.x - w / 2 - 5 / scale, y, w + 10 / scale, h); ctx.fill();
    ctx.strokeStyle = 'rgba(30,40,70,.10)'; ctx.lineWidth = 1 / scale; ctx.stroke();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = n.kind === 'rec' ? '#1f6b47' : '#2c3346';
    ctx.fillText(txt, n.x, y + h / 2 + 0.5 / scale);
  }
  ctx.globalAlpha = 1;
}
async function kxOpen(id, fresh) {
  const req = ++KX.req;
  let x; try { x = await fetch('/api/kg/node?id=' + encodeURIComponent(id)).then(r => { if (!r.ok) throw 0; return r.json(); }); } catch { return; }
  if (req !== KX.req || !KX.fg) return;
  const now = performance.now(), parent = KX.byId.get(id);
  const add = v => {
    if (KX.byId.has(v.id)) return KX.byId.get(v.id);
    const n = {...v, born: now};
    if (parent && parent.x != null) { n.x = parent.x + (Math.random() - 0.5) * 40; n.y = parent.y + (Math.random() - 0.5) * 40; }
    KX.nodes.push(n); KX.byId.set(n.id, n); return n;
  };
  const self = add(x.node);
  if (fresh) { self.x = 0; self.y = 0; self.fx = 0; self.fy = 0; }
  x.nodes.forEach(add);
  x.links.forEach(l => { const k = l.source + '|' + l.target + '|' + l.role; if (!KX.linkKeys.has(k)) { KX.linkKeys.add(k); KX.links.push({...l}); } });
  KX.expanded.add(id); KX.sel = id; KX.nbrs = new Set(x.nodes.map(n => n.id));
  KX.fg.graphData({nodes: [...KX.nodes], links: [...KX.links]}); KX.fg.d3ReheatSimulation();
  const ids = new Set([id, ...KX.nbrs]); KX.fitIds = ids;
  clearTimeout(KX.camT); KX.camT = setTimeout(() => KX.fg && KX.fg.zoomToFit(700, 70, n => ids.has(n.id)), fresh ? 900 : 500);
  kxDetail(x);
}
function kxDetail(x) {
  const n = x.node, q = $('#srcx-quote'); q.className = 'kx-det';
  $('.srcx-quote h4').textContent = n.id === KX.origin && KX.used.size ? 'Cited recommendation' : 'Selected node';
  const kindLbl = {rec: 'Recommendation', concept: 'Concept', guide: 'Guideline'}[n.kind];
  let h = `<span class="kind"><i class="${n.kind === 'rec' ? 'r' : ''}" style="background:${kxCol(n)}"></i>${kindLbl}</span>`;
  if (n.kind === 'rec') {
    h += `<p class="txt">${esc(n.text)}</p><div class="chips">${n.cls.length ? `<span class="chip">Class ${esc(n.cls.join('/'))}</span>` : ''}${n.loe.length ? `<span class="chip">Level of evidence ${esc(n.loe.join('/'))}</span>` : ''}${n.ref ? `<span class="chip g">${esc(n.ref)}</span>` : ''}${KX.used.has(n.id) ? '<span class="chip used">Used in the answer</span>' : ''}</div>`;
  } else {
    h += `<h3>${esc(n.label)}</h3>` + (n.desc ? `<p class="meta">${esc(n.desc)}</p>` : '')
      + (n.n_recs ? `<div class="chips"><span class="chip">${n.n_recs} recommendation${n.n_recs > 1 ? 's' : ''}</span></div>` : '')
      + (n.aliases && n.aliases.length ? `<p class="meta">Also known as: ${esc(n.aliases.join(', '))}</p>` : '');
  }
  if (n.kind === 'concept') h += '<div class="kx-facts" id="kx-facts"><div class="ph">Loading live context from UMLS and Wikidata…</div></div>';
  const rel = new Map(x.links.map(l => [l.source === n.id ? l.target : l.source, l.role]));
  h += `<h5>Connected · ${x.total}${x.total > x.nodes.length ? ` (showing ${x.nodes.length})` : ''}</h5><div class="kx-nb">` + x.nodes.map(m =>
    `<button data-id="${esc(m.id)}"><span class="k ${m.kind}" style="background:${kxCol(m)}"></span><span>${esc(kxShort(m))}<em>${esc(rel.get(m.id) || '')}</em></span></button>`).join('') + '</div>';
  q.innerHTML = h; q.parentElement.scrollTop = 0;
  q.querySelectorAll('.kx-nb button').forEach(b => b.onclick = () => kxOpen(b.dataset.id));
  if (n.kind === 'concept') getFacts(n.id).then(f => { const box = $('#kx-facts'); if (!box || KX.sel !== n.id) return;
    if (!f) { box.innerHTML = '<div class="ph" style="animation:none">Live context unavailable.</div>'; return; }
    box.innerHTML = ''; const host = document.createElement('div'); host.className = 'jr-host'; box.append(host); jrMount(host).set(conceptSpec(f)); });
}
async function kxSearch(v) {
  const box = $('#kx-res'); if (!v.trim()) { box.classList.add('hidden'); return; }
  let x; try { x = await fetch('/api/kg/search?q=' + encodeURIComponent(v)).then(r => r.json()); } catch { return; }
  if ($('#kx-q').value !== v) return;
  box.innerHTML = x.items.length ? x.items.map(m => `<button data-id="${esc(m.id)}"><span class="k ${m.kind}" style="background:${kxCol(m)}"></span><span>${esc(m.kind === 'rec' ? (m.text.length > 90 ? m.text.slice(0, 89) + '…' : m.text) : m.label)}<small>${m.kind === 'rec' ? esc(m.label) : m.n_recs ? m.n_recs + ' recommendations' : 'Concept'}</small></span></button>`).join('')
    : '<p class="lib-empty" style="padding:10px;margin:0">Nothing found.</p>';
  box.classList.remove('hidden');
  box.querySelectorAll('button').forEach(b => b.onclick = () => { box.classList.add('hidden'); $('#kx-q').value = ''; kxOpen(b.dataset.id); });
}
addEventListener('jr-open-concept', e => {
  if (!$('#srcx').classList.contains('hidden') && KX.fg && KX.byId.size) return kxOpen(e.detail);
  srcList = [{n: 'KG', rec_id: e.detail, search: true, title: e.detail.replace(/^c:/, '').replace(/_/g, ' '), ref: 'Knowledge graph'}]; srcIdx = 0;
  renderSource(); $('#srcx').classList.remove('hidden');
});
addEventListener('jr-open-page', e => { const d = LIB.find(d => d.pages.some(([id]) => id === e.detail)); if (!d) return; openDocument(d); srcIdx = Math.max(0, d.pages.findIndex(([id]) => id === e.detail)); renderSource(); });
function closeSource() { $('#srcx').classList.add('hidden'); }
$('#srcx-close').onclick = closeSource;
$('#srcx').addEventListener('click', e => { if (e.target.id === 'srcx') closeSource(); });
$('#srcx-prev').onclick = () => { if (srcIdx > 0) { srcIdx--; renderSource(); } };
$('#srcx-next').onclick = () => { if (srcIdx < srcList.length - 1) { srcIdx++; renderSource(); } };
addEventListener('keydown', e => {
  if ($('#srcx').classList.contains('hidden')) return;
  if (e.target.matches && e.target.matches('input') && e.key !== 'Escape') return;
  if (e.key === 'Escape') closeSource(); else if (e.key === 'ArrowLeft') $('#srcx-prev').click(); else if (e.key === 'ArrowRight') $('#srcx-next').click();
});
function finishRefs(st) {
  settle(st);
  const cited = [...new Set([...st.raw.matchAll(/\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g)].flatMap(m => m[1].match(/\d+/g).map(Number)))].filter(n => st.sources[n]);
  cited.sort((a, b) => a - b).forEach((n, i) => {
    const s = st.sources[n];
    const b = document.createElement('button'); b.className = 'ref'; b.dataset.n = n; b.style.animationDelay = (i * 60) + 'ms';
    b.title = s.title;
    b.innerHTML = `<span class="n">${n}</span><span class="t">${esc(s.ref || s.title)}</span><span class="pg">${s.page ? "p. " + s.page : "KG"}</span>`;
    st.refs.append(b);
  });
}

// Guido's traversal is computed in milliseconds; the timeline replays it at a pace the audience can follow.
// Every narrated step is derived from the real graph events (nodes, relations, recommendations) of this answer.
const MIN_TRAVERSAL_MS = 7000;
function makeTimeline(st, opts = {}) {
  const q = []; let running = false, finished = null, ended = false; const t0 = performance.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const lab = id => (KG.byId.get(id) || {}).label || '';
  const names = (ns, k = 3) => ns.slice(0, k).map(n => `<span class="q">${esc(n.label.length > 32 ? n.label.slice(0, 31) + '…' : n.label)}</span>`).join(', ') + (ns.length > k ? ` +${ns.length - k}` : '');
  function narrate(ev) {
    const concepts = ev.nodes.filter(n => n.kind === 'concept'), recs = ev.nodes.filter(n => n.kind === 'rec');
    opts.say?.(ev.action, ev, concepts, recs);
    if (ev.action === 'seed') return step(st, 'search', `Matching the question to graph concepts: ${names(ev.nodes, 4)}`, true);
    if (ev.action === 'hop1') {
      const rels = [...new Set(ev.links.map(l => l.role).filter(r => !['target', 'population', 'condition', 'parameter', 'mention'].includes(r)))];
      return step(st, 'search', `Hop 1 from <span class="q">${esc(lab(ev.focus))}</span>: ${recs.length} recommendations${rels.length ? ` · relations ${rels.slice(0, 3).map(r => `<span class="q">${esc(r)}</span>`).join(', ')}` : ''}`, true);
    }
    if (ev.action === 'hop2') return step(st, 'search', `Hop 2: following recommendations to ${names(concepts)}`, true);
    if (ev.action === 'expanded') return step(st, 'search', `Expanding <span class="q">${esc(lab(ev.focus))}</span> one hop further`, true);
  }
  function chunks(ev) {   // reveal large hops in small waves so nodes visibly grow out of their parent
    if (ev.action === 'done' || ev.nodes.length <= 4) return [ev];
    const out = [];
    for (let i = 0; i < ev.nodes.length; i += 3) {
      const ids = new Set(ev.nodes.slice(i, i + 3).map(n => n.id));
      out.push({...ev, nodes: ev.nodes.slice(i, i + 3), links: ev.links.filter(l => ids.has(l.source) || ids.has(l.target)), sub: i > 0, full: ev});
    }
    return out;
  }
  async function run() {
    running = true;
    while (q.length) {
      const it = q.shift();
      if (it.kind === 'graph') {
        if (it.ev.action === 'done') { kgUpdate(it.ev); continue; }
        if (!it.ev.sub) narrate(it.ev.full || it.ev);
        kgUpdate(it.ev);
        await sleep(it.ev.action === 'seed' ? 1500 : it.ev.sub ? 650 : 1100);
      } else if (it.kind === 'text') {
        const wait = (opts.min || MIN_TRAVERSAL_MS) - (performance.now() - t0);
        if (wait > 0) { opts.say?.('select'); step(st, 'check', 'Selecting the recommendations that answer the question', true); await sleep(wait); }
        if (opts.silentText) { st.raw += it.delta; continue; }
        settle(st);
        for (let i = 0; i < it.delta.length; i += 18) {   // stream the held-back answer with the blur effect
          st.raw += it.delta.slice(i, i + 18); if (!st.frame) st.frame = requestAnimationFrame(() => render(st));
          await sleep(22);
        }
      }
    }
    running = false;
    if (ended && finished) finished();
  }
  return {
    push(it) { if (it.kind === 'graph' && it.ev.action !== 'done') chunks(it.ev).forEach(c => q.push({kind: 'graph', ev: c})); else q.push(it); if (!running) run(); },
    end() { ended = true; return new Promise(r => { finished = r; if (!running && !q.length) r(); }); }
  };
}

// Guido in the chat: the strongest cited recommendation in its original guideline table + live context of the start concept
// start concepts ranked for the fact cards: named in the question first, then on the answer path
function keySeeds(question, k = 2) {
  const q = (question || '').toLowerCase();
  return KG.nodes.filter(n => n.kind === 'concept' && n.hop === 0)
    .map(n => ({n, s: (q.includes(n.label.toLowerCase()) ? 2 : 0) + (KG.pathIds.has(n.id) ? 1 : 0)}))
    .sort((a, b) => b.s - a.s).slice(0, k).map(x => x.n);
}
function chatEvidence(st) {
  const cited = [...KG.cited], seed = keySeeds((KG.byId.get('q') || {}).label, 1)[0];
  if (!cited.length && !seed) return;
  const box = document.createElement('div'); box.className = 'chat-ev'; st.el.querySelector('.refs').after(box);
  if (cited.length) fetch('/api/kg/evidence?id=' + encodeURIComponent(cited[0])).then(r => r.json()).then(x => {
    if (!x || !x.text) return;
    const grade = [x.class && x.class.length ? 'Class ' + x.class.join('/') : '', x.loe && x.loe.length ? 'LoE ' + x.loe.join('/') : ''].filter(Boolean).join(' · ');
    const host = document.createElement('div'); host.className = 'jr-host'; box.prepend(host);
    jrMount(host).set({root: 'e', elements: {e: {type: 'Excerpt', props: {title: x.title || x.ref, ref: x.ref, page: x.page, grade, html: x.table_html, match: x.match, text: x.text, pageId: x.page_id}, children: []}}});
  }).catch(() => {});
  if (seed) getFacts(seed.id).then(f => { if (!f || !(f.wikidata || f.umls)) return; const host = document.createElement('div'); host.className = 'jr-host'; box.append(host); jrMount(host).set(conceptSpec(f)); });
}
async function sendChat(text) {
  text = text.trim(); if (!text || chatBusy) return;
  chatBusy = true; $('#send').disabled = true; $('#msg').value = ''; status('');
  const a = agent, chatHistory = a.history;
  if (a === AGENTS.guido) kgReset(text);
  const story = attached && a === AGENTS.lino;
  const u = document.createElement('div'); u.className = 'msg-user'; u.textContent = text; $(a.thread).append(u);
  if (story) { u.insertAdjacentHTML('afterbegin', '<span class="u-att">Storyboard prompt</span>'); setAttach(false); }
  chatHistory.push({role: 'user', text: story ? STORYBOARD_PROMPT + '\n\n' + text : text});
  const st = addAssistant();
  const tl = a === AGENTS.guido ? makeTimeline(st) : null;
  try {
    const r = await fetch(a.endpoint, {method: 'POST', headers: HEADERS, body: JSON.stringify({messages: chatHistory})});
    if (r.status === 401) { showLogin(); throw Error('Please sign in again.'); }
    if (!r.ok || !r.body) throw Error('Chat is unavailable right now.');
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === 'tool_call' && ev.name === 'search_guidelines') step(st, 'search', `Searching guidelines for <span class="q">“${esc(ev.args.query || '')}”</span>`, true);
        else if (ev.type === 'sources') { ev.items.forEach(s => st.sources[s.n] = s); if (st.pending && !tl) st.pending.querySelector('.label').insertAdjacentHTML('beforeend', ` · ${ev.items.length} pages`); }
        else if (ev.type === 'tool_call' && ev.name === 'read_page') step(st, 'read', 'Reading the full page', true);
        else if (ev.type === 'read') { const s = ev.item; if (st.pending) st.pending.querySelector('.label').innerHTML = `Reading <span class="q">${esc(s.ref || s.title)}</span>, page ${s.page}`; }
        else if (ev.type === 'text' && tl) tl.push({kind: 'text', delta: ev.delta});
        else if (ev.type === 'text') {
          settle(st);
          st.raw += ev.delta; if (!st.frame) st.frame = requestAnimationFrame(() => render(st));
        }
        else if (ev.type === 'tool_call' && (ev.name === 'explore' || ev.name === 'expand') && tl) { /* narrated by the timeline */ }
        else if (ev.type === 'graph' && tl) tl.push({kind: 'graph', ev});
        else if (ev.type === 'graph') kgUpdate(ev);
        else if (ev.type === 'error') throw Error(ev.message);
      }
    }
    if (tl) await tl.end();
    render(st, true); finishRefs(st);
    if (a === AGENTS.lino) renderStoryboard(st);
    if (a === AGENTS.guido) chatEvidence(st);
    chatHistory.push({role: 'assistant', text: st.raw});
    $(a.preview).textContent = st.raw.replace(/\[\d+\]|[*#_]/g, '').slice(0, 80);
  } catch (e) {
    st.answer.innerHTML = `<span class="err">${esc(e.message)}</span>`;
    chatHistory.pop();
  } finally {
    chatBusy = false; $('#send').disabled = false; $('#msg').focus();
    // Freeze finished messages: unwrap blur spans and stop entry animations, so switching chats
    // (display:none -> block) does not replay them.
    setTimeout(() => {
      st.el.querySelectorAll('.blur-in').forEach(sp => sp.replaceWith(...sp.childNodes));
      [u, st.el, ...st.el.querySelectorAll('*')].forEach(x => { x.style.animation = 'none'; });
    }, 1200);
  }
}
$('#send').onclick = () => sendChat($('#msg').value);
$('#msg').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat($('#msg').value); } });
$('#clear-chat').onclick = () => { if (chatBusy) return; agent.history.length = 0; $(agent.thread).innerHTML = ''; if (agent === AGENTS.guido) kgReset(); $('#msg').focus(); };

/* ---------------- full-screen video call ---------------- */
let call = null, convId = null, preview = null, tick = null, endTimer = null, micOn = true, camOn = true, audioCtx = null, meterRaf = 0;
const cx = $('#callx');
function setClip(rect) {
  const W = innerWidth, H = innerHeight;
  if (!rect) { ['--ct', '--cr', '--cb', '--cl'].forEach(v => cx.style.setProperty(v, '0px')); cx.style.setProperty('--cradius', '0px'); return; }
  cx.style.setProperty('--ct', rect.top + 'px'); cx.style.setProperty('--cl', rect.left + 'px');
  cx.style.setProperty('--cr', (W - rect.right) + 'px'); cx.style.setProperty('--cb', (H - rect.bottom) + 'px');
  cx.style.setProperty('--cradius', '12px');
}
function openOverlay(from) {
  cx.classList.remove('hidden'); cx.style.transition = 'none'; setClip(from.getBoundingClientRect());
  cx.getBoundingClientRect();
  cx.style.transition = ''; requestAnimationFrame(() => { cx.classList.remove('closed'); setClip(null); });
}
function closeOverlay() {
  cx.classList.add('closed'); setClip($('#start').getBoundingClientRect());
  setTimeout(() => { cx.classList.add('hidden'); resetCallUI(); }, 680);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
function resetCallUI() {
  $('#prejoin').classList.remove('hidden'); ['#dock', '#pip', '#cx-wait'].forEach(s => $(s).classList.add('hidden'));
  $('#remote').classList.remove('on'); $('#remote').srcObject = null; $('#remote-audio').srcObject = null;
  $('#cx-chat').classList.remove('open'); cx.classList.remove('chat-open'); restoreChat();
  $('#pj-err').textContent = ''; $('#pj-join').disabled = false;
}

async function listDevices() {
  const devs = await navigator.mediaDevices.enumerateDevices();
  const fill = (sel, kind, current) => {
    const el = $(sel); el.innerHTML = '';
    devs.filter(d => d.kind === kind).forEach((d, i) => { const o = new Option(d.label || `${kind} ${i + 1}`, d.deviceId); if (d.deviceId === current) o.selected = true; el.add(o); });
    el.disabled = !el.options.length;
  };
  const vt = preview?.getVideoTracks()[0]?.getSettings().deviceId, at = preview?.getAudioTracks()[0]?.getSettings().deviceId;
  fill('#sel-cam', 'videoinput', vt); fill('#sel-mic', 'audioinput', at); fill('#sel-spk', 'audiooutput');
  if (!('setSinkId' in HTMLMediaElement.prototype)) $('#sel-spk').disabled = true;
}
async function startPreview() {
  stopPreview();
  const cam = $('#sel-cam').value, mic = $('#sel-mic').value;
  try {
    preview = await navigator.mediaDevices.getUserMedia({
      video: cam ? {deviceId: {exact: cam}, width: 1280, height: 720} : {width: 1280, height: 720},
      audio: mic ? {deviceId: {exact: mic}, echoCancellation: true, noiseSuppression: true} : {echoCancellation: true, noiseSuppression: true}});
  } catch (e) {
    $('#pj-err').textContent = 'Camera or microphone blocked. Allow access in the browser bar, then try again.';
    preview = null; return;
  }
  preview.getVideoTracks().forEach(t => t.enabled = camOn); preview.getAudioTracks().forEach(t => t.enabled = micOn);
  $('#preview').srcObject = preview; $('#preview-off').classList.toggle('hidden', camOn);
  await listDevices(); startMeter();
}
function stopPreview() { cancelAnimationFrame(meterRaf); preview?.getTracks().forEach(t => t.stop()); preview = null; }
function startMeter() {
  try {
    audioCtx = audioCtx || new AudioContext();
    const src = audioCtx.createMediaStreamSource(preview); const an = audioCtx.createAnalyser(); an.fftSize = 512; src.connect(an);
    const data = new Uint8Array(an.fftSize);
    const loop = () => { an.getByteTimeDomainData(data); let m = 0; for (const v of data) m = Math.max(m, Math.abs(v - 128)); $('#meter').style.width = Math.min(100, m * 1.6) + '%'; meterRaf = requestAnimationFrame(loop); };
    loop();
  } catch {}
}
function syncToggles() {
  $('#pj-mic').setAttribute('aria-pressed', !micOn); $('#pj-cam').setAttribute('aria-pressed', !camOn);
  $('#d-mic').setAttribute('aria-pressed', !micOn); $('#d-cam').setAttribute('aria-pressed', !camOn);
  $('#d-mic .lbl').textContent = micOn ? 'Mute' : 'Unmute'; $('#d-cam .lbl').textContent = camOn ? 'Stop Video' : 'Start Video';
  $('#preview-off').classList.toggle('hidden', camOn); $('#local-off').classList.toggle('hidden', camOn); $('#pip-muted').classList.toggle('hidden', micOn);
  preview?.getVideoTracks().forEach(t => t.enabled = camOn); preview?.getAudioTracks().forEach(t => t.enabled = micOn);
  if (call) { call.setLocalAudio(micOn); call.setLocalVideo(camOn); }
}
$('#pj-mic').onclick = $('#d-mic').onclick = () => { micOn = !micOn; syncToggles(); };
$('#pj-cam').onclick = $('#d-cam').onclick = () => { camOn = !camOn; syncToggles(); };
$('#sel-cam').onchange = $('#sel-mic').onchange = () => startPreview();
$('#sel-spk').onchange = () => { const id = $('#sel-spk').value; ['#remote', '#remote-audio'].forEach(s => $(s).setSinkId?.(id).catch(() => {})); };

let callAgent = 'luna';
function openCall(from) {
  if (convId) return;
  callAgent = agent === AGENTS.lino ? 'lino' : agent === AGENTS.guido ? 'guido' : 'luna';
  cx.style.setProperty('--face', `url(/static/img/${callAgent}.jpg)`);
  $('#cx-wait-orb').classList.toggle('lino', callAgent === 'lino'); $('#cx-wait-orb').classList.toggle('guido', callAgent === 'guido');
  $('#cx-wait-text').textContent = 'Connecting to ' + agent.name + '…';
  openOverlay(from || $('#start')); syncToggles(); startPreview();
}
$('#start').onclick = () => openCall($('#start'));
function openPicker() {
  const list = $('#picker-list'); list.innerHTML = '';
  Object.entries(AGENTS).forEach(([id, a], i) => {
    const st = id === 'luna' ? 'online' : gpuState.status;
    const b = document.createElement('button'); b.className = 'pick'; b.style.animationDelay = (60 + i * 50) + 'ms';
    b.innerHTML = `<div class="orb ${a.orb}"><span class="presence ${st === 'online' ? '' : st === 'waking' ? 'wake' : 'off'}"></span></div>`
      + `<div class="meta"><b>${a.name}</b><small>${a.tagline}</small></div><span class="st ${st}">${st === 'online' ? 'Online' : st === 'waking' ? 'Waking' : 'Asleep'}</span>`;
    b.onclick = () => { closePicker(); switchAgent(id); if (!chatBusy) { a.history.length = 0; $(a.thread).innerHTML = ''; } };
    list.append(b);
  });
  $('#picker').classList.remove('hidden'); list.querySelector('.pick')?.focus();
}
function closePicker() { $('#picker').classList.add('hidden'); }
$('#new-chat').onclick = openPicker;
$('#picker-close').onclick = closePicker;
$('#picker').addEventListener('click', e => { if (e.target.id === 'picker') closePicker(); });
addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#picker').classList.contains('hidden')) closePicker(); });
$('#pj-cancel').onclick = () => { stopPreview(); closeOverlay(); };

function attachTracks() {
  if (!call) return;
  const ps = call.participants();
  const local = ps.local;
  const lv = local?.tracks?.video?.persistentTrack;
  if (lv && $('#local').srcObject?.getVideoTracks()[0] !== lv) $('#local').srcObject = new MediaStream([lv]);
  const remote = Object.values(ps).find(p => !p.local);
  if (!remote) return;
  const v = remote.tracks?.video, a = remote.tracks?.audio;
  if (v?.persistentTrack && v.state === 'playable' && $('#remote').srcObject?.getVideoTracks()[0] !== v.persistentTrack) {
    $('#remote').srcObject = new MediaStream([v.persistentTrack]); $('#remote').classList.add('on'); $('#cx-wait').classList.add('hidden');
  }
  if (a?.persistentTrack && a.state === 'playable' && $('#remote-audio').srcObject?.getAudioTracks()[0] !== a.persistentTrack) {
    $('#remote-audio').srcObject = new MediaStream([a.persistentTrack]); $('#remote-audio').play().catch(() => {});
  }
}
$('#pj-join').onclick = async () => {
  $('#pj-join').disabled = true; $('#pj-err').textContent = '';
  try {
    const x = await post('/api/call/start', {agent: callAgent});
    const url = new URL(x.url);
    if (url.protocol !== 'https:' || !url.hostname.endsWith('.daily.co')) throw Error('Unexpected meeting provider URL');
    const token = url.searchParams.get('t') || undefined; url.search = '';
    convId = x.conversation_id;
    const vt = preview?.getVideoTracks()[0], at = preview?.getAudioTracks()[0];
    cancelAnimationFrame(meterRaf);
    call = window.Daily.createCallObject({videoSource: vt || false, audioSource: at || false, subscribeToTracksAutomatically: true});
    ['participant-joined', 'participant-updated', 'track-started', 'joined-meeting'].forEach(e => call.on(e, attachTracks));
    call.on('participant-left', e => { if (!e.participant.local) leaveCall(AGENTS[callAgent].name + ' left the call.'); });
    call.on('error', e => leaveCall(e?.errorMsg || 'The call ended unexpectedly.'));
    call.on('app-message', onAppMessage);
    $('#prejoin').classList.add('hidden'); ['#dock', '#pip', '#cx-wait'].forEach(s => $(s).classList.remove('hidden')); stageReset();
    $('#preview').srcObject = null;
    await call.join({url: url.toString(), token, startVideoOff: !camOn, startAudioOff: !micOn});
    attachTracks();
    endTimer = setTimeout(() => leaveCall('The call time limit was reached.'), x.max_seconds * 1000);
    if (callAgent !== 'luna') { post('/api/gpu/touch').catch(() => {}); tick = setInterval(() => post('/api/gpu/touch').catch(() => {}), 60000); }
  } catch (e) {
    $('#pj-err').textContent = e.message || 'Could not start the call.'; $('#pj-join').disabled = false;
    $('#prejoin').classList.remove('hidden'); ['#dock', '#pip', '#cx-wait'].forEach(s => $(s).classList.add('hidden'));
    if (call) { call.destroy().catch(() => {}); call = null; }
    if (convId) { post('/api/call/end', {conversation_id: convId}).catch(() => {}); convId = null; }
    if (preview) $('#preview').srcObject = preview;
  }
};
// Keep the shared GPU awake while the page is in use, whichever agent the visitor talks to:
// any interaction in the last 15 minutes with the tab visible sends a heartbeat (the server keeps it 10 more minutes).
let lastActive = Date.now();
['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(t => addEventListener(t, () => { lastActive = Date.now(); }, {passive: true, capture: true}));
setInterval(() => {
  if (gpuState.status === 'online' && document.visibilityState === 'visible'
      && Date.now() - lastActive < 15 * 60000 && !$('#app').classList.contains('hidden')) post('/api/gpu/touch').catch(() => {});
}, 60000);
/* ---------- Guido's screen share: window manager driven by the clef-flash director ---------- */
const WM = {on: false, wins: new Map(), order: [], layout: 'focus', dirBusy: false, lastDir: 0, concept: null};
function shareOn() {
  if (WM.on) return; WM.on = true; cx.classList.add('share-on'); stageShow(false);
  const tick = () => { if (!WM.on) return; $('#share-clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); setTimeout(tick, 15000); }; tick();
}
function shareOff() {
  WM.on = false; cx.classList.remove('share-on');
  const cv = $('#kg-canvas'); if (cv.parentNode.id !== 'kg-panel') $('#kg-panel').insertBefore(cv, $('#kg-empty'));
  WM.wins.forEach(w => w.el.remove()); WM.wins.clear(); WM.order = []; WM.concept = null; $('#share-dir-t').textContent = 'director ready';
}
function winOpen(id, title, cls, background) {
  let w = WM.wins.get(id);
  if (!w) {
    const el = document.createElement('div'); el.className = 'win opening ' + (cls || id);
    el.innerHTML = `<header><span class="lights"><i></i><i></i><i></i></span><b></b></header><div class="wbody"></div>`;
    $('#desk-area').append(el); w = {el, body: el.querySelector('.wbody')}; WM.wins.set(id, w);
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove('opening')));
    el.addEventListener('pointerdown', () => winFront(id));
  }
  if (title) w.el.querySelector('header b').textContent = title;
  w.el.classList.remove('min');
  if (background && WM.order.length) { WM.order = [...WM.order.filter(x => x !== id), id]; winFront(WM.order[0]); } else winFront(id);
  return w;
}
function winFront(id) {
  if (!WM.wins.has(id)) return;
  WM.order = [id, ...WM.order.filter(x => x !== id)];
  WM.wins.forEach((w, k) => { w.el.classList.toggle('front', k === id); w.el.style.zIndex = 10 + (WM.order.length - WM.order.indexOf(k)); });
  winLayout();
}
// rectangles in % of the desk area for the three layouts the director can choose
function winLayout(layout) {
  if (layout) WM.layout = layout;
  const ids = WM.order.filter(id => !WM.wins.get(id).el.classList.contains('min')), L = WM.layout;
  const R = (w, x, y, ww, hh) => Object.assign(w.el.style, {left: x + '%', top: y + '%', width: ww + '%', height: hh + '%'});
  ids.forEach((id, i) => {
    const w = WM.wins.get(id);
    if (L === 'grid' || ids.length === 1) {
      if (ids.length === 1) return R(w, 2, 0, 96, 100);
      const cols = 2, rows = Math.ceil(ids.length / 2), c = i % cols, r = Math.floor(i / cols);
      return R(w, c * 50 + 0.6, r * (100 / rows), 48.8, 100 / rows - 1.6);
    }
    if (L === 'split') {
      if (i === 0) return R(w, 0, 0, 57, 100);
      if (i === 1) return R(w, 58.5, 0, 41.5, ids.length > 2 ? 64 : 100);
      return R(w, 58.5 + (i - 2) * 21, 66, 20.2, 34);
    }
    if (i === 0) return R(w, 0, 0, 69, 100);           // focus: one large window, the rest stacked on the right
    const n = ids.length - 1, h = 100 / n;
    return R(w, 70.5, (i - 1) * h, 29.5, h - (i < n ? 1.8 : 0));
  });
}
// ask clef-flash what the shared screen should show; apply only confident decisions
async function direct(phase, utterance) {
  if (!WM.on || WM.dirBusy) return; WM.dirBusy = true;
  const concepts = KG.nodes.filter(n => n.kind === 'concept' && (n.hop === 0 || KG.pathIds.has(n.id))).map(n => n.label).slice(0, 12);
  const state = {phase, speaker: 'Guido', utterance: (utterance || '').slice(0, 900), question: (KG.byId.get('q') || {}).label,
    open_windows: [...WM.wins.keys()], front_window: WM.order[0], concepts,
    recommendations: [...KG.cited].map(id => (KG.byId.get(id) || {}).label).filter(Boolean).slice(0, 5)};
  try {
    const d = await post('/api/director', {state}); const a = d.answers || {};
    if (!a.front) throw 0;
    const pf = a.front.probabilities[a.front.choice], parts = [];
    if (a.show_table && a.show_table.noul > 0.55 && WM.wins.has('table') && WM.wins.get('table').el.classList.contains('min')) winOpen('table');
    if (a.show_inspector && a.show_inspector.noul > 0.6 && d.concept_label) {
      const n = KG.nodes.find(x => x.kind === 'concept' && x.label === d.concept_label);
      if (n && n.id !== WM.concept) { inspect(n.id); parts.push('inspect ' + d.concept_label); }
    }
    if (pf >= 0.38 && WM.wins.has(a.front.choice)) winFront(a.front.choice);
    if (a.layout && a.layout.probabilities[a.layout.choice] >= 0.42) winLayout(a.layout.choice);
    $('#share-dir-t').innerHTML = `front <b>${a.front.choice}</b> ${Math.round(pf * 100)}% · layout <b>${a.layout ? a.layout.choice : WM.layout}</b>`
      + ` · table ${Math.round((a.show_table || {}).noul * 100 || 0)}% · inspector ${Math.round((a.show_inspector || {}).noul * 100 || 0)}%` + (parts.length ? ' · ' + parts.join(', ') : '');
  } catch { $('#share-dir-t').textContent = 'director unavailable, default layout'; }
  finally { WM.dirBusy = false; WM.lastDir = performance.now(); }
}
function inspect(id) {
  WM.concept = id;
  getFacts(id).then(f => {
    if (!f || !(f.wikidata || f.umls) || !WM.on) return;
    const w = winOpen('inspector', 'Inspector — ' + ((f.wikidata && f.wikidata.label) || f.label) + ' · UMLS · Wikidata', 'inspector', true);
    w.body.innerHTML = ''; const host = document.createElement('div'); host.className = 'jr-host'; w.body.append(host); jrMount(host).set(conceptSpec(f));
  });
}
function showTable(x) {
  const w = winOpen('table', `Guideline — ${x.ref} · page ${x.page}`);
  const grade = [x.class && x.class.length ? 'Class ' + x.class.join('/') : '', x.loe && x.loe.length ? 'LoE ' + x.loe.join('/') : ''].filter(Boolean).join(' · ');
  w.body.innerHTML = `<div class="tabs"><button class="on" data-t="t">Table</button>${x.page_id ? '<button data-t="p">Page</button>' : ''}</div><div class="tview"></div>`;
  const view = w.body.querySelector('.tview');
  const showT = () => { view.innerHTML = ''; const host = document.createElement('div'); host.className = 'jr-host'; view.append(host);
    jrMount(host).set({root: 'e', elements: {e: {type: 'Excerpt', props: {title: x.title || x.ref, ref: x.ref, page: x.page, grade, html: x.table_html, match: x.match, text: x.text, pageId: x.page_id}, children: []}}}); };
  const showP = () => { view.innerHTML = `<img class="pageimg" alt="Guideline page" src="/api/page-image/${encodeURIComponent(x.page_id)}">`; };
  w.body.querySelectorAll('.tabs button').forEach(b => b.onclick = () => { w.body.querySelectorAll('.tabs button').forEach(z => z.classList.toggle('on', z === b)); b.dataset.t === 'p' ? showP() : showT(); });
  showT();
}
/* ---------- live stage next to the video: graph traversal, evidence, live json-render boards ---------- */
const stage = {said: [], lastBoard: '', greeted: false};
// speaking state of the face, from Tavus started/stopped_speaking events: narration lines are only sent into silence
const face = {speaking: false, stoppedAt: 0, sentAt: 0};
const faceIdle = (ms = 9000) => new Promise(r => { const t0 = performance.now(); const t = setInterval(() => {
  const quiet = !face.speaking && performance.now() - face.stoppedAt > 350 && performance.now() - face.sentAt > 1500;
  if (quiet || performance.now() - t0 > ms) { clearInterval(t); r(); } }, 120); });
const normSpeech = t => (t || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
function stageShow(on) { cx.classList.toggle('stage-on', on); }
function stageBusy(on, sub) { $('#stage-dot').classList.toggle('busy', on); if (sub != null) $('#stage-sub').textContent = sub; }
function stageReset() {
  stage.said = []; stage.lastBoard = ''; stage.greeted = false; face.speaking = false;
  $('#stage-cards').innerHTML = `<p class="stage-empty">${callAgent === 'guido' ? 'Ask Guido a question: the graph walk, the guideline table and live context from UMLS and Wikidata appear here.' : 'What ' + (callAgent === 'lino' ? 'Lino' : 'Luna') + ' says is turned into live cards here.'}</p>`;
  $('#stage-title').textContent = callAgent === 'guido' ? 'Guideline knowledge graph' : 'Live board'; $('#stage-sub').textContent = '';
  stageBusy(false);
  stageShow(false);
}
function stageCard(spec, top) {
  $('#stage-cards .stage-empty')?.remove();
  const host = document.createElement('div'); host.className = 'jr-host';
  top ? $('#stage-cards').prepend(host) : $('#stage-cards').append(host);
  const r = jrMount(host); if (spec) r.set(spec);
  while ($('#stage-cards').children.length > 6) $('#stage-cards').lastElementChild.remove();
  return r;
}
$('#stage-close').onclick = () => stageShow(false);
// speak exact text through the face (Tavus conversation.echo: verbatim TTS, bypasses the LLM)
function echo(text) {
  if (!call || !convId || !text) return;
  stage.said.push(normSpeech(text).slice(0, 60)); face.sentAt = performance.now();
  call.sendAppMessage({message_type: 'conversation', event_type: 'conversation.echo', conversation_id: convId, properties: {modality: 'text', text}}, '*');
}
function toolResult(m, p, output, ok) {
  call?.sendAppMessage({message_type: 'conversation', event_type: 'conversation.tool_result', conversation_id: m.conversation_id || convId,
    properties: {tool_call_id: p.tool_call_id, output: String(output).slice(0, 3000), status: ok ? 'success' : 'error'}}, '*');
}
// cut long text at a sentence end so a single echo stays below Tavus' 4 KB app-message limit
function speakable(t, max = 2600) {
  t = String(t || '').replace(/```[\s\S]*?```/g, ' ').replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, '').replace(/[*_#>`|]/g, '').replace(/^\s*[-•]\s*/gm, '').replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max), i = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('? '));
  return i > max * 0.5 ? cut.slice(0, i + 1) : cut;
}
// live board: the llm restructures what was said into a json-render spec, streamed as SpecStream patches
async function liveBoard(text, who) {
  const key = normSpeech(text).slice(0, 80);
  if (!key || key === stage.lastBoard || text.length < 70) return; stage.lastBoard = key;
  let r;
  if (WM.on) { const w = winOpen('notes', 'Notes — what Guido said', 'notes', true); const host = document.createElement('div'); host.className = 'jr-host'; w.body.prepend(host); r = jrMount(host); }  // notes open behind; the director decides the front
  else { if (!cx.classList.contains('stage-on')) stageShow(true); r = stageCard(null, true); }
  const comp = jrCompiler();
  try {
    const res = await fetch('/api/board', {method: 'POST', headers: HEADERS, body: JSON.stringify({text, agent: who})});
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true}); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === 'delta') { const {result, newPatches} = comp.push(ev.text); if (newPatches.length && result.root) r.set(result, true); }
      }
    }
    const fin = comp.push('\n').result; if (fin.root) r.set(fin, false);
  } catch {}
}
// guido narrates in the language of the question. en, de, ru and hi, like the agents themselves
function langOf(text) {
  const t = (text || '').toLowerCase();
  if (/[а-яё]/.test(t)) return 'ru';
  if (/[ऀ-ॿ]/.test(t)) return 'hi';
  return /[äöüß]|\b(der|die|das|und|ist|nicht|was|wie|welche|bei|mit|für|sind|wird|kann|gibt)\b/.test(t) ? 'de' : 'en';
}
const ruPlural = (n, one, few, many) => n % 10 === 1 && n % 100 !== 11 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many;
const SAY = {
  en: {and: 'and', share: 'Let me share my screen and walk you through our knowledge graph.', again: 'Let me look at the graph again.',
    seed: x => `I'm starting from ${x}.`, recs: (n, l) => `Here's something: ${n} recommendation${n > 1 ? 's' : ''} around ${l}. Let me follow them.`,
    links: l => `Let me follow the links of ${l}.`, hop2: x => `Two steps out now, I'm at ${x}.`, expand: l => `Let me expand ${l} a bit further.`,
    select: 'I found what I need. Let me pick the recommendations that answer your question.', table: 'Look, here is the recommendation in the original guideline table.'},
  de: {and: 'und', share: 'Ich teile kurz meinen Bildschirm und gehe mit Ihnen durch unseren Wissensgraphen.', again: 'Ich schaue noch einmal in den Graphen.',
    seed: x => `Ich starte bei ${x}.`, recs: (n, l) => `Hier ist etwas: ${n} ${n > 1 ? 'Empfehlungen' : 'Empfehlung'} rund um ${l}. Denen folge ich jetzt.`,
    links: l => `Ich folge den Verbindungen von ${l}.`, hop2: x => `Zwei Schritte weiter bin ich jetzt bei ${x}.`, expand: l => `Ich schaue mir ${l} noch genauer an.`,
    select: 'Ich habe, was ich brauche. Jetzt wähle ich die Empfehlungen aus, die Ihre Frage beantworten.', table: 'Schauen Sie, hier ist die Empfehlung in der originalen Leitlinientabelle.'},
  ru: {and: 'и', share: 'Сейчас я покажу свой экран и проведу вас по нашему графу знаний.', again: 'Давайте ещё раз посмотрим на граф.',
    seed: x => `Начинаю с ${x}.`, recs: (n, l) => `Вот что я нашёл: ${n} ${ruPlural(n, 'рекомендация', 'рекомендации', 'рекомендаций')} вокруг ${l}. Иду по ним дальше.`,
    links: l => `Иду по связям ${l}.`, hop2: x => `Два шага дальше, я сейчас у ${x}.`, expand: l => `Посмотрю на ${l} подробнее.`,
    select: 'Я нашёл то, что нужно. Сейчас выберу рекомендации, которые отвечают на ваш вопрос.', table: 'Смотрите, вот эта рекомендация в оригинальной таблице руководства.'},
  hi: {and: 'और', share: 'मैं अपनी स्क्रीन शेयर करता हूँ और आपको हमारे नॉलेज ग्राफ़ से होकर ले चलता हूँ।', again: 'चलिए, ग्राफ़ को एक बार फिर देखते हैं।',
    seed: x => `मैं ${x} से शुरू कर रहा हूँ।`, recs: (n, l) => `यह मिला: ${l} से जुड़ी ${n} ${n > 1 ? 'सिफ़ारिशें' : 'सिफ़ारिश'}। मैं इन्हें आगे देखता हूँ।`,
    links: l => `मैं ${l} के कनेक्शन देख रहा हूँ।`, hop2: x => `अब दो कदम आगे, मैं ${x} पर हूँ।`, expand: l => `मैं ${l} को थोड़ा और विस्तार से देखता हूँ।`,
    select: 'मुझे जो चाहिए था, वह मिल गया। अब मैं वे सिफ़ारिशें चुनता हूँ जो आपके सवाल का जवाब देती हैं।', table: 'देखिए, यह रही वह सिफ़ारिश मूल गाइडलाइन टेबल में।'},
};
// Guido's narration while he walks the graph (spoken via echo, spaced so lines never overlap)
function narrator(lang = 'en') {
  const T = SAY[lang] || SAY.en;
  let last = 0, n = 0; const q = [], seen = new Set();
  const short = (l, w = 4) => { const p = String(l).replace(/\s*\(.*?\)/g, '').split(/\s+/); return p.slice(0, w).join(' ') + (p.length > w ? '…' : ''); };
  const list = ls => ls.length > 1 ? ls.slice(0, -1).join(', ') + ` ${T.and} ` + ls[ls.length - 1] : ls[0] || '';
  async function pump() {
    while (q.length) {
      await faceIdle();
      const t = q.shift(); if (t) { echo(t); last = performance.now(); }
    }
  }
  const say = (t, force) => { if (!force && (n >= 5 || q.length > 1)) return; n++; q.push(t); if (q.length === 1) pump(); };
  return {
    say,
    on(kind, ev, concepts, recs) {
      if (kind === 'seed' && ev.nodes.length) say(T.seed(list(ev.nodes.slice(0, 2).map(x => short(x.label)))));
      else if (kind === 'hop1' && !seen.has('hop1-' + ev.focus) && seen.size < 2) { seen.add('hop1-' + ev.focus); const l = short((KG.byId.get(ev.focus) || {}).label || 'this concept');
        say(recs.length ? T.recs(recs.length, l) : T.links(l)); }
      else if (kind === 'hop2' && !seen.has('hop2')) { seen.add('hop2'); if (concepts.length) say(T.hop2(list(concepts.slice(0, 2).map(x => short(x.label, 3))))); }
      else if (kind === 'expanded' && !seen.has('exp')) { seen.add('exp'); say(T.expand(short((KG.byId.get(ev.focus) || {}).label || '…'))); }
      else if (kind === 'select' && !seen.has('sel')) { seen.add('sel'); say(T.select); }
    },
    done: () => new Promise(r => { const t = setInterval(() => { if (!q.length) { clearInterval(t); r(); } }, 200); })
  };
}
async function guidoCall(m, p) {
  let args = p.arguments; try { if (typeof args === 'string') args = JSON.parse(args); } catch { args = {}; }
  const question = (args && args.question) || '';
  const firstShare = !WM.on; shareOn();
  const gw = winOpen('graph', 'Knowledge Graph — ' + question, 'graph');
  if (!gw.body.querySelector('.kg-canvas')) { gw.body.append($('#kg-canvas')); const ws = document.createElement('div'); ws.className = 'wsteps'; gw.body.append(ws); }
  gw.body.querySelector('.wsteps').innerHTML = '';
  ['table', 'inspector'].forEach(id => WM.wins.get(id)?.el.classList.add('min')); winLayout('focus');
  kgReset(question);
  const lang = langOf(question), nar = narrator(lang); nar.say(firstShare ? SAY[lang].share : SAY[lang].again, true);
  const st = {el: document.createElement('div'), steps: gw.body.querySelector('.wsteps'), answer: document.createElement('div'), refs: document.createElement('div'), raw: '', shown: 0, sources: {}, pending: null, frame: 0};
  const tl = makeTimeline(st, {min: 9000, silentText: true, say: (k, ev, c, r) => { nar.on(k, ev, c, r); if (k === 'hop1' && performance.now() - WM.lastDir > 4000) direct('searching', 'Walking the knowledge graph from ' + ((KG.byId.get(ev.focus) || {}).label || '')); }});
  let ok = true;
  try {
    const r = await fetch('/api/chat/guido', {method: 'POST', headers: HEADERS, body: JSON.stringify({messages: [{role: 'user', text: question}], via: 'call'})});
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true}); let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.type === 'graph') tl.push({kind: 'graph', ev});
        else if (ev.type === 'text') tl.push({kind: 'text', delta: ev.delta});
        else if (ev.type === 'sources') ev.items.forEach(s => st.sources[s.n] = s);
        else if (ev.type === 'error') throw Error(ev.message);
      }
    }
    await tl.end(); settle(st);
  } catch (err) { ok = false; st.raw = 'The knowledge graph is unavailable right now: ' + (err.message || 'error'); }
  const answer = speakable(st.raw, 2800);
  // "look, here": the strongest cited recommendation in its original guideline table, live UMLS/Wikidata context,
  // then the clef-flash director arranges the shared screen
  const cited = [...KG.cited];
  const seeds = keySeeds(question, 2).map(n => n.id);
  if (ok && cited.length) {
    nar.say(SAY[lang].table, true);
    try { const x = await (await fetch('/api/kg/evidence?id=' + encodeURIComponent(cited[0]))).json(); if (x && x.text) showTable(x); } catch {}
  }
  if (seeds[0]) inspect(seeds[0]);
  winLayout('split'); if (WM.wins.has('table')) winFront('table');
  direct('found', answer.slice(0, 600));
  await nar.done(); await faceIdle(12000);
  toolResult(m, p, ok ? answer : st.raw, ok);
}
async function linoCall(m, p) {
  let args = p.arguments; try { if (typeof args === 'string') args = JSON.parse(args); } catch { args = {}; }
  const question = (args && args.question) || '';
  stageShow(true); stageBusy(true, 'Asking the fine-tuned model: ' + question); $('#stage-title').textContent = 'Lino · Llama 3.2 1B (NeSyCoT)';
  let output, ok = true;
  try { output = (await post('/api/call/lino', {question})).output || ''; }
  catch (err) { output = 'My model is not reachable right now.'; ok = false; }
  stageBusy(false, question);
  const spoken = speakable(output);
  echo(spoken);                          // Lino's own words, verbatim
  toolResult(m, p, spoken, ok);          // on_resolve add_to_context: kept in history, not re-spoken
  liveBoard(output, 'lino');
}
async function onAppMessage(e) {
  const m = e?.data; if (!m) return;
  if (m.event_type === 'conversation.replica.started_speaking') { face.speaking = true; return; }
  if (m.event_type === 'conversation.replica.stopped_speaking') { face.speaking = false; face.stoppedAt = performance.now(); return; }
  if (m.event_type === 'conversation.utterance') {   // what the face just said -> live board
    const pr = m.properties || {};
    if (pr.role === 'replica' && pr.speech && !stage.greeted) { stage.greeted = true; return; }  // greeting: no board
    if (pr.role === 'replica' && pr.speech) {
      const k = normSpeech(pr.speech).slice(0, 60);
      if (!stage.said.some(x => x && (k.startsWith(x.slice(0, 40)) || x.startsWith(k.slice(0, 40))))) { liveBoard(pr.speech, callAgent); if (WM.on) direct('answering', pr.speech); }
    }
    return;
  }
  if (m.event_type !== 'conversation.tool_call') return;
  const p = m.properties || {};
  if (p.name === 'ask_guideline_graph') return guidoCall(m, p);
  if (p.name === 'ask_lino') return linoCall(m, p);
}
async function leaveCall(message) {
  clearInterval(tick); clearTimeout(endTimer);
  const id = convId; convId = null;
  if (call) { const c = call; call = null; try { await c.leave(); } catch {} c.destroy().catch(() => {}); }
  if (id) post('/api/call/end', {conversation_id: id}).catch(() => {});
  stageShow(false); shareOff();
  stopPreview(); closeOverlay(); if (message) status(message);
}
$('#d-end').onclick = () => leaveCall('Call ended.');
// chat drawer during the call reuses the same conversation
function restoreChat() {
  if ($('#feed').parentNode.id !== 'view-chat') $('#view-chat').append($('#feed'), $('#composer'));
  $('#d-chat').setAttribute('aria-pressed', false);
}
$('#d-chat').onclick = () => {
  const open = !$('#cx-chat').classList.contains('open');
  if (open) $('#cx-slot').append($('#feed'), $('#composer')); else restoreChat();
  $('#cx-chat').classList.toggle('open', open); cx.classList.toggle('chat-open', open); $('#d-chat').setAttribute('aria-pressed', open);
  if (open) setTimeout(() => $('#msg').focus(), 300);
};
$('#cx-chat-close').onclick = () => { if ($('#cx-chat').classList.contains('open')) $('#d-chat').click(); };
addEventListener('keydown', e => { if (e.key === 'Escape' && !cx.classList.contains('hidden') && !convId) $('#pj-cancel').click(); });
addEventListener('pagehide', () => { if (convId) navigator.sendBeacon?.('/api/call/end-beacon', JSON.stringify({conversation_id: convId})); });
