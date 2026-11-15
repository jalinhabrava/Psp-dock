// Pure JS tests of controller navigation, without opening browser or touching
// the PSP. Run: node tests/xmb_controller_navigation.test.js
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const code = fs.readFileSync(path.resolve(__dirname, '../web/app.js'), 'utf8');
const start = code.indexOf('function navigateDialogControl(');
const next = code.indexOf('\nfunction controllerInput(', start);
assert(start > 0 && next > start, 'Expected a dedicated controller navigation function');
const movements = [];
class FakeEvent {
  constructor(type, options) { this.type = type; this.options = options; }
}
const document = { activeElement: null };
const scope = {
  document, Event: FakeEvent,
  focusDialogControl: (dialog, delta) => movements.push({ dialog, delta }),
};
vm.createContext(scope);
vm.runInContext(code.slice(start, next), scope);
const dialog = { id: 'graphics-dialog' };
const select = {
  tagName: 'SELECT', disabled: false, selectedIndex: 0,
  options: [{disabled:false},{disabled:false},{disabled:true},{disabled:false}],
  events: [], dispatchEvent(event) { this.events.push(event); },
};
document.activeElement = select;

scope.navigateDialogControl(dialog, 'right');
assert.equal(select.selectedIndex, 1, 'Right selects next enabled option');
scope.navigateDialogControl(dialog, 'right');
assert.equal(select.selectedIndex, 3, 'Right skips disabled option');
scope.navigateDialogControl(dialog, 'right');
assert.equal(select.selectedIndex, 0, 'Right wraps at end of options');
scope.navigateDialogControl(dialog, 'left');
assert.equal(select.selectedIndex, 3, 'Left wraps to last enabled option');
assert.equal(select.events.length, 4, 'Select changes dispatch real events');
assert(select.events.every(event => event.type === 'change'));
scope.navigateDialogControl(dialog, 'down');
assert.deepEqual(movements.at(-1), { dialog, delta: 1 });
scope.navigateDialogControl(dialog, 'up');
assert.deepEqual(movements.at(-1), { dialog, delta: -1 });
document.activeElement = { tagName:'BUTTON', disabled:false };
scope.navigateDialogControl(dialog,'left');
assert.deepEqual(movements.at(-1), { dialog, delta:-1 });

// Native PSP frames remain primed on connect. In the explicit PSP-free
// welcome screen, the first Xbox A press may choose "Jugar con otro
// mando", but no PSP ISO/Retro launch can bypass its device gates.
assert(code.includes('ui.nativePadHeld = frame;'));
assert(code.includes('ui.gamepadHeld = pad && !(entry.waiting && entry.canStartGuest) ?'));
assert(code.includes('controllerInput(pad) : {};'));
assert(code.includes('processNativePspFrame(frame, performance.now());'));
assert(code.includes("await mutate('/api/copy-policy'"));

console.log('PSP XMB controller UI navigation / first-frame safety tests: OK');
