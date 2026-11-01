/* Reach app screens. Plain JavaScript, no libraries. All work happens in app/actions.py;
   this file only draws the screens and sends button presses to the local server. */
'use strict';

const TOKEN = document.querySelector('meta[name="reach-token"]').content;
let S = null;                       // latest status from the server
let JOB = null;                     // the current or last task
let jobTimer = null;
const ui = { form: {}, jobDismissed: '', newKey: null, openDoor: '' };

/* ------------------------------------------------------------------ helpers */
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ICON = {
  check: '<svg class="cn-icon-done" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10"></path></svg>',
  spin: '<svg class="cn-icon-run spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 3a9 9 0 1 1-9 9"></path></svg>',
  ext: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17L17 7"></path><path d="M8 7h9v9"></path></svg>',
  warn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:0 0 auto"><path d="M12 3l9 16H3z"></path><path d="M12 10v4"></path><path d="M12 17h.01"></path></svg>',
};
const ring = '<span class="cn-ring" aria-hidden="true"></span>';
const num = (n) => `<span class="cn-step-num" aria-hidden="true">${n}</span>`;
function field(key, def) { return ui.form[key] !== undefined ? ui.form[key] : (def || ''); }
function input(key, label, opts = {}) {
  const id = 'f-' + key;
  return `<div class="cn-field"><label class="cn-label" for="${id}">${esc(label)}</label>
    <input class="cn-input" id="${id}" data-bind="${key}" type="${opts.type || 'text'}" value="${esc(field(key, opts.value))}"
      ${opts.placeholder ? `placeholder="${esc(opts.placeholder)}"` : ''} autocomplete="off" spellcheck="false">
    ${opts.help ? `<span class="cn-help">${esc(opts.help)}</span>` : ''}</div>`;
}
function button(label, action, kind = 'primary', extra = '') {
  const busy = JOB && JOB.state === 'running';
  return `<button type="button" class="cn-btn cn-btn--${kind}" data-action="${action}" ${extra} ${busy ? 'disabled' : ''}>${esc(label)}</button>`;
}
function link(label, href, kind = 'secondary') {
  return `<a class="cn-btn cn-btn--${kind}" href="${esc(href)}" target="_blank" rel="noopener">${esc(label)} ${ICON.ext}</a>`;
}
function copyField(label, value) {
  return `<div class="copy-row"><span class="cn-label">${esc(label)}</span><div class="cn-copy"><code>${esc(value)}</code>
    <button type="button" class="cn-btn cn-btn--secondary cn-btn--sm" data-copy="${esc(value)}">Copy</button></div></div>`;
}
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 4000);
}
const args = (o) => `data-args='${esc(JSON.stringify(o))}'`;

/* ------------------------------------------------------------------ talking to the local server */
async function api(path, body) {
  const opts = { headers: { 'X-Reach-Token': TOKEN } };
  if (body !== undefined) { opts.method = 'POST'; opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `The app answered ${r.status}`);
  return data;
}
function takeJob(j) {
  if (!j) return;
  JOB = j;
  if (j.state === 'done' && j.result && j.result.client_secret) ui.newKey = j.result;   // shown once
}
async function refresh(fresh) {
  try {
    S = await api('/api/status' + (fresh ? '?fresh=1' : ''));
    if (S.job && (!JOB || S.job.id === JOB.id || S.job.started >= JOB.started)) takeJob(S.job);
    if (JOB && JOB.state === 'running') watchJob();
  } catch (e) {
    const gone = e instanceof TypeError;
    document.getElementById('main').innerHTML = `<div class="job job--failed"><strong>${gone ? 'Reach is not running.' : 'Reach hit a problem.'}</strong>
      <span class="small">${gone ? 'Open Reach again from Applications; it opens a fresh page. Your doors keep working while Reach is closed.' : esc(e.message)}</span></div>`;
    return;
  }
  render();
}
async function act(name, a = {}) {
  try {
    takeJob(await api('/api/action/' + name, a));
    ui.jobDismissed = '';
    render();
    watchJob();
  } catch (e) { toast(e.message); }
}
function watchJob() {
  if (jobTimer) return;
  jobTimer = setInterval(async () => {
    try { takeJob(await api('/api/job/' + JOB.id)); } catch (e) { return; }
    renderJob();
    if (JOB.state !== 'running') {
      clearInterval(jobTimer); jobTimer = null;
      if (JOB.state === 'done') {
        delete ui.form.cf_token;
        if (JOB.name === 'door_add') ui.form = {};
        if (JOB.name === 'device_add') Object.keys(ui.form).filter((k) => k.startsWith('dev_')).forEach((k) => delete ui.form[k]);
      }
      await refresh(true);
    }
  }, 1000);
}

