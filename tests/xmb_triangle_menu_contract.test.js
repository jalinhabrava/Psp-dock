// TRIANGLE displays a right-side menu. Copy only after explicit selection.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync(path.resolve(__dirname,'../web/app.js'),'utf8');
const start = code.indexOf('function xmbGameMenu(game)');
const end = code.indexOf('\nfunction xmbGoto(', start);
assert(start > 0 && end > start);
const calls = [];
const context = {
  xmb: {gfx:{}},
  xmbGfxSummary: () => 'Automático',
  bytes: (n) => String(n),
  xmbLoadGraphics: () => calls.push('prefetch graphics'),
  xmbSide: (title, options, choose) => {
    calls.push('open RIGHT side');
    context.choose=choose;
    context.options=options;
  },
  xmbLaunch: () => calls.push('launch'),
  xmbQueueMissingGame: () => calls.push('queue after explicit selection'),
  xmbOpenGraphics: () => calls.push('graphics'),
  xmbGoto: () => calls.push('saves'),
  toast: () => calls.push('info'),
  ui: {selected:null},
  renderSelected: () => {},
  xmbText: () => '',
};
vm.createContext(context);
vm.runInContext(code.slice(start,end),context);
const game={id:'a'.repeat(24),title:'Missing game',format:'ISO',
  size:123,relative:'ISO/x.iso',availability:'missing'};
context.xmbGameMenu(game);
assert.equal(calls.includes('open RIGHT side'),true);
assert.equal(calls.includes('queue after explicit selection'),false);
assert.equal(context.options[0].t,'Sincronizar con Dock');
context.choose(1);
assert.equal(calls.includes('queue after explicit selection'),false);
context.choose(0);
assert.equal(calls.filter(v=>v==='queue after explicit selection').length,1);
const ready={...game,availability:'ready'};
context.xmbGameMenu(ready);
assert.equal(context.options[0].t,'Jugar');
context.choose(0);
assert.equal(calls.filter(v=>v==='launch').length,1);
assert(code.includes('menu: () => xmbGameMenu(game)'));
console.log('TRIANGLE opens XMB right-side menu; copy requires explicit selection: OK');
