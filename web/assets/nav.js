// NEOS shared nav + logo. Each page calls NEOS_NAV.render('key') once,
// where key identifies the current page for the active-link highlight.
const NEOS_LOGO = `
<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M16 2 L28.2 9 V23 L16 30 L3.8 23 V9 Z" stroke="#1B7A4B" stroke-width="2" fill="#F2F8F4"/>
  <path d="M10.5 20.5 V11.5 L21.5 20.5 V11.5" stroke="#1B7A4B" stroke-width="2.1" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="21.5" cy="11.5" r="1.9" fill="#39C26D"/>
</svg>`;

const NEOS_PAGES = [
  ['home', 'Home', '/'],
  ['grid', 'Grid', './grid.html'],
  ['community', 'Community', './community.html'],
  ['utility', 'Utility', './utility.html'],
  
  ['rural', 'Rural', './rural.html'],
  ['hub', 'Participation', './hub.html'],
  ['evidence', 'Evidence', './evidence.html'],
];

const NEOS_NAV = {
  render(active, standalone){
    const links = standalone
      ? NEOS_PAGES.map(([key, label]) =>
          `<span style="font-size:13.5px;color:var(--ink-faint);padding:7px 10px">${label}</span>`).join('')
      : NEOS_PAGES.map(([key, label, href]) =>
          `<a href="${href}"${key===active ? ' aria-current="page"' : ''}>${label}</a>`).join('');
    const html = `
      <nav class="neos-nav">
        <div class="neos-nav-in">
          <div style="display:flex;align-items:center;gap:4px">
            <div class="neos-links">${links}</div>
            <div class="neos-status"><span class="dot"></span><span id="neos-backend-label">checking backend…</span></div>
          </div>
          <${standalone ? 'span' : 'a href="/"'} class="neos-brand" style="text-decoration:none">
            <span class="word">NEOS</span>${NEOS_LOGO}
          </${standalone ? 'span' : 'a'}>
        </div>
      </nav>`;
    const root = document.getElementById('neos-nav-root');
    if (root) root.outerHTML = html;
    fetch('/api/meta').then(r => r.json()).then(d => {
      const el = document.getElementById('neos-backend-label');
      if (el) el.textContent = `${d.grid_backend} · ${d.homes} homes`;
    }).catch(() => {
      const el = document.getElementById('neos-backend-label');
      if (el) el.textContent = 'backend unreachable';
    });
  },
  renderFoot(){
    const html = `
      <footer class="neos-foot"><div class="neos-foot-in">
        <span>NEOS — Neighbourhood Energy Operating System</span>
        <span>Simulation only. No equipment is commanded by this console. See <a href="./evidence.html">Evidence</a>.</span>
      </div></footer>`;
    const root = document.getElementById('neos-foot-root');
    if (root) root.outerHTML = html;
  }
};