/* ------------------------------------------------------------------ navigation */
const VIEWS = ['doors', 'setup'];
function view() { const v = location.hash.replace('#', ''); return VIEWS.includes(v) ? v : (S && !S.setup_done ? 'setup' : 'doors'); }
window.addEventListener('hashchange', () => { if (JOB && JOB.state === 'done') ui.jobDismissed = JOB.id; render(); document.getElementById('main').focus(); });
function setupStep() { const i = ['tools', 'cloudflare', 'domain', 'connect'].findIndex((k) => !S.steps[k]); return i < 0 ? null : i + 1; }
function connState() {
  const c = S.connector;
  if (!S.steps.connect) return ['Not connected', ''];
  if (!c.wanted || !c.running) return ['Off: nothing reachable', 'wait'];
  if (c.cloud === 'healthy') return ['Live', 'done'];
  return ['Connecting…', 'wait'];
}
function renderNav() {
  const v = view();
  const meta = (txt, kind) => `<span class="cn-meta ${kind ? 'cn-meta--' + kind : ''}">${esc(txt)}</span>`;
  const open = S.doors.filter((d) => d.open).length;
  const items = [
    ['doors', 'Doors', S.doors.length ? meta(`${open} open`) : ''],
    ['setup', 'Set up', S.setup_done ? meta('Done', 'done') : meta(`Step ${setupStep()} of 4`)],
  ];
  document.getElementById('nav').innerHTML = items.map(([id, label, m]) =>
    `<a class="cn-nav-item" href="#${id}" ${v === id ? 'aria-current="page"' : ''}>${label}${m}</a>`).join('');
  document.getElementById('zone-name').textContent = S.zone || 'No domain yet';
  document.getElementById('conn-state').textContent = connState()[0];
}

/* ------------------------------------------------------------------ the task panel */
function jobPanel() {
  if (!JOB || ui.jobDismissed === JOB.id) return '';
  const state = JOB.state;
  const icon = state === 'running' ? ICON.spin : state === 'done' ? ICON.check : ICON.warn;
  const head = state === 'running' ? `${esc(JOB.title)}…` : state === 'done' ? `${esc(JOB.title)}: done` : `${esc(JOB.title)}: stopped`;
  return `<section class="job job--${state}" aria-live="polite">
    <div class="row-between"><span class="row" style="font-weight:600">${icon}${head}</span>
      ${state !== 'running' ? '<button type="button" class="cn-btn cn-btn--ghost cn-btn--sm" data-dismiss>Close</button>' : ''}</div>
    ${JOB.error ? `<div class="error-text">${esc(JOB.error)}</div>` : ''}
    ${JOB.log && JOB.log.length ? `<details class="cn-details" ${state === 'failed' ? 'open' : ''}><summary>Show technical log</summary>
      <pre class="cn-log">${esc(JOB.log.slice(-60).join('\n'))}</pre></details>` : ''}
  </section>`;
}
function renderJob() { const el = document.getElementById('job-slot'); if (el) el.innerHTML = jobPanel(); }
function head(title, lede, actions = '') {
  return `<header class="cn-head"><div class="stack" style="gap:6px"><h1 class="cn-h1">${title}</h1>${lede ? `<p class="cn-lede">${lede}</p>` : ''}</div>${actions}</header>`;
}

