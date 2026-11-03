'use strict';
// The XMB must retain boundary nodes long enough for CSS to slide them away.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../web/app.js'), 'utf8');
const css = fs.readFileSync(path.resolve(__dirname, '../web/style.css'), 'utf8');
const start = source.indexOf('function xmbPlaceColumn(');
const end = source.indexOf('\nfunction xmbRender()', start);
assert(start > 0 && end > start);

const nodes = new Map();
const list = { append(node) { node.parentNode = this; } };
const xmb = { nav: { cat: 0, idx: { juegos: 4 } }, sections: [{ id: 'juegos' }] };
const scope = {
  xmb, $: () => list,
  xmbItemNode(key) {
    if (!nodes.has(key)) nodes.set(key, {
      style: {}, dataset: {}, classList: { toggle() {} },
      setAttribute() {},
    });
    return nodes.get(key);
  },
};
vm.createContext(scope);
vm.runInContext(source.slice(start, end), scope);
const items = Array.from({ length: 30 }, (_, i) => ({ key: String(i) }));
const first = new Set();
scope.xmbPlaceColumn('main:juegos:', items, 4, 20, false, first);
assert(first.has('main:juegos:1'), 'Upper edge stays mounted one step outside the visible window');
assert(first.has('main:juegos:11'), 'Lower edge stays mounted one step outside the visible window');
assert.equal(nodes.get('main:juegos:1').style.opacity, '0');
assert.equal(nodes.get('main:juegos:11').style.pointerEvents, 'none');

const selected = nodes.get('main:juegos:4');
const second = new Set();
scope.xmbPlaceColumn('main:juegos:', items, 5, 20, false, second);
assert.equal(nodes.get('main:juegos:4'), selected, 'Up/down navigation retains the same DOM node');
assert(second.has('main:juegos:2'), 'Formerly visible item continues to the edge before removal');
assert.equal(nodes.get('main:juegos:2').style.opacity, '0');
assert(second.has('main:juegos:12'), 'Incoming item is mounted ahead of becoming visible');
assert.match(css, /will-change:transform,opacity/, 'Vertical entries animate their visual position');
assert.match(source, /previousPositions\.get\(key\)/, 'Vertical motion starts at the previous rendered coordinate');
assert.match(source, /transform: `translate\(\$\{dx\}px, \$\{dy\}px\)`/, 'Vertical motion uses explicit intermediate translation');
assert.match(source, /function xmbReducedMotion\(\)/, 'The XMB has its own accessible motion preference');
assert.match(source, /xmb\.motion === 'off'/, 'The player can explicitly disable the menu animations');
assert.match(source, /psp-dock-xmb-motion/, 'The motion preference is stored for this browser origin');
assert.match(source, /const still = xmbReducedMotion\(\)/, 'The animated XMB waves honor the in-app setting');
assert.match(source, /if \(xmbWavesFrameId !== null\) return/, 'Restarting waves never schedules a second RAF loop');
assert.match(source, /xmbSyncMotion\(\)/, 'Changing the preference updates the entire XMB');
assert.match(css, /\.xmb\.xmb-motion-on \.xmb-side\{transition:transform/, 'Explicit menu motion restores the right panel transition');
assert.match(css, /\.xmb\.xmb-motion-off \*/, 'Motion opt-out disables every XMB menu animation');
assert.match(source, /exit\.finished\.then\(\(\) => node\.remove\(\)/,
  'Outgoing horizontal column is removed only after its exit animation');
console.log('PSP XMB fluid movement regression tests: OK');
