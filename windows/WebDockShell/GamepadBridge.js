// Injected solely into the untrusted GAME view by WebView2. It exposes the
// original PSP PDK1 controller as a standard virtual Gamepad, not as
// privileged Windows keyboard injection. Real Xbox/PS gamepads are FIRST.
// No USB storage commands, local file APIs or host bridge calls are exposed.
(() => {
  'use strict';
  if (!window.chrome?.webview?.addEventListener) return;
  const original = typeof navigator.getGamepads === 'function' ?
    navigator.getGamepads.bind(navigator) : () => [];
  let state = null;
  let lastFrame = null;
  let lastKeys = new Set();
  let connected = false;
  const arrowCodes = {up:38, down:40, left:37, right:39};
  const arrowKeys = {up:'ArrowUp', down:'ArrowDown', left:'ArrowLeft', right:'ArrowRight'};

  function dispatchKey(direction, pressed) {
    const e = new KeyboardEvent(pressed ? 'keydown' : 'keyup', {
      key: arrowKeys[direction], code: arrowKeys[direction], bubbles: true,
      cancelable: true
    });
    // Some old JS ports still check deprecated keyCode/which.
    try {
      Object.defineProperty(e, 'keyCode', {value:arrowCodes[direction]});
      Object.defineProperty(e, 'which', {value:arrowCodes[direction]});
    } catch { /* The modern key and code fields remain available. */ }
    (document.activeElement || document.body || document).dispatchEvent(e);
  }
  function virtualPad(index = 0) {
    if (!state?.connected) return null;
    const yes = name => !!state[name];
    const buttons = Array.from({length:17}, () => ({pressed:false, touched:false, value:0}));
    const set = (index, value) => {
      buttons[index] = {pressed:!!value, touched:!!value, value:value ? 1 : 0};
    };
    set(0, yes('accept')); set(1, yes('back')); set(2, yes('saves'));
    set(3, yes('graphics')); set(4, yes('pageLeft')); set(5, yes('pageRight'));
    // Buttons 6/7 (L2/R2) and axes 2/3 (right stick) do not exist on PSP.
    set(8, yes('exit')); set(9, yes('settings'));
    set(12, yes('up')); set(13, yes('down'));
    set(14, yes('left')); set(15, yes('right'));
    const axes = [
      (yes('right') ? 1 : 0) - (yes('left') ? 1 : 0),
      (yes('down') ? 1 : 0) - (yes('up') ? 1 : 0),
      0, 0
    ];
    return {
      id:'PSP Dock PDK1 virtual controller (054c:01cb)', index,
      connected:true, mapping:'standard', timestamp:performance.now(),
      buttons, axes, vibrationActuator:null, hapticActuators:[]
    };
  }
  function allGamepads() {
    let physical = [];
    try {
      physical = Array.from(original() || []).filter(pad =>
        pad?.connected !== false &&
        !(/054c/i.test(pad.id || '') && /01cb/i.test(pad.id || '')));
    } catch { /* An unavailable browser API is not a fatal error. */ }
    // The first gamepad must be a complete external pad when available.
    const virtual = virtualPad(physical.length);
    return virtual ? [...physical, virtual] : physical;
  }
  try {
    Object.defineProperty(navigator, 'getGamepads',
      {configurable:true, value:allGamepads});
  } catch { /* Ports with no override support need a real gamepad. */ }

  window.chrome.webview.addEventListener('message', ({data}) => {
    if (!data || data.type !== 'psp-input' || !data.input) return;
    lastFrame = state;
    state = data.input;
    const active = !!state.connected;
    if (connected !== active) {
      connected = active;
      const event = new Event(active ? 'gamepadconnected' : 'gamepaddisconnected');
      Object.defineProperty(event, 'gamepad', {value:virtualPad()});
      window.dispatchEvent(event);
    }
    const newKeys = new Set();
    if (data.keyboardArrows && active) {
      for (const key of Object.keys(arrowKeys)) if (state[key]) newKeys.add(key);
    }
    for (const key of lastKeys) if (!newKeys.has(key)) dispatchKey(key,false);
    for (const key of newKeys) if (!lastKeys.has(key)) dispatchKey(key,true);
    lastKeys = newKeys;
  });
})();
