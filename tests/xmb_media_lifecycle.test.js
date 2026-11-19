// Actual app functions, with controllable media/HTTP promises; no USB or game.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(process.argv[2] || path.resolve(__dirname, '../web/app.js'), 'utf8');
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const defer = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject}; };

function fixture() {
  const timers = new Map(); let timerId = 0;
  const nodes = new Map();
  const scope = {
    ui: {state: {session:{running:false}, product_flow:{enabled:true}}, operation:'', busy:false},
    xmb: {mediaGame:null, mediaGeneration:0, mediaSound:true, menuSound:true, menuSoundAt:0},
    document: {hidden:false, body:{classList:{add(){},remove(){}}, setAttribute(){},removeAttribute(){}},
      querySelectorAll:()=>[]},
    setTimeout: (fn) => {timers.set(++timerId,fn); return timerId;},
    clearTimeout: (id) => timers.delete(id), Date, AbortController,
    URL:{createObjectURL:()=> 'blob:fixture', revokeObjectURL:()=>{}},
    Image: class {}, fetch:async()=>({ok:false}),
    renderPreflight(){}, renderSaves(){}, toast(){},
    render: () => scope.xmbPreviewGame('a'.repeat(24)),
    calls:[],
    request:async (route) => {scope.calls.push(route); return {session:{running:false}};},
  };
  scope.$ = (id) => {
    if (!nodes.has(id)) nodes.set(id, {hidden:true,open:false,src:'',pauses:0,plays:0,
      pause(){this.pauses++;}, load(){}, removeAttribute(name){if(name==='src')this.src='';},
      play(){this.plays++; return Promise.resolve();}});
    return nodes.get(id);
  };
  vm.createContext(scope);
  vm.runInContext(slice(source.includes('function xmbPreviewAllowed()') ?
    'function xmbPreviewAllowed()' : 'function xmbPreviewGame(', 'function xmbText('), scope);
  vm.runInContext(slice('async function mutate(', 'function renderDevices('), scope);
  scope.runTimer = () => {const entry=timers.entries().next().value; if(entry){timers.delete(entry[0]);entry[1]();}};
  scope.audio = scope.$('xmb-game-audio');
  return scope;
}

async function main() {
  const s=fixture();
  s.xmbPreviewGame('a'.repeat(24));s.runTimer();
  assert.equal(s.audio.plays,1);assert.equal(s.audio.loop,true);
  const send=defer();
  s.request=async route => {s.calls.push(route); if(route==='/api/launch')return send.promise;
    return {session:{running:true},product_flow:{enabled:true}};};
  const launching=s.mutate('/api/launch',{id:'game'});
  assert.equal(s.audio.src,'','Preview stops BEFORE launch HTTP completes');
  assert.equal(s.audio.loop,false);
  assert.equal(s.xmb.mediaLaunchPending,true);
  s.xmbPreviewGame('a'.repeat(24));s.runTimer();
  assert.equal(s.audio.plays,1,'An old ready snapshot cannot restart preview');
  s.document.hidden=true;send.resolve({ok:true});await launching;
  assert.equal(s.ui.state.session.running,true);
  assert.equal(s.audio.src,'','Hidden window stays silent after launch');
  s.document.hidden=false;s.xmbPreviewGame('a'.repeat(24));s.runTimer();
  assert.equal(s.audio.plays,1,'Still silent if game runs while Dock is visible');
  s.request=async()=>({session:{running:false}});await s.refresh();s.runTimer();
  assert.equal(s.audio.plays,2,'Preview resumes after confirmed game exit, not before');

  const stale=fixture(), oldGet=defer(), post=defer();
  stale.request=async route=>route==='/api/state'?oldGet.promise:post.promise;
  const oldRefresh=stale.refresh();
  const pending=stale.mutate('/api/retro-launch',{id:'rom'});
  oldGet.resolve({session:{running:false}});await oldRefresh;
  assert.equal(stale.xmb.mediaLaunchPending,true,'State request issued before launch does not unlatch');
  stale.request=async()=>({session:{running:true}});post.resolve({ok:true});await pending;
  assert.equal(stale.ui.state.session.running,true);
  assert.equal(stale.audio.plays,0);

  const late=fixture(), play=defer();
  late.audio.play=function(){this.plays++;return play.promise;};
  late.xmbPreviewGame('a'.repeat(24));late.runTimer();
  late.xmb.mediaLaunchPending=true;late.xmbPreviewGame(null);
  const pauses=late.audio.pauses;play.resolve();await Promise.resolve();
  assert(late.audio.pauses>pauses,'A delayed play resolution cannot leak over the emulator');

  const fail=fixture();
  fail.xmbPreviewGame('a'.repeat(24));fail.runTimer();
  fail.request=async route=>{if(route==='/api/launch')throw Error('rejected');return {session:{running:false}};};
  assert.equal(await fail.mutate('/api/launch',{}),false);fail.runTimer();
  assert.equal(fail.xmb.mediaLaunchPending,false,'Known launch failure can restore selection');
  assert.equal(fail.audio.plays,2);

  const unknown=fixture();unknown.request=async()=>{throw Error('offline');};
  await unknown.mutate('/api/launch',{});
  assert.equal(unknown.xmb.mediaLaunchPending,true,'Uncertain delivery stays silent');
  assert.equal(unknown.audio.plays,0);

  const sfx=fixture();sfx.xmbMenuClick('cursor');sfx.xmbMenuClick('cursor');
  assert.equal(sfx.calls.length,1,'Menu sound is debounced');
  sfx.xmb.menuSoundAt=0;sfx.ui.state.session.running=true;sfx.xmbMenuClick('cursor');
  assert.equal(sfx.calls.length,1,'No menu sound while a game runs');
  sfx.ui.state.session.running=false;sfx.xmb.menuSound=false;sfx.xmbMenuClick('cursor');
  assert.equal(sfx.calls.length,1,'Menu sound has an independent mute');

  assert(source.includes("path === '/api/launch' || path === '/api/retro-launch'"));
  assert(source.includes("document.addEventListener('visibilitychange'"));
  console.log('XMB media lifecycle: launch, hidden window, stale responses, late play, failed launch, return and original UI sounds PASS');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