/* ------------------------------------------------------------------ doors */
function newKeyCard() {
  const k = ui.newKey;
  if (!k) return '';
  const curl = `curl ${k.url}/ -H "CF-Access-Client-Id: ${k.client_id}" -H "CF-Access-Client-Secret: ${k.client_secret}"`;
  return `<section class="cn-card cn-card--decision"><span class="cn-eyebrow cn-eyebrow--accent">Copy now: shown only once</span>
    <h2 class="cn-h3">Device key for ${esc(k.device)}</h2>
    <p class="small" style="margin:0">The device sends both values as headers with every request. Store them in that device's password manager or the app's settings. If they are lost, remove this key and create a new one.</p>
    ${copyField('CF-Access-Client-Id', k.client_id)}${copyField('CF-Access-Client-Secret', k.client_secret)}
    <details class="cn-details"><summary>Example: test it from the device's Terminal</summary><div class="cn-copy"><code style="white-space:pre-wrap;word-break:break-all">${esc(curl)}</code><button type="button" class="cn-btn cn-btn--secondary cn-btn--sm" data-copy="${esc(curl)}">Copy</button></div></details>
    <div class="row"><button type="button" class="cn-btn cn-btn--primary" data-clear-key>I’ve stored it</button></div></section>`;
}
function doorCard(d) {
  const expanded = ui.openDoor === d.id;
  const live = connState()[1] === 'done';
  const state = !d.open ? '<span class="cn-status">Closed</span>'
    : live ? '<span class="cn-status cn-status--success"><span class="cn-dot cn-dot--success"></span>Open</span>'
      : '<span class="cn-status"><span class="cn-dot"></span>Open, but the connector is off</span>';
  const local = d.local ? `<span class="small muted">port ${d.port} · answering on this Mac</span>`
    : `<span class="small" style="color:var(--warning-ink)">port ${d.port} · nothing answering: start the tool on this Mac</span>`;
  const devices = d.devices.length ? `<div class="cn-table-wrap"><table class="cn-table"><tbody>${d.devices.map((v) => `<tr><td>${esc(v.name)}</td>
      <td class="small muted">since ${esc(v.created)}</td><td style="text-align:right">${button('Remove', 'device_remove', 'ghost cn-btn--sm', args({ id: d.id, device: v.id }) + ` data-confirm="Remove the key for ${esc(v.name)}? That device loses access at once."`)}</td></tr>`).join('')}</tbody></table></div>`
    : '<p class="small muted" style="margin:0">None yet. Browsers don’t need one; scripts and apps that can send headers do.</p>';
  const details = expanded ? `<div class="stack" style="border-top:1px solid var(--line-subtle);padding-top:var(--space-4)">
      <h3 class="cn-h3" style="margin:0">Who may sign in</h3>
      <div class="fields">${input('emails_' + d.id, 'Email addresses, separated by commas', { value: d.emails.join(', ') })}</div>
      <div class="row">${button('Update people', 'door_people', 'secondary', args({ id: d.id }))}</div>
      <h3 class="cn-h3" style="margin:0">Device keys</h3>${devices}
      <div class="fields">${input('dev_' + d.id, 'New device name', { placeholder: 'Laptop, Phone shortcut, Agent server' })}</div>
      <div class="row">${button('Create device key', 'device_add', 'secondary', args({ id: d.id }))}</div>
      <div class="row" style="justify-content:flex-end">${button('Remove door', 'door_remove', 'danger', args({ id: d.id }) + ` data-confirm="Remove ${esc(d.url)}? Its address, sign-in rule and device keys are deleted."`)}</div></div>` : '';
  return `<section class="cn-card"><div class="row-between"><div class="stack" style="gap:4px">
      <div class="row"><strong style="font-weight:600">${esc(d.name)}</strong>${state}</div>
      <a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.url.replace('https://', ''))}</a>${local}
      <span class="small muted">${d.emails.length} ${d.emails.length === 1 ? 'person' : 'people'} · ${d.devices.length} device key${d.devices.length === 1 ? '' : 's'}</span></div>
    <div class="row">${d.open ? button('Close door', 'door_close', 'secondary', args({ id: d.id })) : button('Open door', 'door_open', 'primary', args({ id: d.id }))}
      <button type="button" class="cn-btn cn-btn--ghost" data-toggle-door="${esc(d.id)}" aria-expanded="${expanded}">${expanded ? 'Hide settings' : 'Settings'}</button></div></div>${details}</section>`;
}
function addDoorCard() {
  const found = S.detected.length ? `<div class="stack" style="gap:6px"><span class="cn-label">Found on this Mac</span><div class="row">${S.detected.map((p) =>
    `<button type="button" class="cn-btn cn-btn--secondary cn-btn--sm" data-preset='${esc(JSON.stringify(p))}'>${esc(p.name)} · port ${p.port}</button>`).join('')}</div></div>`
    : '<p class="small muted" style="margin:0">No common AI tools are answering on this Mac right now. Start one, or type its port below.</p>';
  return `<section class="cn-card"><h2 class="cn-h2">Add a door</h2>${found}
    <div class="fields">${input('name', 'Name', { placeholder: 'Ollama' })}${input('sub', `Address: ___.${S.zone}`, { placeholder: 'ai (letters, digits, hyphens)' })}${input('port', 'Port on this Mac', { placeholder: '11434' })}</div>
    <div class="fields">${input('emails', 'Who may sign in (email addresses, separated by commas)', { placeholder: 'you@example.com' })}</div>
    <label class="row small" style="flex-wrap:nowrap;align-items:flex-start"><input type="checkbox" data-bind-check="api" ${field('api') ? 'checked' : ''}> It’s an API that only accepts local requests (Ollama, LM Studio). Reach presents requests as local.</label>
    <div class="row">${button('Add door', 'door_add')}</div></section>`;
}
function vDoors() {
  if (!S.setup_done) return head('Doors', '') + `<div class="empty">Finish <a href="#setup">Set up</a> first (about 15 minutes).</div>`;
  const c = S.connector, [txt, kind] = connState();
  const sw = c.wanted && c.running
    ? button('Close everything', 'connector', 'danger', args({ on: false }) + ' data-confirm="Turn off the connector? Every door closes at once until you turn it back on."')
    : button('Turn connector on', 'connector', 'primary', args({ on: true }));
  const top = `<section class="cn-grid" aria-label="Status">
    <div class="cn-card ${kind === 'done' ? '' : 'cn-card--decision'}"><span class="cn-eyebrow">Connector</span>
      <span class="big-number"><span class="cn-dot ${kind === 'done' ? 'cn-dot--success' : ''}"></span>${esc(txt)}</span>
      <div class="row">${sw}${button('Check status', 'refresh', 'ghost')}</div></div>
    <div class="cn-card"><span class="cn-eyebrow">Domain</span><span style="font-weight:600">${esc(S.zone)}</span>
      <span class="small muted">Sign-in page: ${esc(S.team_domain)}</span></div>
  </section>`;
  const note = S.origin_check ? '' : `<div class="cn-callout cn-callout--warning">${ICON.warn}<span>Cloudflare didn’t accept the second sign-in check on this Mac, so doors rely on Cloudflare’s sign-in alone (still required for every visit). Nothing to do now; Reach retries each time a door changes.</span></div>`;
  const list = S.doors.length ? S.doors.map(doorCard).join('') : '<div class="empty">No doors yet. Add the first one below.</div>';
  return head('Doors', 'Each door is one tool on this Mac at its own address. Every visit asks for sign-in first.') +
    top + note + newKeyCard() + list + addDoorCard();
}

