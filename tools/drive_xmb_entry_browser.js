// This driver connects only to the temporary browser created by the Python fixture.
'use strict';
const [port, url] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
  const target = targets.find(item => item.type === 'page' && item.url === url);
  if (!target) throw Error('The isolated entry page was not found');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const requests = new Map();
  let serial = 0;
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data), pending = requests.get(message.id);
    if (!pending) return;
    requests.delete(message.id);
    clearTimeout(pending.timer);
    message.error ? pending.reject(Error(JSON.stringify(message.error))) : pending.resolve(message.result);
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => { requests.delete(id); reject(Error('CDP timeout: ' + method)); }, 5000);
    requests.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async fn => {
    const result = await call('Runtime.evaluate', {
      expression: '(' + fn.toString() + ')()', returnByValue: true,
    });
    if (result.exceptionDetails) throw Error(result.exceptionDetails.text);
    return result.result.value;
  };
  const until = async fn => {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const result = await evaluate(fn);
      if (result) return result;
      await sleep(50);
    }
    throw Error('The expected browser phase did not arrive');
  };
  try {
    const initial = await until(() => {
      if (typeof ui === 'undefined' || ui.state?.product_flow?.phase !== 'choosing' || ui.busy) return false;
      const panel = document.getElementById('dock-session');
      const button = document.querySelector('[data-session-choice="none"]');
      const menu = document.getElementById('xmb-menu');
      if (!button || button.disabled || panel.hidden) return false;
      button.scrollIntoView({ block: 'center' });
      const bounds = button.getBoundingClientRect();
      return {
        selectorVisible: bounds.width > 0 && bounds.height > 0 && !panel.hidden,
        menuHidden: menu.hidden && menu.inert,
        buttonText: button.textContent.trim(),
        x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2,
      };
    });
    // Exercise the real DOM listener and normal mutate()/refresh() path.
    await call('Input.dispatchMouseEvent', { type: 'mousePressed', x: initial.x, y: initial.y, button: 'left', clickCount: 1 });
    await call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: initial.x, y: initial.y, button: 'left', clickCount: 1 });
    await until(() => typeof ui !== 'undefined' && ui.state?.product_flow?.phase === 'ready' && !ui.busy && !ui.refreshPending);
    const final = await evaluate(() => {
      const view = xmbConnectionView();
      const menu = document.getElementById('xmb-menu');
      const bounds = menu.getBoundingClientRect();
      const list = document.getElementById('xmb-list');
      return {
        phase: ui.state.product_flow.phase, choice: ui.state.product_flow.choice,
        selectedDevice: ui.state.selected_device, controllerPhase: ui.state.controller_status.phase,
        screen: view.screen, nativeController: view.nativeController, canPlay: view.canPlay,
        menuVisible: !menu.hidden && !menu.inert && menu.getAttribute('aria-hidden') === 'false' &&
          getComputedStyle(menu).display !== 'none' && bounds.width > 0 && bounds.height > 0,
        welcomeHidden: document.getElementById('dock-connect').hidden,
        selectorHidden: document.getElementById('dock-session').hidden,
        catalogVisible: !menu.hidden && !menu.inert && list.getBoundingClientRect().height > 0 &&
          list.textContent.includes('Synthetic entry test'),
        catalogText: list.textContent.trim(),
      };
    });
    const outcome = await (await fetch(url + 'fixture/outcome')).json();
    const requestVerified = outcome.choice_posts.length === 1 &&
      outcome.choice_posts[0].choice === 'none' && outcome.choice_posts[0].offer_id === 'isolated-entry-offer' &&
      outcome.choice_posts[0].automatic === false && outcome.unexpected_posts.length === 0;
    const passed = initial.selectorVisible && initial.menuHidden && requestVerified &&
      final.phase === 'ready' && final.choice === 'none' && final.selectedDevice === null && final.controllerPhase === 2 &&
      final.screen === 'menu' && final.nativeController && final.canPlay && final.menuVisible &&
      final.welcomeHidden && final.selectorHidden && final.catalogVisible;
    console.log(JSON.stringify({
      passed: !!passed, initial, final, requestVerified, ...outcome,
      failure_reason: passed ? null : 'A confirmed native READY after no-copy must reveal the usable XMB menu',
    }));
  } finally {
    // The profile and CDP endpoint belong only to this isolated test.
    socket.send(JSON.stringify({ id: ++serial, method: 'Browser.close' }));
    await sleep(150);
    socket.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
