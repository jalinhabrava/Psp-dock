'use strict';
// Live PSP Dock demo + real Chrome compositor test. No real hardware or user data.
// node tests/xmb_browser_motion.test.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const chrome = [process.env.PSP_DOCK_CHROME,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
].find(p => p && fs.existsSync(p));
assert(chrome, 'Chrome/Edge unavailable; set PSP_DOCK_CHROME');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'psp-xmb-live-'));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const finite = (value, message) => { assert(Number.isFinite(value), message); return value; };
const mid = (a, b, end) => Math.min(a, end) + 1 < b && b < Math.max(a, end) - 1;
const browserScript = `
  (async () => {
    const wait = () => new Promise(resolve => requestAnimationFrame(resolve));
    const sample = () => {
      const cats = document.querySelector('.xmb-cats');
      const row = document.querySelector('.xmb-item.on');
      return { cat: cats?.getBoundingClientRect().left, row: row?.getBoundingClientRect().top,
        catAnimations: cats?.getAnimations().length ?? 0,
        rowAnimations: row?.getAnimations().length ?? 0 };
    };
    const capture = async (button) => {
      const old = sample();
      const tracked = document.querySelector('.xmb-item.on');
      const trackedTop = () => tracked?.isConnected ? tracked.getBoundingClientRect().top : null;
      const oldTracked = trackedTop();
      const beforeCat = xmb.nav.cat;
      const beforeIdx = { ...xmb.nav.idx };
      xmbPress(button);
      const frames = [];
      const start = performance.now();
      while (performance.now() - start < 410) {
        await wait();
        frames.push({ ...sample(), tracked: trackedTop() });
      }
      return { button, old, oldTracked, frames, end: { ...sample(), tracked:trackedTop() }, beforeCat, afterCat: xmb.nav.cat,
        beforeIdx, afterIdx: { ...xmb.nav.idx } };
    };
    await wait(); await wait();
    if (!document.querySelector('.xmb-item.on') || document.querySelector('#xmb-menu')?.hidden)
      throw new Error('Real XMB demo menu was not ready');
    const initial = { reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,
      configured:xmb.motion, connection:xmbConnectionView().waiting, section:xmb.sections[xmb.nav.cat]?.id };
    const right = await capture('right');
    await capture('left');
    const down = await capture('down');
    return { initial, right, down };
  })()`;

