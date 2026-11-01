// Pure tests of the XMB navigation model (sections left/right, items
// up/down, sub-menus, △ menu and Sí/No). No browser and no PSP.
// Run: node tests/xmb_navigation_model.test.js
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const code = fs.readFileSync(path.resolve(__dirname, '../web/app.js'), 'utf8');
const start = code.indexOf('function xmbStep(');
const end = code.indexOf('// ==== fin del modelo puro ====', start);
assert(start > 0 && end > start, 'Expected the pure XMB model block in app.js');
const scope = {};
vm.createContext(scope);
vm.runInContext(code.slice(start, end), scope);
const { xmbStep, xmbMonthColors } = scope;

const view = { sections: [
  { id: 'ajustes', count: 6 }, { id: 'dock', count: 3 }, { id: 'juegos', count: 4 },
  { id: 'partidas', count: 2 }, { id: 'graficos', count: 5 }] };
let nav = { cat: 2, idx: {}, level: 'list', subIdx: 0, side: null, confirm: null };
const step = (button, extra = {}) => { const r = xmbStep(nav, { ...view, ...extra }, button); nav = r.nav; return r.effect; };

// Left/right change section and stop at the ends, like the PSP.
step('right'); step('right'); step('right');
assert.equal(nav.cat, 4, 'Right stops at the last section');
step('left'); step('left'); step('left'); step('left'); step('left');
assert.equal(nav.cat, 0, 'Left stops at the first section');
step('start');
assert.equal(nav.cat, 2, 'START returns to Juegos');

// Up/down move within the section and each section remembers its item.
step('down'); step('down'); step('down'); step('down'); step('down');
assert.equal(nav.idx.juegos, 3, 'Down stops at the last game');
step('up');
assert.equal(nav.idx.juegos, 2);
step('right'); step('down');
assert.equal(nav.idx.partidas, 1);
step('left');
assert.equal(nav.idx.juegos, 2, 'Each section keeps its own position');
step('pageLeft');
assert.equal(nav.idx.juegos, 0, 'L jumps back up to 5 items');

// ✕ activates, △ asks for the item menu, SELECT asks for Salir del Dock.
assert.deepEqual({ ...step('accept') }, { type: 'activate', section: 'juegos', index: 0 });
assert.deepEqual({ ...step('menu') }, { type: 'menu', section: 'juegos', index: 0 });
assert.equal(step('select').type, 'select');
assert.equal(xmbStep({ ...nav, cat: 1 }, { sections: [{ id: 'a', count: 0 }, { id: 'b', count: 0 }] }, 'accept').effect, null,
  'An empty section never activates anything');

// Side menu (△ or a value column) wraps and only picks on ✕.
nav = { ...nav, side: { i: 0 } };
step('up', { sideCount: 3 });
assert.equal(nav.side.i, 2, 'Side menu wraps upwards');
step('down', { sideCount: 3 });
assert.equal(nav.side.i, 0);
assert.equal(step('left', { sideCount: 3 }), null);
assert.equal(nav.side, null, '○/◀ closes the side menu without choosing');
nav = { ...nav, side: { i: 1 } };
assert.deepEqual({ ...step('accept', { sideCount: 3 }) }, { type: 'pick', index: 1 });
assert.equal(nav.side, null);

// Sí/No: ○ always cancels, ✕ returns the highlighted answer.
nav = { ...nav, confirm: { yes: false } };
assert.deepEqual({ ...step('accept') }, { type: 'confirm', yes: false }, 'Default No stays No on ✕');
nav = { ...nav, confirm: { yes: false } };
step('left');
assert.deepEqual({ ...step('accept') }, { type: 'confirm', yes: true });
nav = { ...nav, confirm: { yes: true } };
assert.deepEqual({ ...step('back') }, { type: 'confirm', yes: false }, '○ never confirms');
nav = { ...nav, confirm: { yes: true } };
assert.equal(step('menu'), null, '△ is ignored inside Sí/No');
assert.deepEqual(nav.confirm, { yes: true });
nav = { ...nav, confirm: null };

// Per-game graphics sub-level: up/down rows, ✕/▶ edits, ○/◀ goes back.
nav = { ...nav, cat: 4, level: 'sub', subIdx: 0 };
step('down', { subCount: 4 }); step('down', { subCount: 4 }); step('down', { subCount: 4 }); step('down', { subCount: 4 });
assert.equal(nav.subIdx, 3);
assert.deepEqual({ ...step('right', { subCount: 4 }) }, { type: 'row', index: 3 });
step('back', { subCount: 4 });
assert.equal(nav.level, 'list');
assert.equal(nav.cat, 4, 'Leaving a game profile keeps the Gráficos section');

// Month colours follow the PSP idea: one hue per month, darker at night.
const noon = xmbMonthColors(10, 13), night = xmbMonthColors(10, 1);
assert.notEqual(xmbMonthColors(0, 13).top, xmbMonthColors(11, 13).top);
const lum = (hex) => parseInt(hex.slice(1, 3), 16) + parseInt(hex.slice(3, 5), 16) + parseInt(hex.slice(5, 7), 16);
assert(lum(noon.top) > lum(night.top), 'Night is darker than midday');
assert.match(noon.top, /^#[0-9a-f]{6}$/);

// Safety rules from the old launcher are kept in the XMB wiring.
assert(code.includes("xmbConfirm('¿Salir del Dock?"), 'Salir del Dock asks Sí/No');
assert(code.includes("!(dialog.id === 'transfer-dialog' && ui.transferInFlight)"), '○ cannot hide an active copy');
assert(code.includes("const button = edge('exit') ? 'select'"), 'SELECT only moves to the exit item');

console.log('PSP XMB navigation model tests: OK');