/* ------------------------------------------------------------------ set up */
function vSetup() {
  const step = setupStep(), st = S.steps;
  const rows = [
    ['tools', 'Tools on this Mac', st.tools ? 'Cloudflare’s connector program is installed' : '', toolsBody],
    ['cloudflare', 'Cloudflare access', st.cloudflare ? `key saved in the keychain · sign-in page ${S.team_domain}` : '', cloudflareBody],
    ['domain', 'Domain', st.domain ? S.zone : '', domainBody],
    ['connect', 'Connect this Mac', st.connect ? 'connector installed; it starts by itself after a restart' : '', connectBody],
  ];
  const list = rows.map(([key, title, summary, body], i) => {
    const n = i + 1;
    if (st[key] && n !== step) return `<li class="cn-step"><div class="cn-step-row">${ICON.check}<span class="cn-step-text"><strong>${n} · ${title}</strong><span class="cn-step-note"> · ${esc(summary)}</span></span></div></li>`;
    if (n === step) return `<li class="cn-step cn-step--open"><div class="cn-step-row">${num(n)}<h2 class="cn-h3">${title}</h2></div><div class="cn-step-body">${body()}</div></li>`;
    return `<li class="cn-step cn-step--todo"><div class="cn-step-row">${ring}<span class="cn-step-text"><strong>${n} · ${title}</strong></span></div></li>`;
  }).join('');
  const done = S.setup_done ? `<section class="cn-card cn-card--decision"><h2 class="cn-h3">This Mac is connected</h2><p class="small" style="margin:0">Next: <a href="#doors">add your first door</a>.</p></section>` : '';
  return head('Set up', 'Connects this Mac to Cloudflare so your tools can have their own addresses behind a sign-in. About 15 minutes, once.') + done + `<ol class="cn-steps">${list}</ol>`;
}
function toolsBody() {
  if (!S.tools.brew) return `<div class="cn-callout cn-callout--warning">${ICON.warn}<span>Reach installs Cloudflare’s connector program through Homebrew, a free installer for Mac tools. Install it once:</span></div>
    <ol class="list small"><li>Open <a href="https://brew.sh" target="_blank" rel="noopener">brew.sh</a> and click the copy button next to the command under “Install Homebrew”.</li>
      <li>Open Terminal: Finder → Applications → Utilities → Terminal.</li>
      <li>Paste the command, press Return, and type your Mac password when asked (nothing shows while you type).</li>
      <li>When it says “Installation successful”, come back here and press <strong>Check again</strong>.</li></ol>
    <div class="row">${button('Check again', 'refresh', 'secondary')}</div>`;
  return `<p class="small" style="margin:0">Reach uses Cloudflare’s free connector program (cloudflared). It connects outward from this Mac, so no ports are opened.</p>
    <div class="row">${button('Install it', 'install_tools')}${button('Check again', 'refresh', 'secondary')}</div>`;
}
function cloudflareBody() {
  const rows = ['Account · Cloudflare Tunnel · Edit', 'Account · Access: Apps and Policies · Edit', 'Account · Access: Service Tokens · Edit',
    'Account · Access: Organizations, Identity Providers, and Groups · Edit', 'Zone · DNS · Edit'];
  return `<p class="small" style="margin:0">Your domain must already be on Cloudflare (Cloudflare → <strong>Add a domain</strong>, then switch the nameservers where you bought it). Any domain works.</p>
    <h3 class="cn-h3" style="margin:0">A · Switch on Cloudflare Zero Trust (once, free)</h3>
    <div class="row">${link('Open Cloudflare Zero Trust', 'https://one.dash.cloudflare.com/')}</div>
    <ol class="list small"><li>Pick your account if asked. On the welcome screen, choose a <strong>team name</strong> (any short word; it becomes your sign-in page address).</li>
      <li>Choose the <strong>Free</strong> plan (up to 50 people).</li>
      <li>Cloudflare asks for a payment card even on the Free plan; nothing is charged. Finish the checkout.</li></ol>
    <h3 class="cn-h3" style="margin:0">B · Make a key for Reach</h3>
    <div class="row">${link('Open Cloudflare’s key page', 'https://dash.cloudflare.com/profile/api-tokens')}</div>
    <ol class="list small"><li>Click <strong>Create Token</strong>, then next to “Create Custom Token” click <strong>Get started</strong>.</li>
      <li>Token name: <span class="kbd">reach</span></li>
      <li>Under Permissions, add these rows (use “+ Add more”):<ul>${rows.map((r) => `<li><strong>${r}</strong></li>`).join('')}</ul></li>
      <li>Account Resources: <strong>Include · your account</strong>. Zone Resources: <strong>Include · Specific zone · your domain</strong>.</li>
      <li>Click <strong>Continue to summary</strong>, then <strong>Create Token</strong>, and copy it. Cloudflare shows it only once.</li></ol>
    <div class="fields">${input('cf_token', 'Paste the key', { type: 'password', help: 'Saved in your Mac’s keychain, never in a file. Reach checks each permission and names any that are missing.' })}</div>
    <div class="fields">${input('account_id', 'Account ID (only if Reach asks for it)', { placeholder: '32 letters and digits' })}</div>
    <div class="row">${button('Check key and continue', 'save_key')}</div>`;
}
function domainBody() {
  if (!S.zones.length) return '<p class="small muted" style="margin:0">The key can’t see any domain yet. Fix the key’s Zone Resources, then press Check key again in step 2.</p>';
  return `<p class="small" style="margin:0">Doors will live at addresses like <strong>ai.${esc(S.zones[0].name)}</strong>.</p>
    <div class="cn-segmented" role="group" aria-label="Domain">${S.zones.map((z) => `<button type="button" aria-pressed="${field('zone_id', S.zones[0].id) === z.id}" data-zone="${esc(z.id)}">${esc(z.name)}</button>`).join('')}</div>
    <div class="row">${button('Use this domain', 'choose_zone')}</div>`;
}
function connectBody() {
  return `<p class="small" style="margin:0">Creates the connection at Cloudflare, keeps its key in your keychain, and starts the connector. It starts again by itself after a restart, even when Reach is closed. Nothing is reachable until you add a door.</p>
    <div class="row">${button('Connect this Mac', 'connect')}</div>`;
}