async function main() {
  const server = spawn(process.env.PSP_DOCK_PYTHON || 'python', ['-u', 'dock.py', '--demo', '--no-browser', '--port', '0', '--data', path.join(temp, 'demo')],
    { cwd:path.resolve(__dirname, '..'), windowsHide:true, stdio:['ignore', 'pipe', 'pipe'] });
  const profile = path.join(temp, 'profile');
  let browser, socket, stderr = '';
  try {
    server.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-3000); });
    let output = '';
    server.stdout.on('data', chunk => { output += chunk.toString(); });
    let url;
    for (let i = 0; i < 200; i++) {
      url = output.match(/PSP Dock: (http:\/\/127\.0\.0\.1:\d+)/)?.[1];
      if (url) break;
      assert(server.exitCode === null, 'Demo server exited: ' + stderr);
      await sleep(50);
    }
    assert(url, 'Timed out waiting for isolated demo URL: ' + stderr);
    const browserFlags = ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
      '--disable-background-networking','--disable-extensions','--remote-debugging-port=0',
      '--user-data-dir=' + profile, url];
    browser = spawn(chrome, browserFlags, { windowsHide:true, stdio:'ignore' });
    let port;
    for (let i = 0; i < 120; i++) {
      const file = path.join(profile, 'DevToolsActivePort');
      if (fs.existsSync(file)) { port = fs.readFileSync(file, 'utf8').split(/\r?\n/)[0]; break; }
      await sleep(50);
    }
    assert(port, 'Chrome debugging endpoint did not start');
    let page;
    for (let i = 0; i < 100; i++) {
      const pages = await fetch(`http://127.0.0.1:${port}/json/list`).then(r => r.json());
      page = pages.find(p => p.type === 'page' && p.url.startsWith(url));
      if (page) break;
      await sleep(50);
    }
    assert(page, 'Actual PSP Dock demo page did not open in Chrome');
    socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once:true });
      socket.addEventListener('error', reject, { once:true });
    });
    let sequence = 0;
    const pending = new Map();
    socket.addEventListener('message', e => {
      const reply = JSON.parse(e.data), entry = pending.get(reply.id);
      if (!entry) return;
      pending.delete(reply.id);
      reply.error ? entry.reject(new Error(JSON.stringify(reply.error))) : entry.resolve(reply.result);
    });
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async expression => {
      const response = await send('Runtime.evaluate', { expression, awaitPromise:true, returnByValue:true });
      assert(!response.exceptionDetails, JSON.stringify(response.exceptionDetails));
      return response.result?.value;
    };
    const ready = async () => {
      for (let i = 0; i < 100; i++) {
        const value = await evaluate(`typeof xmbPress === 'function' && xmb.sections.length > 0 && !document.querySelector('#xmb-menu').hidden && document.querySelector('.xmb-item.on') !== null`);
        if (value) return;
        await sleep(60);
      }
      throw new Error('Live demo XMB did not become navigable');
    };
    const inspect = result => {
      assert.equal(result.initial.connection, false, 'Demo unexpectedly requires hardware');
      assert.equal(result.right.afterCat, result.right.beforeCat + 1, 'Right did not change actual app category');
      const cat = result.right;
      const row = result.down;
      const smoothX = cat.frames.some(f => mid(cat.old.cat, f.cat, cat.end.cat));
      const smoothY = row.frames.some(f => Number.isFinite(f.tracked) && mid(row.oldTracked, f.tracked, row.end.tracked));
      return { smoothX, smoothY, category:cat.beforeCat+'→'+cat.afterCat,
        horizontal:{ old:cat.old.cat, middle:cat.frames.find(f => mid(cat.old.cat, f.cat, cat.end.cat))?.cat, end:cat.end.cat },
        vertical:{ old:row.oldTracked, middle:row.frames.find(f => Number.isFinite(f.tracked) && mid(row.oldTracked, f.tracked, row.end.tracked))?.tracked, end:row.end.tracked, beforeIdx:row.beforeIdx, afterIdx:row.afterIdx },
        section:result.initial.section, reduced:result.initial.reduced, configured:result.initial.configured };
    };
    await ready();
    await send('Emulation.setEmulatedMedia', { features:[{ name:'prefers-reduced-motion', value:'no-preference' }] });
    await evaluate(`localStorage.setItem('psp-dock-xmb-motion','on'); xmb.motion='on'`);
    let result = inspect(await evaluate(browserScript));
    console.log('Actual PSP Dock demo XMB motion:', JSON.stringify(result));
    assert.equal(result.configured, 'on');
    assert(result.smoothX, 'Real xmbPress(right) did not animate intermediate horizontal positions');
    assert(result.smoothY, 'Real xmbPress(down) did not animate intermediate vertical positions');
    // Run the same real application navigation under accessibility reduce preference.
    await send('Emulation.setEmulatedMedia', { features:[{ name:'prefers-reduced-motion', value:'reduce' }] });
    await send('Page.reload', { ignoreCache:true });
    await ready();
    result = inspect(await evaluate(browserScript));
    console.log('Actual PSP Dock demo reduced OS / motion ON:', JSON.stringify(result));
    assert.equal(result.reduced, true);
    assert.equal(result.configured, 'on');
    assert.equal(result.smoothX, true, 'Explicit motion ON must animate horizontal under OS reduce');
    assert.equal(result.smoothY, true, 'Explicit motion ON must animate vertical under OS reduce');
    await evaluate(`localStorage.setItem('psp-dock-xmb-motion','system')`);
    await send('Page.reload', { ignoreCache:true });
    await ready();
    result = inspect(await evaluate(browserScript));
    console.log('Actual PSP Dock demo reduced OS / motion SYSTEM:', JSON.stringify(result));
    assert.equal(result.configured, 'system');
    assert.equal(result.smoothX, false, 'Reduced-motion horizontal still interpolates');
    assert.equal(result.smoothY, false, 'Reduced-motion vertical still interpolates');
    console.log('Real PSP Dock demo horizontal + vertical rendering: OK');
  } finally {
    socket?.close();
    for (const child of [browser, server]) {
      if (!child) continue;
      if (child.exitCode === null) child.kill();
      await Promise.race([new Promise(resolve => child.once('exit', resolve).once('error', resolve)), sleep(3000)]);
    }
    try { fs.rmSync(temp, { recursive:true, force:true, maxRetries:12, retryDelay:250 }); }
    catch (error) { console.error('Isolated test directory cleanup:', error.message); }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
