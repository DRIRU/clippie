/* Shared shell and helpers for the dashboard pages. Include after dash.css:
 *   <div id="shell"></div> ... <script src="/dash.js"></script> <script>Dash.mount('stages')</script>
 * A tab with `ready: false` is shown as "soon" until its page exists. */
(function () {
  const TABS = [
    { id: 'overview', label: 'Overview', href: '/dashboard.html', ready: false },
    { id: 'stages', label: 'Stages', href: '/perf.html', ready: true },
    { id: 'compare', label: 'Compare', href: '/compare.html', ready: false },
    { id: 'videos', label: 'Videos', href: '/videos.html', ready: false },
    { id: 'live', label: 'Live', href: '/live.html', ready: false },
    { id: 'resources', label: 'Resources', href: '/resources.html', ready: false },
  ];

  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') e.className = v; else if (k.startsWith('on')) e[k] = v; else if (v != null && v !== false) e.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
    return e;
  };

  const num = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d));
  // 0.4 -> "0.40 s", 75 -> "1 min 15 s", 5400 -> "1 h 30 min"
  const secs = v => {
    if (v == null) return '-';
    v = Number(v);
    if (v < 10) return v.toFixed(2) + ' s';
    if (v < 60) return v.toFixed(1) + ' s';
    if (v < 3600) return Math.floor(v / 60) + ' min ' + Math.round(v % 60) + ' s';
    return Math.floor(v / 3600) + ' h ' + Math.round((v % 3600) / 60) + ' min';
  };

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) { /* keep status text */ } throw new Error(m); }
    return r.json();
  }

  function mount(active, opts = {}) {
    const host = document.getElementById('shell');
    host.replaceChildren(
      el('header', { class: 'dash-bar' },
        el('a', { class: 'brand', href: '/' }, 'clip', el('b', {}, 'pie')),
        el('span', { class: 'tag' }, 'Performance'),
        el('a', { class: 'back', href: '/' }, 'Back to the app')),
      el('div', { class: 'dash-wrap', style: 'padding-bottom:0;margin-top:24px' },
        el('nav', { class: 'dash-tabs', 'aria-label': 'Performance sections' },
          ...TABS.map(t => t.ready
            ? el('a', { href: t.href, class: t.id === active ? 'on' : '', 'aria-current': t.id === active ? 'page' : null }, t.label)
            : el('span', { 'aria-disabled': 'true', title: 'Planned, not built yet' }, t.label, el('small', {}, 'soon'))))));
    document.title = 'clippie - ' + (opts.title || TABS.find(t => t.id === active).label);
  }

  window.Dash = { mount, el, num, secs, getJSON, TABS };
})();
