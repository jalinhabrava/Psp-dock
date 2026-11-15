'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../windows/WebDockShell/GamepadBridge.js'), 'utf8');
function setup(native) {
  let physical = null, receive, poll, visibility, blur;
  const messages = [];
  const scope = {
    navigator:{getGamepads:()=>[physical]},performance:{now:()=>0},
    document:{hidden:false,addEventListener:(name,cb)=>{if(name==='visibilitychange')visibility=cb;}},
    window:{chrome:{webview:{addEventListener:(name,cb)=>{receive=cb;},postMessage:m=>messages.push(m)}},dispatchEvent(){},
      addEventListener:(name,cb)=>{if(name==='blur')blur=cb;}},
    requestAnimationFrame:cb=>{poll=cb;},Event:class {constructor(type){this.type=type;}},
  };
  vm.createContext(scope);vm.runInContext(source,scope);
  return {messages,scope,frame:time=>poll(time),blur:()=>blur(),pause:()=>{
    scope.document.hidden=true;visibility();scope.document.hidden=false;
  },pad:(buttons=[],id='Xbox XInput 0',index=0)=>{
    if(native)receive({data:{type:'psp-input',input:{connected:false},xbox:{connected:true,id,
      buttons:Array.from({length:17},(_,i)=>buttons.includes(i)?1:0),axes:[0,0,0,0]}}});
    else physical={id,index,connected:true,buttons:Array.from({length:17},(_,i)=>({pressed:buttons.includes(i)})),axes:[0,0,0,0]};
  },disconnect:()=>{
    if(native)receive({data:{type:'psp-input',input:{connected:false},xbox:null}});
    else physical=null;
  }};
}
for(const native of [false,true]) {
  const name=native?'Native XInput fallback':'Physical browser pad';
  const t=setup(native);t.pad([8,9]);t.frame(0);t.frame(1999);
  assert.equal(t.messages.length,0,name+': 1999 ms must not close');
  t.frame(2000);assert.equal(t.messages.length,1,name+': 2000 ms from zero must close');
  t.frame(5000);assert.equal(t.messages.length,1,name+': one close request per hold');
  t.pad([]);t.frame(5100);t.pad([8,9]);t.frame(5200);t.frame(7200);
  assert.equal(t.messages.length,2,name+': release rearms the gesture');
  for(const interruption of ['release','disconnect','identity','pause','blur']) {
    const r=setup(native);r.pad([8,9]);r.frame(0);r.frame(1000);
    if(interruption==='pause')r.pause();
    else if(interruption==='blur')r.blur();
    else if(interruption==='disconnect'){r.disconnect();r.frame(1100);}
    else if(interruption==='identity'){r.pad([8,9],'Xbox XInput 1',1);r.frame(1100);}
    else {r.pad([8]);r.frame(1100);}
    if(interruption!=='identity')r.pad([8,9]);
    const start=interruption==='identity'?1100:1200;
    r.frame(start);r.frame(start+1999);
    assert.equal(r.messages.length,0,name+': '+interruption+' resets the timer');
    r.frame(start+2000);assert.equal(r.messages.length,1,name+': uninterrupted hold works after '+interruption);
  }
  const old=setup(native);old.pad([4,5,8]);old.frame(0);old.frame(3000);
  assert.equal(old.messages.length,0,name+': old LB+RB+View chord is inactive');
  const split=setup(native);split.pad([8]);split.frame(0);split.frame(1000);
  split.pad([9]);split.frame(2000);split.frame(5000);
  assert.equal(split.messages.length,0,name+': nonoverlapping buttons cannot complete a hold');
}
{
  const t=setup(true);let now=0;t.scope.performance.now=()=>now;
  t.pad([8,9]);t.frame(now);
  now=601;t.frame(now);
  now=2000;t.frame(now);
  assert.equal(t.messages.length,0,'An expired native snapshot cannot complete a hold after HTTP stops delivering input');
  t.pad([8,9]);t.frame(now);
  for(now=2500;now<=4000;now+=500){t.pad([8,9]);t.frame(now);}
  assert.equal(t.messages.length,1,'Fresh native frames restore normal hold detection');
}
console.log('Injected physical/native Xbox exit gesture: threshold, identity, disconnect, pause and rearm: OK');
