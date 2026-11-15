// Exercise the exact WebView2 injected bridge without a browser or USB.
// Xbox A must activate a *known* runtime Play control, not a guide link.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const src=fs.readFileSync(path.resolve(__dirname,
  '../windows/WebDockShell/GamepadBridge.js'),'utf8');
const buttons=Array.from({length:17},()=>({pressed:false,value:0}));
const pad={id:'Xbox Wireless Controller',index:0,connected:true,buttons,axes:[0,0,0,0]};
let receive=null, clicks=0, focused=0;
const frames=[], keys=[], messages=[];
const launcher={
  disabled:false,
  getClientRects:()=>[{}],
  getAttribute:()=>null,
  focus:()=>focused++,
  click:()=>clicks++,
};
const scope={
  navigator:{getGamepads:()=>[pad]},
  performance:{now:()=>100},
  document:{
    activeElement:{dispatchEvent:e=>keys.push(e.type+':'+e.key)},
    querySelector:selector=>{
      assert(['#play','#load-full-version','#start'].includes(selector));
      return launcher;
    }
  },
  window:{chrome:{webview:{
    addEventListener:(name,cb)=>{if(name==='message')receive=cb},
    postMessage:message=>messages.push(message)
  }},dispatchEvent:()=>{}},
  Event:class Event{constructor(type){this.type=type}},
  KeyboardEvent:class KeyboardEvent{
    constructor(type,opts){Object.assign(this,opts);this.type=type}
  },
  requestAnimationFrame:fn=>frames.push(fn)
};
vm.createContext(scope);
vm.runInContext(src,scope);
assert.equal(frames.length,1);
const send=(profile,selector,autoStart=false)=>receive({data:{type:'psp-input',
  input:{connected:false},keyboardArrows:false,
  controlProfile:profile,startSelector:selector,autoStart}});
const frame=at=>{const cb=frames.shift();assert(cb,'missing controller poll');cb(at)};
const down=index=>{buttons[index]={pressed:true,value:1}};
const up=index=>{buttons[index]={pressed:false,value:0}};

send('gamepad','#play');frame(10);
assert.equal(clicks,0,'pad detection must never trigger Play');
down(0);frame(20);
assert.equal(clicks,1,'Xbox A must click the actual host runtime Play button');
assert.equal(focused,1);
frame(30);
assert.equal(clicks,1,'A held cannot repeatedly click Play');
up(0);launcher.disabled=true;frame(40);
down(0);frame(50);
assert.equal(clicks,1,'loading/disabled Play button must not be pressed');
up(0);launcher.disabled=false;frame(60);
down(0);frame(70);
assert.equal(clicks,2,'A works again once the game is ready');

send('arrows',null);
up(0);down(12);frame(80);
assert(keys.includes('keydown:ArrowUp'),'2048 must receive Xbox directional input');
up(12);frame(90);
assert(keys.includes('keyup:ArrowUp'));

send('wipeout','#load-full-version');
down(0);frame(100);
assert(keys.includes('keydown:x'),'WipEout thrust is accessible from Xbox A');
down(4);down(5);down(8);frame(150);
frame(2150);
assert.equal(messages.length,0,'The old LB+RB+View shortcut must not close a port');
up(4);up(5);down(9);frame(2200);
frame(4199);
assert.equal(messages.length,0,'Menu+View requires a full 2000 ms hold');
frame(4200);
assert(messages.some(m=>m.type==='game-exit-request'),
  'Menu+View must return to Dock without touching the mouse');
frame(4600);
assert.equal(messages.filter(m=>m.type==='game-exit-request').length,1,
  'exit request must be sent once per hold');
up(8);up(9);up(0);
const beforeAutostart = clicks;
send('gamepad','#start',true);
frame(4700);
assert.equal(clicks,beforeAutostart+1,'XMB selection should start a verified runtime without using the mouse');
frame(4710);
assert.equal(clicks,beforeAutostart+1,'Auto-start must not click repeatedly each animation frame');
console.log('Xbox WebView2 start buttons, keyboard ports and exit chord: OK');
