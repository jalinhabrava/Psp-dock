'use strict';
// Execute the real frontend input and lifecycle functions with deterministic
// controller snapshots. No network, hardware or game runtime is simulated.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function setup() {
  const actions = [], messages = [], elements = {};
  let pad = null;
  const scope = {
    navigator:{getGamepads:()=>Array.isArray(pad)?pad:[null,pad]},
    document:{hidden:false,querySelectorAll:()=>[],activeElement:null},
    window:{chrome:{webview:{postMessage:m=>messages.push(m)}}},
    $:id=>elements[id] ||= {textContent:'',hidden:false,focus(){},classList:{toggle(){}}},
    requestAnimationFrame:()=>{}, Date,
    xmbConnectionView:()=>({waiting:false,product:{blocking:false}}),
    xmbRender(){}, xmbPress:button=>actions.push(button),
  };
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('const ui = {'), source.indexOf('\nconst bytes ='))
    .replace('const ui =', 'var ui ='), scope);
  vm.runInContext(source.slice(source.indexOf('function controllerInput('),
    source.indexOf('\nasync function pollNativePspInput(')), scope);
  vm.runInContext(source.slice(source.indexOf('function xmbValidatedWebPort('),
    source.indexOf('\nfunction xmbGoto(')), scope);
  scope.ui.state = {session:{running:false}};
  const hold = {...neutral(),settings:true,exit:true};
  return {scope,actions,messages,hold,setPad:p=>{pad=p;},
    open:()=>{scope.ui.webPortActive='test-port';scope.xmbWebPortResetHolds();},
    active:()=>!!scope.ui.webPortActive};
}
function neutral() { return {connected:true,source:'psp_winmm'}; }
function browserPad(id='Xbox', index=0, buttons=[]) {
  return {connected:true,id,index,buttons:Array.from({length:17},(_,i)=>({pressed:buttons.includes(i)})),axes:[0,0,0,0]};
}

for (const origin of ['browser','native']) {
  const t=setup();t.open();
  const frame=(time,input=t.hold)=>{
    if(origin==='native') t.scope.processNativePspFrame(input,time);
    else {
      t.setPad(input.connected ? browserPad('Xbox',0,[...(input.exit?[8]:[]),...(input.settings?[9]:[]),
        ...(input.pageLeft?[4]:[]),...(input.pageRight?[5]:[])]) : null);
      t.scope.gamepadLoop(time);
    }
  };
  frame(0);frame(1999);assert(t.active(),origin+': 1999 ms must not exit');
  frame(2000);assert(!t.active(),origin+': 2000 ms from zero must exit');
  assert.equal(t.actions.length,0,'The game owns input while running');
  frame(2100);frame(2200,{...t.hold,exit:false});
  assert.deepEqual(t.actions,[],'Both keys must be released after returning');
  frame(2300,neutral());
  frame(2400,{...neutral(),settings:true});
  assert.deepEqual(t.actions,['start'],'START must work again after neutral input');

  for (const interruption of ['release','disconnect','pause']) {
    const r=setup();r.open();
    const send=(input,time)=>{
      if(origin==='native')r.scope.processNativePspFrame(input,time);
      else {r.setPad(input.connected ? browserPad('Xbox',0,input.settings&&input.exit?[8,9]:[]) : null);r.scope.gamepadLoop(time);}
    };
    send(r.hold,0);send(r.hold,1000);
    if(interruption==='pause') {
      r.scope.document.hidden=true;send(r.hold,1100);r.scope.document.hidden=false;
    } else send(interruption==='release'?neutral():{connected:false},1100);
    send(r.hold,1200);send(r.hold,3199);assert(r.active(),origin+': '+interruption+' resets the deadline');
    send(r.hold,3200);assert(!r.active(),origin+': fresh uninterrupted hold exits');
  }
  const old=setup();old.open();
  if(origin==='native') {
    old.scope.processNativePspFrame({...neutral(),pageLeft:true,pageRight:true,exit:true},0);
    old.scope.processNativePspFrame({...neutral(),pageLeft:true,pageRight:true,exit:true},3000);
  } else {
    old.setPad(browserPad('Xbox',0,[4,5,8]));old.scope.gamepadLoop(0);old.scope.gamepadLoop(3000);
  }
  assert(old.active(),origin+': the old shoulder shortcut does not exit');
}

