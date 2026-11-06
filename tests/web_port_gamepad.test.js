// Execute the exact script injected by WebView2 in an isolated JS runtime.
// Real external pads take priority, and PSP cannot acquire fictional L2/R2.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.resolve(__dirname,
  '../windows/WebDockShell/GamepadBridge.js'), 'utf8');

const events = [];
let dispatchInput = null;
const external = {id:'Xbox Controller',connected:true,buttons:[],axes:[0,0,0,0]};
const scope = {
  navigator:{getGamepads:()=>[external]},
  performance:{now:()=>1000},
  window:{
    chrome:{webview:{addEventListener:(name,handler)=>{
      if (name==='message') dispatchInput=handler;
    }}},
    dispatchEvent:event=>events.push(event.type),
  },
  document:{activeElement:{dispatchEvent:event=>events.push(event.type+':'+event.key)}},
  Event:class Event {constructor(type){this.type=type;}},
  KeyboardEvent:class KeyboardEvent {
    constructor(type,options){this.type=type;Object.assign(this,options);}
  },
};
vm.createContext(scope);
vm.runInContext(script,scope);
assert.equal(typeof dispatchInput,'function');
dispatchInput({data:{type:'psp-input',keyboardArrows:true,input:{
  connected:true,source:'psp_winmm',accept:true,pageLeft:true,pageRight:false,
  exit:false,up:true,down:false,left:false,right:false
}}});
let pads=scope.navigator.getGamepads();
assert.equal(pads.length,2);
assert.equal(pads[0].id,'Xbox Controller','A complete controller must win priority');
assert.match(pads[1].id,/PSP Dock/);
assert.equal(pads[1].buttons[0].pressed,true);
assert.equal(pads[1].buttons[4].pressed,true);
assert.equal(pads[1].buttons[5].pressed,false);
assert.equal(pads[1].buttons[6].pressed,false,'PSP L2 must never be synthesized');
assert.equal(pads[1].buttons[7].pressed,false,'PSP R2 must never be synthesized');
assert.equal(pads[1].axes[2],0,'PSP has no right stick');
assert.equal(pads[1].axes[3],0);
assert(events.includes('gamepadconnected'));
assert(events.includes('keydown:ArrowUp'));
dispatchInput({data:{type:'psp-input',keyboardArrows:false,input:{
  connected:false,up:false
}}});
pads=scope.navigator.getGamepads();
assert.equal(pads.length,1);
assert(events.includes('gamepaddisconnected'));
assert(events.includes('keyup:ArrowUp'));
console.log('WebView2 PSP Gamepad bridge, external pad priority and L2/R2 gaps: OK');
