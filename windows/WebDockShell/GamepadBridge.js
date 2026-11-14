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
  let controlProfile = 'gamepad';
  let startSelector = null;
  let autoStart = false;
  let autoStarted = false;
  let physicalKeys = new Set();
  let physicalPadId = '';
  let physicalAcceptHeld = false;
  let physicalExitSince = 0;
  let physicalExitSent = false;
  let nativeXbox = null;
  let nativeXboxConnected = false;
  const allowedSelectors = new Set(['#play', '#start', '#load-full-version']);
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
  function dispatchPortKey(key, pressed) {
    const printable = key.length === 1;
    const code = key.startsWith('Arrow') || key === 'Enter' || key === 'Escape' ?
      key : key.toUpperCase() === key ? 'Key' + key : 'Key' + key.toUpperCase();
    const legacy = {ArrowUp:38,ArrowDown:40,ArrowLeft:37,ArrowRight:39,
      Enter:13,Escape:27}[key] || (printable ? key.toUpperCase().charCodeAt(0) : 0);
    const event = new KeyboardEvent(pressed ? 'keydown' : 'keyup',
      {key,code,bubbles:true,cancelable:true});
    try {
      Object.defineProperty(event,'keyCode',{value:legacy});
      Object.defineProperty(event,'which',{value:legacy});
    } catch { /* Key and code are still standard properties. */ }
    (document.activeElement || document.body || document).dispatchEvent(event);
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
  function xboxPad(index = 0) {
    if (!nativeXbox?.connected || !Array.isArray(nativeXbox.buttons) ||
        nativeXbox.buttons.length !== 17 ||
        !Array.isArray(nativeXbox.axes) || nativeXbox.axes.length !== 4) return null;
    const buttons = nativeXbox.buttons.map(value => {
      const level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
      return {pressed:level > .65,touched:level > .05,value:level};
    });
    return {
      id:nativeXbox.id || 'Xbox XInput', index, connected:true, mapping:'standard',
      timestamp:performance.now(), buttons, axes:nativeXbox.axes.map(value =>
        Number.isFinite(value) ? Math.max(-1,Math.min(1,value)) : 0),
      vibrationActuator:null,hapticActuators:[]
    };
  }
  function physicalPads() {
    let physical = [];
    try {
      physical = Array.from(original() || []).filter(pad =>
        pad && pad.connected !== false &&
        !(/054c/i.test(pad.id || '') && /01cb/i.test(pad.id || '')));
    } catch { /* Browser Gamepad API not activated in the game view. */ }
    const native = physical.length ? null : xboxPad(0);
    return native ? [native] : physical;
  }
  function allGamepads() {
    const physical = physicalPads();
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
    nativeXbox = data.xbox?.connected === true ? data.xbox : null;
    const currentNativeXbox = nativeXbox !== null;
    if (currentNativeXbox !== nativeXboxConnected) {
      nativeXboxConnected = currentNativeXbox;
      const event = new Event(currentNativeXbox ? 'gamepadconnected' : 'gamepaddisconnected');
      Object.defineProperty(event, 'gamepad', {value:xboxPad(0)});
      window.dispatchEvent(event);
    }
    controlProfile = ['arrows','wipeout','gamepad'].includes(data.controlProfile) ?
      data.controlProfile : 'gamepad';
    const nextSelector = allowedSelectors.has(data.startSelector) ? data.startSelector : null;
    if (nextSelector !== startSelector) autoStarted = false;
    startSelector = nextSelector;
    autoStart = data.autoStart === true;
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

  const pressed = (pad, index) => pad?.buttons?.[index]?.pressed === true ||
    (pad?.buttons?.[index]?.value || 0) > .65;
  const direction = (pad, button, axis, sign) =>
    pressed(pad, button) || (pad?.axes?.[axis] || 0) * sign > .55;
  function pollPhysical(time) {
    const pads = physicalPads();
    const pad = pads[0] || null;
    const identity = pad ? pad.index + ':' + pad.id : '';
    const accept = pressed(pad, 0);
    if (identity !== physicalPadId) {
      physicalPadId = identity;
      physicalAcceptHeld = accept; // Never launch from a held connection key.
      physicalExitSince = 0;
      physicalExitSent = false;
    }

    // Some original hosts have a Play/Start control *inside the runtime*.
    // Xbox A focuses and activates that control in the same game WebView2;
    // it must never open a different browser tab or click arbitrary adverts.
    if (startSelector) {
      const button = document.querySelector?.(startSelector);
      if (button && !button.disabled &&
          button.getClientRects?.().length && button.getAttribute?.('aria-disabled') !== 'true') {
        // Selecting the game in XMB requests playback; activate the actual
        // runtime's Play/Start control once when it becomes ready. If the
        // author requires a trusted user gesture, Xbox A retries it.
        const topLevel = !window.top || window.top === window;
        if (topLevel && ((autoStart && !autoStarted) ||
            (accept && !physicalAcceptHeld && pad))) {
          autoStarted = true;
          button.focus?.({preventScroll:true});
          button.click?.();
        }
      }
    }
    physicalAcceptHeld = accept;

    const next = new Set();
    if (pad && (controlProfile === 'arrows' || controlProfile === 'wipeout')) {
      if (direction(pad,12,1,-1)) next.add('ArrowUp');
      if (direction(pad,13,1,1)) next.add('ArrowDown');
      if (direction(pad,14,0,-1)) next.add('ArrowLeft');
      if (direction(pad,15,0,1)) next.add('ArrowRight');
      if (controlProfile === 'wipeout') {
        if (pressed(pad,0)) next.add('x');     // Thrust
        if (pressed(pad,1)) next.add('z');     // Weapon
        if (pressed(pad,4)) next.add('c');     // Left airbrake
        if (pressed(pad,5)) next.add('v');     // Right airbrake
        if (pressed(pad,3)) next.add('a');     // Change view
        if (pressed(pad,9)) next.add('Enter'); // Start game
      }
    }
    for (const key of physicalKeys) if (!next.has(key)) dispatchPortKey(key,false);
    for (const key of next) if (!physicalKeys.has(key)) dispatchPortKey(key,true);
    physicalKeys = next;

    // Keep an escape route that does not require a mouse. The native host
    // accepts only a close request, never privileged commands from a port.
    const exitChord = !!pad && pressed(pad,4) && pressed(pad,5) && pressed(pad,8);
    if (exitChord) {
      if (!physicalExitSince) physicalExitSince = time;
      if (time - physicalExitSince >= 1200 && !physicalExitSent) {
        physicalExitSent = true;
        window.chrome.webview.postMessage?.({type:'game-exit-request'});
      }
    } else {
      physicalExitSince = 0;
      physicalExitSent = false;
    }
    requestAnimationFrame(pollPhysical);
  }
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(pollPhysical);
})();
