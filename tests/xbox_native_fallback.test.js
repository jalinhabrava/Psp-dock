// A separate game WebView2 may have no browser Gamepad activation gesture.
// Its native XInput fallback must supply Xbox buttons/axes without the mouse.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const script=fs.readFileSync(path.resolve(__dirname,
  '../windows/WebDockShell/GamepadBridge.js'),'utf8');
const events=[], frames=[];
let receive=null, clicks=0;
const play={disabled:false,getClientRects:()=>[{}],
  getAttribute:()=>null,focus:()=>{},click:()=>clicks++};
const scope={
  navigator:{getGamepads:()=>[null]},
  performance:{now:()=>102},
  document:{querySelector:selector=>selector==='#play'?play:null,
    activeElement:{dispatchEvent:()=>{}}},
  window:{chrome:{webview:{
    addEventListener:(event,cb)=>{if(event==='message')receive=cb},
    postMessage:()=>{}
  }},dispatchEvent:event=>events.push(event.type)},
  requestAnimationFrame:fn=>frames.push(fn),
  Event:class Event{constructor(type){this.type=type}},
  KeyboardEvent:class KeyboardEvent{constructor(type,opts){this.type=type;Object.assign(this,opts)}}
};
vm.createContext(scope);
vm.runInContext(script,scope);
assert.equal(typeof receive,'function');
assert.equal(scope.navigator.getGamepads().length,0,'null slots are not controllers');
const buttons=Array(17).fill(0);
buttons[0]=1;buttons[6]=.5;buttons[7]=.9;buttons[8]=1;
receive({data:{type:'psp-input',input:{connected:false},
  keyboardArrows:false,controlProfile:'gamepad',startSelector:'#play',
  autoStart:true,xbox:{connected:true,id:'Xbox XInput 0',
    buttons,axes:[.4,-.8,.2,.3]}}});
const pads=scope.navigator.getGamepads();
assert.equal(pads.length,1,'native bridge supplies one Xbox pad');
assert.equal(pads[0].id,'Xbox XInput 0');
assert.equal(pads[0].buttons[0].pressed,true);
assert.equal(pads[0].buttons[6].value,.5);
assert.equal(pads[0].buttons[7].value,.9);
assert.equal(pads[0].axes[2],.2,'right analog stick remains available');
assert(events.includes('gamepadconnected'));
frames.shift()(300);
assert.equal(clicks,1,'native Xbox can start the exact game runtime');
frames.shift()(310);
assert.equal(clicks,1,'auto-start does not click repeatedly');
receive({data:{type:'psp-input',input:{connected:false},
  keyboardArrows:false,controlProfile:'gamepad',startSelector:'#play',
  autoStart:true,xbox:null}});
assert.equal(scope.navigator.getGamepads().length,0);
assert(events.includes('gamepaddisconnected'));
console.log('Native XInput fallback keeps Xbox controls in the game WebView2: OK');
