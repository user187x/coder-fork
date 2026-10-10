// Browser end-to-end test of an installed coder-platform (./e2e-kind up + install): Keycloak sign-in, owner role,
// every dashboard add-on, a certificate upload, and a workspace built from the air-gap image.
//   node browser-e2e.mjs <files dir readable by chromium> [shots dir]
// Env: ADDR (127.0.0.2), KC_ADDR (Keycloak elsewhere, e.g. 127.0.0.3), DOMAIN (cp.test), KC_USER/KC_PASS, CHROME
import { spawn, execFileSync } from 'node:child_process';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';

const FILES = process.argv[2]; const SHOTS = process.argv[3] || FILES;
const ADDR = process.env.ADDR || '127.0.0.2'; const DOMAIN = process.env.DOMAIN || 'cp.test';
const BASE = `https://coder.${DOMAIN}`;
const USER = process.env.KC_USER || 'jdoe'; const PASS = process.env.KC_PASS || 'e2e-Passw0rd';
mkdirSync(SHOTS, { recursive: true });
if (!existsSync(`${FILES}/e2e.p12`)) {
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30', '-subj', `/CN=${USER}`, '-keyout', `${FILES}/e2e.key`, '-out', `${FILES}/e2e.crt`], { stdio: 'ignore' });
  execFileSync('openssl', ['pkcs12', '-export', '-inkey', `${FILES}/e2e.key`, '-in', `${FILES}/e2e.crt`, '-passout', 'pass:e2e', '-out', `${FILES}/e2e.p12`], { stdio: 'ignore' });
}
const chrome = spawn(process.env.CHROME || 'chromium', ['--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=9361',
  '--window-size=1440,1000', `--host-resolver-rules=${process.env.KC_ADDR ? `MAP keycloak.${DOMAIN} ${process.env.KC_ADDR}, ` : ''}MAP *.${DOMAIN} ${ADDR}`, '--ignore-certificate-errors', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws, id = 0; const pending = new Map(); const errors = []; const results = []; const dialogs = [];
const check = (name, ok, extra = '') => { results.push(!!ok); console.log(`${ok ? 'OK  ' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`); };
try {
  let t; for (let i = 0; i < 40; i++) { try { t = await (await fetch('http://127.0.0.1:9361/json')).json(); break; } catch { await sleep(250); } }
  ws = new WebSocket(t.find((x) => x.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') errors.push((m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text || '').split('\n')[0]);
    if (m.method === 'Page.javascriptDialogOpening') { dialogs.push(m.params.message); send('Page.handleJavaScriptDialog', { accept: true }); }
  });
  const ev = async (x) => { const r = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true }); return r.result.exceptionDetails ? 'EVAL ERROR ' + r.result.exceptionDetails.exception?.description : r.result.result?.value; };
  const waitFor = async (x, ms = 15000) => { for (let i = 0; i < ms; i += 250) { const v = await ev(x); if (v && !(typeof v === 'string' && v.startsWith('EVAL ERROR'))) return true; await sleep(250); } return false; };
  const shot = async (n) => { const r = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(`${SHOTS}/${n}.png`, Buffer.from(r.result.data, 'base64')); };
  const api = (path, method = 'GET', body) => ev(`fetch(${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'Content-Type':'application/json','X-CSRF-TOKEN':document.querySelector('meta[property="csrf-token"]')?.content||''},body:${body ? JSON.stringify(JSON.stringify(body)) : 'undefined'}}).then(async r=>({status:r.status,body:await r.json().catch(()=>null)}))`);
  const links = `[...document.querySelectorAll('a')].map(a=>a.textContent.trim())`;
  const click = (label) => ev(`(()=>{const a=[...document.querySelectorAll('a')].find(a=>a.textContent.trim()===${JSON.stringify(label)}&&a.getAttribute('href')?.startsWith('/'));if(!a)return 'no link';a.click();return a.getAttribute('href')})()`);
  await send('Runtime.enable'); await send('Page.enable'); await send('DOM.enable');

  // ---- Keycloak sign-in
  await send('Page.navigate', { url: `${BASE}/login` });
  // the OIDC button (its label is the installation's CODER_OIDC_SIGN_IN_TEXT)
  const OIDC = `[...document.querySelectorAll('a,button')].find(e=>/oidc/.test(e.getAttribute('href')||'') || /^Sign in with/.test(e.textContent.trim()))`;
  check('Coder sign-in page offers Keycloak only (OIDC button, no password form)',
    await waitFor(`!!${OIDC} && !document.querySelector('input[type=password]')`, 30000), await ev(`${OIDC}?.textContent`));
  await shot('01-login');
  await ev(`${OIDC}.click()`);
  check('redirected to Keycloak', await waitFor(`location.host==='keycloak.${DOMAIN}' && !!document.querySelector('#username')`, 20000), await ev('location.href'));
  await ev(`(()=>{document.querySelector('#username').value=${JSON.stringify(USER)};document.querySelector('#password').value=${JSON.stringify(PASS)};document.querySelector('#kc-login, [type=submit]').click();return 1})()`);
  check('back in Coder, signed in', await waitFor(`location.host==='coder.${DOMAIN}' && !location.pathname.startsWith('/login')`, 30000), await ev('location.href'));
  await waitFor(`!!document.querySelector('meta[property="csrf-token"]')`, 10000);
  const me = await api('/api/v2/users/me');
  check(`${USER} is an owner (pre-created by the bootstrap Job, linked on first Keycloak sign-in)`, me.body?.roles?.some((r) => r.name === 'owner') && me.body?.login_type === 'oidc', JSON.stringify({ roles: me.body?.roles?.map((r) => r.name), login: me.body?.login_type }));

  // ---- dashboard add-ons
  await send('Page.navigate', { url: `${BASE}/deployment/overview` });
  await waitFor(`${links}.includes('Announcement')`, 20000);
  const side = await ev(`[...document.querySelectorAll('section a[href^="/deployment"], section summary')].map(a=>a.textContent.trim()).filter(Boolean)`);
  const want = ['Overview', 'Announcement', 'Classification', 'Change Logo', 'Monitoring', 'User Authentication', 'Keycloak', 'AI›', 'Network', 'Security', 'Observability', 'Users', 'Notifications'];
  check('General sidebar has every add-on, in order', want.every((w, i) => side.indexOf(w) >= 0 && (i === 0 || side.indexOf(w) > side.indexOf(want[i - 1]))), JSON.stringify(side));
  check('removed items are gone', !['Licenses', 'Workspace Proxies', 'Groups', 'IdP Organization Sync', 'Premium', 'Trial Upgrade', 'Appearance', 'External Authentication'].some((g) => side.includes(g)));
  await shot('02-general');
  for (const [label, needle] of [['Announcement', 'srcdoc'], ['Classification', 'classification.html'], ['Keycloak', 'keycloak.html'], ['Change Logo', 'logo.html'], ['Monitoring', 'monitoring.html']]) {
    await click(label);
    const ok = await waitFor(`(()=>{const v=document.querySelector('.coder-ui-view');return v && v.querySelector('h1')?.textContent===${JSON.stringify(label)} && (${JSON.stringify(needle)}==='srcdoc' ? v.querySelector('iframe')?.hasAttribute('srcdoc') : (v.querySelector('iframe')?.getAttribute('src')||'').includes(${JSON.stringify(needle)}))})()`, 15000);
    check(`General > ${label} opens`, ok);
    if (label === 'Keycloak') { await sleep(4000); await shot('03-keycloak'); }
  }
  // the Keycloak view reads Coder's settings from the chart's ConfigMap (envFrom)
  const kc = await ev(`document.querySelector('.coder-ui-view iframe')?.contentDocument?.body?.innerText || ''`);
  await click('Keycloak'); await sleep(5000);
  const kcText = await ev(`document.querySelector('.coder-ui-view iframe')?.contentDocument?.body?.innerText || ''`);
  check("Keycloak view reads Coder's settings from the chart (issuer, realm cp)", /Realm\s+cp\b/.test(kcText) && /coder-platform chart|Coder's own Helm release/.test(kcText) && !/Coder has no issuer URL/.test(kcText), kcText.replace(/\s+/g, ' ').slice(0, 300));
  await send('Page.navigate', { url: `${BASE}/workspaces` });
  await waitFor(`[...document.querySelectorAll('button')].some(b=>/Admin settings/.test(b.textContent))`, 20000);
  await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/Admin settings/.test(b.textContent));b.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'}));b.click();return 1})()`);
  await sleep(800);
  const admin = await ev(`[...document.querySelectorAll('[role=menu] [role=menuitem], [role=menu] a')].map(e=>e.textContent.trim())`);
  check('Admin settings: General + Users, no Deployment / AI', Array.isArray(admin) && admin.includes('General') && admin.includes('Users') && !admin.includes('Deployment') && !admin.includes('AI'), JSON.stringify(admin));
  await ev(`document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
  await send('Page.navigate', { url: `${BASE}/health` });
  // the Health page renders after Coder's health report, which takes a while where its internet probes time out
  check('Health > Workspace Health', await waitFor(`${links}.includes('Workspace Health')`, 90000));
  const banner = await ev(`!!document.querySelector('#coder-banner, [id*=banner]')`);
  check('announcement banner is shown', banner);

  // ---- Settings > Certificates, then a workspace that gets it
  await send('Page.navigate', { url: `${BASE}/settings/certificates` });
  const D = `document.querySelector('.coder-ui-view iframe').contentDocument`;
  check(`Certificates page targets ~/.cert/${USER}.p12`, await waitFor(`${D}?.getElementById('target')?.textContent==='~/.cert/${USER}.p12'`, 20000));
  await ev(`${D}.getElementById('add').click()`);
  const doc = await send('DOM.getDocument', { depth: -1, pierce: true });
  const ifr = await send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: '.coder-ui-view iframe' });
  const fdoc = (await send('DOM.describeNode', { nodeId: ifr.result.nodeId, depth: 1, pierce: true })).result.node.contentDocument;
  const fnode = (await send('DOM.pushNodesByBackendIdsToFrontend', { backendNodeIds: [fdoc.backendNodeId] })).result.nodeIds[0];
  const input = await send('DOM.querySelector', { nodeId: fnode, selector: '#file' });
  await send('DOM.setFileInputFiles', { nodeId: input.result.nodeId, files: [`${FILES}/e2e.p12`] });
  await waitFor(`!${D}.getElementById('save').disabled`);
  await ev(`${D}.getElementById('save').click()`);
  check('certificate uploaded', await waitFor(`${D}.getElementById('list').innerText.includes('~/.cert/${USER}.p12')`, 15000));

  const tpl = await api('/api/v2/organizations/default/templates/ubuntu-vnc-desktop');
  check('workspace template pushed by the bootstrap Job', tpl.status === 200, `HTTP ${tpl.status}`);
  const created = await api('/api/v2/organizations/default/members/me/workspaces', 'POST', { template_id: tpl.body?.id, name: `e2e-${Date.now().toString(36)}` });
  check(`workspace ${created.body?.name} created`, created.status === 201, `HTTP ${created.status} ${JSON.stringify(created.body)?.slice(0, 200)}`);
  let wsp, agent;
  for (let i = 0; i < 180; i++) {   // up to 15 minutes: the first pull of the workspace image takes a while
    wsp = (await api(`/api/v2/workspaces/${created.body?.id}`)).body;
    agent = wsp?.latest_build?.resources?.flatMap((r) => r.agents || [])[0];
    if (wsp?.latest_build?.job?.status === 'failed' || agent?.lifecycle_state === 'ready' || /error|timeout/.test(agent?.lifecycle_state || '')) break;
    await sleep(5000);
  }
  check('workspace built offline (Terraform from the provider mirror)', wsp?.latest_build?.job?.status === 'succeeded', `${wsp?.latest_build?.job?.status} ${wsp?.latest_build?.job?.error || ''}`.slice(0, 300));
  check('agent ready (startup script + Certificates script ran with no internet)', agent?.lifecycle_state === 'ready', agent?.lifecycle_state);
  await sleep(20000);   // app health checks run every few seconds after start
  const apps = (await api(`/api/v2/workspaces/${created.body?.id}`)).body?.latest_build?.resources?.flatMap((r) => r.agents || [])[0]?.apps || [];
  const health = Object.fromEntries(apps.map((a) => [a.slug, a.health]));
  check('workspace apps healthy: code-server, File Browser, marimo, noVNC', ['code-server', 'filebrowser', 'marimo', 'novnc'].every((s) => health[s] === 'healthy'), JSON.stringify(health));
  await send('Page.navigate', { url: `${BASE}/@${USER}/${created.body?.name}` });
  await sleep(6000); await shot('04-workspace');
  console.log(`WORKSPACE_ID=${created.body?.id}`);

  // Group-restricted sign-in (CODER_OIDC_ALLOWED_GROUPS): a Keycloak user outside the allowed group is refused.
  if (process.env.DENIED_USER) {
    await send('Network.enable'); await send('Network.clearBrowserCookies');
    await send('Page.navigate', { url: `${BASE}/login` });
    await waitFor(`!!${OIDC}`, 20000);
    await ev(`${OIDC}.click()`);
    await waitFor(`location.host==='keycloak.${DOMAIN}' && !!document.querySelector('#username')`, 20000);
    await ev(`(()=>{document.querySelector('#username').value=${JSON.stringify(process.env.DENIED_USER)};document.querySelector('#password').value=${JSON.stringify(PASS)};document.querySelector('#kc-login, [type=submit]').click();return 1})()`);
    await waitFor(`location.host==='coder.${DOMAIN}'`, 30000); await sleep(3000);
    const me2 = await ev(`fetch('/api/v2/users/me').then(r=>r.status)`);
    const page = await ev(`document.body.innerText.slice(0,300)`);
    check(`${process.env.DENIED_USER} (not in the allowed Keycloak group) is refused`, me2 === 401, `HTTP ${me2}: ${String(page).replace(/\s+/g, ' ').slice(0, 160)}`);
    await shot('05-denied');
  }

  const errs = [...new Set(errors)].filter((e) => !/ServiceWorker|WebSocket|Failed to fetch|NetworkError|AbortError/.test(e));
  check('no JavaScript errors', errs.length === 0, errs.join(' | '));
  console.log(results.every(Boolean) ? 'ALL PASSED' : `${results.filter((r) => !r).length} FAILED`);
} finally {
  ws?.close(); chrome.kill();
}