{
  const t=setup();t.open();
  t.scope.ui.nativePadConnected=true;t.scope.ui.nativePadLastAt=Date.now();
  t.setPad([browserPad('PSP 054c:01cb',0),browserPad('Xbox',1,[8,9])]);
  t.scope.gamepadLoop(0);t.scope.gamepadLoop(2000);
  assert(!t.active(),'A native PSP duplicate must not hide an external Xbox from the iframe escape route');
}
{
  const t=setup();t.open();
  t.setPad(browserPad('one',0,[8,9]));t.scope.gamepadLoop(0);
  t.setPad(browserPad('two',1,[8,9]));t.scope.gamepadLoop(1500);
  t.scope.gamepadLoop(3499);assert(t.active(),'A different pad cannot complete the first hold');
  t.scope.gamepadLoop(3500);assert(!t.active());
}
{
  const t=setup();t.open();
  // START from one origin and SELECT from the other never combine.
  t.setPad(browserPad('Xbox',0,[9]));t.scope.gamepadLoop(0);
  t.scope.processNativePspFrame({...neutral(),exit:true},0);
  t.scope.gamepadLoop(3000);t.scope.processNativePspFrame({...neutral(),exit:true},3000);
  assert(t.active(),'Different controllers must not complete a chord together');
}
{
  const t=setup();t.scope.xmbWebPortHostMessage({data:{type:'web-port-session',active:true}});
  t.setPad(browserPad('Xbox',0,[8,9]));
  t.scope.gamepadLoop(0);t.scope.gamepadLoop(2000);t.scope.gamepadLoop(4000);
  assert.equal(t.messages.length,1,'Only one host close request per hold');
  assert.equal(t.messages[0].reason,'controller-exit','Controller close requests retain the foreground guard');
  t.scope.xmbWebPortHostMessage({data:{type:'web-port-session',active:false}});
  t.scope.processNativePspFrame(neutral(),4100);
  t.scope.gamepadLoop(4100);
  assert.deepEqual(t.actions,[],'A neutral PSP cannot release held Xbox keys');
  t.setPad(browserPad());t.scope.gamepadLoop(4200);
  t.setPad(browserPad('Xbox',0,[8]));t.scope.gamepadLoop(4300);
  assert.deepEqual(t.actions,['select']);
}
{
  const t=setup();t.scope.xmbWebPortHostMessage({data:{type:'web-port-session',active:true}});
  t.scope.processNativePspFrame(t.hold,0);t.scope.processNativePspFrame(t.hold,2000);
  t.scope.xmbWebPortHostMessage({data:{type:'web-port-session',active:false}});
  t.setPad(browserPad());t.scope.gamepadLoop(2100);
  t.scope.processNativePspFrame(t.hold,2100);
  assert.deepEqual(t.actions,[],'A neutral browser pad cannot release held PSP keys');
  t.scope.processNativePspFrame(neutral(),2200);
  t.scope.processNativePspFrame({...neutral(),exit:true},2300);
  assert.deepEqual(t.actions,['select']);
  // A new game starts a new deadline even if the previous one ended mid-hold.
  t.scope.xmbWebPortHostMessage({data:{type:'web-port-session',active:true}});
  t.scope.processNativePspFrame(t.hold,3000);t.scope.processNativePspFrame(t.hold,4999);
  assert.equal(t.messages.length,1);
  t.scope.processNativePspFrame(t.hold,5000);assert.equal(t.messages.length,2);
}
console.log('Frontend iframe/host START+SELECT hold, source isolation and return suppression: OK');