/* ------------------------------------------------------------------ drawing */
function renderMain() {
  const main = document.getElementById('main');
  const html = { doors: vDoors, setup: vSetup }[view()]();
  main.innerHTML = `<div id="job-slot">${jobPanel()}</div>` + html;
}
function render() {
  if (!S) return;
  const a = document.activeElement;
  if (a && a.matches && a.matches('input[type=text], input[type=password]') && document.getElementById('main').contains(a)) { renderNav(); renderJob(); return; }
  renderNav();
  renderMain();
}

/* ------------------------------------------------------------------ clicks and typing */
document.addEventListener('input', (e) => {
  const k = e.target.dataset && e.target.dataset.bind;
  if (k) ui.form[k] = e.target.value;
  const c = e.target.dataset && e.target.dataset.bindCheck;
  if (c) ui.form[c] = e.target.checked;
});
function valueOf(k) { const el = document.getElementById('f-' + k); return el ? el.value : (ui.form[k] || ''); }
const ARGS = {
  save_key: () => ({ token: valueOf('cf_token'), account_id: valueOf('account_id') }),
  choose_zone: () => ({ zone_id: field('zone_id', S.zones[0] && S.zones[0].id) }),
  door_add: () => ({ name: valueOf('name'), sub: valueOf('sub'), port: valueOf('port'), emails: valueOf('emails'), api: !!ui.form.api }),
  door_people: (a) => ({ emails: valueOf('emails_' + a.id) }),
  device_add: (a) => ({ name: valueOf('dev_' + a.id) }),
};
document.addEventListener('click', async (e) => {
  const t = e.target.closest('button, [data-copy]');
  if (!t) return;
  if (t.dataset.copy !== undefined) {
    try { await navigator.clipboard.writeText(t.dataset.copy); t.textContent = 'Copied'; setTimeout(() => { t.textContent = 'Copy'; }, 1500); }
    catch (err) { toast('Select the text and copy it with ⌘C.'); }
    return;
  }
  if (t.hasAttribute('data-quit')) {
    try { await api('/api/quit', {}); } catch (err) { /* already stopping */ }
    document.body.innerHTML = '<main class="cn-main" style="margin:auto"><h1 class="cn-h1">Reach has stopped</h1><p class="cn-lede">Your doors keep working: the connector runs on its own. Open Reach again from Applications to change them.</p></main>';
    return;
  }
  if (t.hasAttribute('data-dismiss')) { ui.jobDismissed = JOB.id; renderJob(); return; }
  if (t.hasAttribute('data-clear-key')) { ui.newKey = null; renderMain(); return; }
  if (t.dataset.toggleDoor) { ui.openDoor = ui.openDoor === t.dataset.toggleDoor ? '' : t.dataset.toggleDoor; renderMain(); return; }
  if (t.dataset.zone) { ui.form.zone_id = t.dataset.zone; renderMain(); return; }
  if (t.dataset.preset) {
    const p = JSON.parse(t.dataset.preset);
    Object.assign(ui.form, { name: p.name, sub: p.sub, port: String(p.port), api: p.api });
    renderMain(); const el = document.getElementById('f-emails'); if (el) el.focus();
    return;
  }
  const name = t.dataset.action;
  if (!name) return;
  if (t.dataset.confirm && !window.confirm(t.dataset.confirm)) return;
  if (name === 'refresh') { await refresh(true); toast('Checked again.'); return; }
  let a = t.dataset.args ? JSON.parse(t.dataset.args) : {};
  if (ARGS[name]) a = Object.assign(a, ARGS[name](a));
  act(name, a);
});

/* ------------------------------------------------------------------ keeping the screens current */
refresh(true);
setInterval(() => { if (!JOB || JOB.state !== 'running') refresh(false); }, 15000);
