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
assert.match(css, /transition:top \.28s/, 'Vertical movement is animated');
assert.match(source, /exit\.finished\.then\(\(\) => node\.remove\(\)/,
  'Outgoing horizontal column is removed only after its exit animation');
console.log('PSP XMB fluid movement regression tests: OK');
