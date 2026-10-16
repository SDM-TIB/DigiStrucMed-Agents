/* Sources > Graph: the complete final MHH guideline graph, explored live over the graph server (QLever SPARQL).
   G6 v5 (AntV, MIT) draws it: force layout, minimap, hover highlight. nothing of the graph is bundled into the page;
   search, details and neighbours come from /api/graph/*. G6 loads the first time the tab opens. */
(() => {
  const G6_URL = 'https://cdn.jsdelivr.net/npm/@antv/g6@5.1.1/dist/g6.min.js';
  const START = 'http://digistrucmed.org/entity/heart_failure';  // first view: one well-connected concept
  const PAGE_SIZE = 40;
  const GX = {g: null, ready: null, kinds: {}, hidden: new Set(), sel: null, req: 0, offset: new Map()};
  const T = (s, v) => uiText(s, v);
  const nf = n => (n == null ? '–' : Number(n).toLocaleString(careLocale()));
  const css = name => getComputedStyle(document.body).getPropertyValue(name).trim() || undefined;
  const color = k => (GX.kinds[k] || {}).color || '#94a3b8';
  const short = (s, n = 38) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');

  async function api(path, params) {
    const r = await fetch(path + '?' + new URLSearchParams(params || {}));
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  }

  function loadG6() {
    if (window.G6) return Promise.resolve();
    return new Promise((ok, fail) => {
      const s = document.createElement('script');
      s.src = G6_URL; s.onload = ok; s.onerror = () => fail(new Error('G6 could not be loaded'));
      document.head.append(s);
    });
  }

  function status(text) { $('#gx-status').textContent = text || ''; }

  async function init() {
    await loadG6();
    const {kinds} = await api('/api/graph/kinds');
    kinds.forEach(k => GX.kinds[k.kind] = k);
    renderKinds(kinds);
    fillCounts(kinds, 0);
    const ink = css('--ink') || '#1f2937', paper = css('--bg') || '#ffffff', muted = css('--muted') || '#64748b';
    GX.g = new G6.Graph({
      container: 'gx-canvas', autoResize: true, data: {nodes: [], edges: []},
      node: {
        style: {
          size: d => ({concept: 20, recommendation: 16, guideline: 22, page: 14, code: 12})[d.data.kind] || 10,
          fill: d => color(d.data.kind), stroke: paper, lineWidth: 1.5,
          labelText: d => short(d.data.label), labelFill: ink, labelFontSize: 10, labelPlacement: 'bottom',
          labelBackground: true, labelBackgroundFill: paper, labelBackgroundOpacity: 0.75, labelBackgroundRadius: 3,
        },
        state: {selected: {lineWidth: 3, stroke: ink, halo: true, haloLineWidth: 10}, active: {halo: true, haloLineWidth: 8}},
      },
      edge: {
        style: {stroke: muted, strokeOpacity: 0.55, lineWidth: 1, endArrow: true, endArrowSize: 5,
                labelText: d => d.data.p, labelFontSize: 8, labelFill: muted, labelBackground: true, labelBackgroundFill: paper, labelBackgroundOpacity: 0.8},
        state: {active: {strokeOpacity: 1, lineWidth: 1.6}},
      },
      layout: {type: 'd3-force', link: {distance: 120, strength: 0.35}, manyBody: {strength: -280}, collide: {radius: 36}},
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element-force', {type: 'hover-activate', degree: 1}],
      plugins: [
        {type: 'minimap', size: [220, 140], position: 'right-bottom'},
        {type: 'tooltip', trigger: 'hover', getContent: async (e, items) => {
          const d = items[0]; if (!d || !d.data || !d.data.kind) return '';
          return `<div class="gx-tip"><i style="background:${color(d.data.kind)}"></i><b>${esc(T((GX.kinds[d.data.kind] || {}).name || d.data.kind))}</b><p>${esc(d.data.label || '')}</p></div>`;
        }},
      ],
    });
    GX.g.on('node:click', e => select(e.target.id));
    GX.g.on('node:dblclick', e => expand(e.target.id));
    await GX.g.render();
    await openNode(START, true);
  }

  function renderKinds(kinds) {
    $('#gx-kinds').innerHTML = kinds.map(k => `<label class="gx-kind"><input type="checkbox" data-kind="${esc(k.kind)}" checked>
      <i style="background:${k.color}"></i><span>${esc(T(k.name))}</span><small>${nf(k.count)}</small></label>`).join('');
    $('#gx-kinds').querySelectorAll('input').forEach(c => c.onchange = () => {
      c.checked ? GX.hidden.delete(c.dataset.kind) : GX.hidden.add(c.dataset.kind);
      applyFilter();
    });
  }

  // the server counts the nodes of each kind once, in the background: ask again until the numbers are there
  async function fillCounts(kinds, tries) {
    if (kinds.some(k => k.count != null) || tries > 20) return;
    await new Promise(r => setTimeout(r, 3000));
    try {
      const x = await api('/api/graph/kinds');
      x.kinds.forEach(k => { const el = $(`#gx-kinds input[data-kind="${k.kind}"]`); if (el) el.parentElement.querySelector('small').textContent = nf(k.count); });
      fillCounts(x.kinds, tries + 1);
    } catch {}
  }

  function applyFilter() {
    const g = GX.g; if (!g) return;
    const hide = [], show = [];
    const hiddenNode = new Set();
    g.getNodeData().forEach(n => (GX.hidden.has(n.data.kind) ? (hide.push(n.id), hiddenNode.add(n.id)) : show.push(n.id)));
    g.getEdgeData().forEach(e => ((hiddenNode.has(e.source) || hiddenNode.has(e.target)) ? hide : show).push(e.id));
    if (hide.length) g.hideElement(hide);
    if (show.length) g.showElement(show);
  }

  // add nodes (and the edges between them) next to the node they came from, then let the layout settle
  async function add(nodes, links, near) {
    const g = GX.g;
    const have = new Set(g.getNodeData().map(n => n.id));
    let [x, y] = near && have.has(near) ? g.getElementPosition(near) : [0, 0];
    const fresh = nodes.filter(n => !have.has(n.id)).map(n => ({id: n.id, data: {kind: n.kind, label: n.label},
      style: {x: x + (Math.random() - 0.5) * 80, y: y + (Math.random() - 0.5) * 80}}));
    fresh.forEach(n => have.add(n.id));
    const haveE = new Set(g.getEdgeData().map(e => e.id));
    const edges = [];
    for (const l of links) {
      const id = l.source + '\u0001' + l.p + '\u0001' + l.target;
      if (haveE.has(id) || !have.has(l.source) || !have.has(l.target)) continue;
      haveE.add(id); edges.push({id, source: l.source, target: l.target, data: {p: l.p}});
    }
    if (!fresh.length && !edges.length) return 0;
    g.addData({nodes: fresh, edges});
    applyFilter();
    await g.draw();  // the new elements now; the force layout settles in the background (it never "ends" while
    g.layout();      // drag-element-force keeps the simulation alive, so nothing may wait for it)
    return fresh.length;
  }

  async function openNode(id, withNeighbours) {
    const req = ++GX.req;
    status(T('Loading…'));
    try {
      const n = await api('/api/graph/node', {id});
      if (req !== GX.req) return;
      await add([{id: n.id, kind: n.kind, label: n.label}], []);
      if (withNeighbours) await expand(id);
      select(id, n);
      GX.g.focusElement(id, {duration: 500});
      status('');
    } catch (e) { console.error('graph explorer', e); status(T('The knowledge graph server is not reachable right now')); }
  }

  async function expand(id, p, dir) {
    const key = [id, p || '', dir || ''].join('\u0001');
    const offset = GX.offset.get(key) || 0;
    status(T('Loading…'));
    try {
      const x = await api('/api/graph/neighbours', {id, ...(p ? {p} : {}), ...(dir ? {dir} : {}), limit: PAGE_SIZE, offset});
      GX.offset.set(key, offset + PAGE_SIZE);
      const added = await add(x.nodes, x.links, id);
      status(added ? T('{n} nodes added', {n: added}) : T('No further nodes'));
      return added;
    } catch (e) { console.error('graph explorer', e); status(T('The knowledge graph server is not reachable right now')); }
  }

  async function select(id, known) {
    const g = GX.g;
    if (GX.sel && g.hasNode(GX.sel)) g.setElementState(GX.sel, []);
    GX.sel = id;
    if (g.hasNode(id)) g.setElementState(id, ['selected']);
    const box = $('#gx-detail');
    box.innerHTML = `<p class="gx-hint">${esc(T('Loading…'))}</p>`;
    let n = known;
    try { n = n || await api('/api/graph/node', {id}); } catch { box.innerHTML = `<p class="gx-hint">${esc(T('The knowledge graph server is not reachable right now'))}</p>`; return; }
    if (GX.sel !== id) return;
    box.innerHTML = detailHTML(n);
    box.querySelectorAll('[data-expand]').forEach(b => b.onclick = async () => {
      const [p, dir] = [b.dataset.expand, b.dataset.dir];
      const added = await expand(id, p, dir);
      if (added === 0) b.disabled = true;
    });
    box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => openNode(b.dataset.open, false));
    box.querySelectorAll('.gx-json').forEach(pre => { try { pre.textContent = JSON.stringify(JSON.parse(pre.textContent), null, 1); } catch {} });
  }

  const PAGE_IMG = n => n.kind === 'page' ? n.id.split('/').pop() : null;
  const CODE_URL = {
    umls: c => `https://uts.nlm.nih.gov/uts/umls/concept/${c}`, mesh: c => `https://meshb.nlm.nih.gov/record/ui?ui=${c}`,
    snomed: c => `https://browser.ihtsdotools.org/?perspective=full&conceptId1=${c}`, rxnorm: c => `https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm=${c}`,
    loinc: c => `https://loinc.org/${c}`, wikidata: c => `https://www.wikidata.org/wiki/${c}`};

  function detailHTML(n) {
    const kind = GX.kinds[n.kind] || {name: n.kind};
    const prop = name => (n.props.find(p => p.p === name) || {}).value;
    let h = `<header class="gx-dh"><span class="gx-badge" style="--c:${color(n.kind)}">${esc(T(kind.name))}</span>
      <h4>${esc(n.label)}</h4><code class="gx-iri" title="${esc(n.id)}">${esc(n.id)}</code>
      ${n.types.length ? `<small>${esc(T('Type'))}: ${n.types.map(esc).join(', ')}</small>` : ''}</header>`;
    if (PAGE_IMG(n)) h += `<img class="gx-page" loading="lazy" alt="" src="/api/page-image/${encodeURIComponent(PAGE_IMG(n))}" onerror="this.remove()">`;
    if (n.kind === 'code' && CODE_URL[prop('terminologySource')]) h += `<a class="gx-ext" target="_blank" rel="noopener" href="${esc(CODE_URL[prop('terminologySource')](prop('code')))}">${esc(T('Open in the terminology browser'))} ↗</a>`;
    if (n.groups.length) {
      h += `<h5>${esc(T('Connections'))}</h5><div class="gx-groups">` + n.groups.map(g => `<button data-expand="${esc(g.p)}" data-dir="${g.dir}">
        <span>${g.dir === 'out' ? '→' : '←'} ${esc(g.name)}</span><b>${nf(g.count)}</b></button>`).join('') + '</div>';
    }
    if (n.props.length) {
      h += `<h5>${esc(T('Properties'))}</h5><dl class="gx-props">` + n.props.map(p => {
        const v = p.value, json = /^[[{]/.test(v.trim());
        return `<dt>${esc(p.p)}</dt><dd>${json ? `<pre class="gx-json">${esc(v)}</pre>` : v.length > 400 ? `<details><summary>${esc(v.slice(0, 200))}…</summary>${esc(v)}</details>` : esc(v)}</dd>`;
      }).join('') + '</dl>';
    }
    return h;
  }

  // search over the whole graph: concepts, codes, recommendations, page and Level-4 mentions, units
  let qTimer = 0, qSeq = 0;
  async function search(v) {
    const box = $('#gx-res'), seq = ++qSeq;
    if (v.trim().length < 2) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden'); box.innerHTML = `<p class="gx-hint">${esc(T('Searching the complete graph…'))}</p>`;
    let x;
    try { x = await api('/api/graph/search', {q: v}); } catch { box.innerHTML = `<p class="gx-hint">${esc(T('The knowledge graph server is not reachable right now'))}</p>`; return; }
    if (seq !== qSeq) return;
    if (!x.items.length) { box.innerHTML = `<p class="gx-hint">${esc(T('Nothing found'))}</p>`; return; }
    const groups = {};
    x.items.forEach(it => (groups[it.kind] = groups[it.kind] || []).push(it));
    box.innerHTML = Object.entries(groups).map(([k, items]) => `<div class="gx-rg"><h6><i style="background:${color(k)}"></i>${esc(T((GX.kinds[k] || {}).name || k))}</h6>` +
      items.map(it => `<button data-id="${esc(it.id)}">${esc(short(it.label, 90))}</button>`).join('') + '</div>').join('');
    box.querySelectorAll('button').forEach(b => b.onclick = () => { box.classList.add('hidden'); openNode(b.dataset.id, true); });
  }

  function wire() {
    $('#gx-q').addEventListener('input', e => { clearTimeout(qTimer); qTimer = setTimeout(() => search(e.target.value), 250); });
    $('#gx-q').addEventListener('keydown', e => {
      if (e.key === 'Enter') $('#gx-res button')?.click();
      if (e.key === 'Escape') { e.stopPropagation(); $('#gx-res').classList.add('hidden'); }
    });
    $('#gx-in').onclick = () => GX.g && GX.g.zoomBy(1.3, {duration: 250});
    $('#gx-out').onclick = () => GX.g && GX.g.zoomBy(1 / 1.3, {duration: 250});
    $('#gx-fit').onclick = () => GX.g && GX.g.fitView({when: 'always'}, {duration: 400});
    $('#gx-relayout').onclick = () => GX.g && GX.g.layout();
    $('#gx-clear').onclick = async () => {
      if (!GX.g) return;
      GX.g.clear(); GX.offset.clear(); GX.sel = null;
      $('#gx-detail').innerHTML = `<p class="gx-hint">${esc(T('Search the graph or click a node.'))}</p>`;
    };
  }

  // the Sources view switches between the documents and the graph
  function mode(which) {
    const graph = which === 'graph';
    $('#src-tab-docs').setAttribute('aria-selected', String(!graph));
    $('#src-tab-graph').setAttribute('aria-selected', String(graph));
    $('#view-sources').classList.toggle('graph-mode', graph);
    if (graph && !GX.ready) GX.ready = init().catch(e => { GX.ready = null; status(T('The knowledge graph server is not reachable right now')); console.error(e); });
  }
  wire();
  $('#src-tab-docs').onclick = () => mode('docs');
  $('#src-tab-graph').onclick = () => mode('graph');
  // other parts of the app open a node in the explorer, e.g. a concept from a card
  window.graphExplorer = {open: async id => { view('sources'); mode('graph'); await GX.ready; openNode(id, true); }};
})();
