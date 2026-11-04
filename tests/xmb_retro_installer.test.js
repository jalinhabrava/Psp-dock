'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.resolve(__dirname, '../web/app.js'), 'utf8');
const css = fs.readFileSync(path.resolve(__dirname, '../web/style.css'), 'utf8');
const html = fs.readFileSync(path.resolve(__dirname, '../web/index.html'), 'utf8');
const start = source.indexOf('function xmbRetroInstallState(');
const end = source.indexOf('\nfunction xmbBuildSections(', start);
const scope = {
  ui: { state: { retro_install: {
    policy: 'ask', needed: true, external: false, status: 'pending',
    progress: { active: false, phase: 'idle' },
  } }, serviceOffline: false },
  xmb: { settings: { retro_install_policy: 'ask' } },
  requests: [], messages: [],
  request: async (url) => { scope.requests.push(url); return {}; },
  refresh: async () => {},
  toast: (message) => scope.messages.push(message),
};
vm.createContext(scope);
vm.runInContext(source.slice(start, end), scope);
(async () => {
  assert(!source.includes('retro-installer'), 'Retro category opens with actual emulators, not an installer row');
  assert(!source.includes('Conecta la PSP en modo almacenamiento USB Sony para autorizar los emuladores'), 'PC installation never prompts USB');
  assert(html.includes('name="retro_install_policy"'));
  assert(html.includes('value="auto"') && html.includes('value="ask"') && html.includes('value="off"'));
  assert(css.includes('.xmb-install-ring'));
  assert(css.includes('@media(prefers-reduced-motion:reduce){.xmb-install-ring{animation:none'));

  await scope.xmbStartRetroInstall();
  assert.deepEqual(scope.requests, ['/api/install-retro'], 'ask policy allows installing in background on explicit emulator click');
  assert(!scope.ui.busy, 'background download does not lock XMB navigation');

  scope.requests.length = 0;
  scope.ui.state.retro_install.policy = 'off';
  await scope.xmbStartRetroInstall();
  assert.equal(scope.requests.length, 0, 'off policy is explicit opt-out');

  scope.ui.state.retro_install.policy = 'auto';
  scope.ui.state.retro_install.progress = { active: true, phase: 'downloading', bytes_total: 1000, bytes_done: 310, item: 'RetroArch' };
  assert.equal(scope.xmbRetroInstallState().percent, 31);
  assert.equal(scope.xmbRetroInstallState().active, true);
  await scope.xmbStartRetroInstall();
  assert.equal(scope.requests.length, 0, 'active download never starts in parallel');
  scope.ui.state.retro_install.progress.active = false;
  scope.ui.state.retro_install.external = true;
  await scope.xmbStartRetroInstall();
  assert.equal(scope.requests.length, 0, 'external RetroArch is never modified');
  console.log('XMB RetroArch install: PC-only, background progress, policy and external guards OK');
})().catch((error) => { console.error(error); process.exitCode = 1; });
