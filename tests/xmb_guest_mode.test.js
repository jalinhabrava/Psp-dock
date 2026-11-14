// Guest entry without a PSP: no fabricated USB/HID state, no ROM/save menus.
// Run with Node. This uses the real pure connection model and XMB guest code.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname,'../web/app.js'),'utf8');
const first = source.indexOf('// ==== inicio del modelo de conexión puro ====');
const last = source.indexOf('// ==== fin del modelo de conexión puro ====',first);
assert(first >= 0 && last > first);
const scope = {};
vm.createContext(scope);
vm.runInContext(source.slice(first,last),scope);
const view = scope.dockConnectionView;
const absent = {
  demo:false, selected_device:null, controller_status:{connected:false,mode:'await_native',can_play:false},
  product_flow:{enabled:true,phase:'waiting_psp',connection:{phase:'waiting'}},
  session:{running:false,pending_return:false,exit_requested:false}
};
const connect = { ...absent, selected_device:'E:\\', controller_status:{connected:false,mode:'usb_storage'} };
const psp = { ...absent, controller_status:{connected:true,mode:'native_dock',can_play:true} };

assert.equal(view(absent).waiting,true);
assert.equal(view(absent).canStartGuest,true,'offer must be explicit, not an automatic launch');
assert.equal(view({...absent,controller_status:{connected:false,mode:'await_hid'}}).canStartGuest,true,
  'legacy HID-only waiting also offers guest play');
assert.equal(view(absent,{guestRequested:true}).guestMode,true);
assert.equal(view(absent,{guestRequested:true}).screen,'menu');
assert.equal(view(absent,{guestRequested:true}).canPlay,false,'guest is never a PSP launch capability');
for (const [label, state, options] of [
  ['real PSP',psp,{}],['USB storage',connect,{}],
  ['pending return',{...absent,session:{pending_return:true}},{}],
  ['recovery',{...absent,recovery_required:true},{}],
  ['copy in progress',{...absent,copy_queue:{status:'copying'}},{}],
  ['closed window',absent,{closed:true}],
  ['demo',{...absent,demo:true},{}],
  ['unexpected dock phase',{...absent,product_flow:{enabled:true,phase:'attention'}},{}],
]) {
  const result=view(state,{...options,guestRequested:true});
  assert.equal(result.guestMode,false,label+' must not allow guest');
  assert.equal(result.canStartGuest,false,label+' must not show guest button');
}
for (const [label, state, options] of [
  ['temporary backend outage',absent,{serviceOffline:true}],
  ['USB poll in progress',{...absent,scanning:true},{}],
  ['transient search',{...absent,product_flow:{...absent.product_flow,
    connection:{phase:'searching'}}},{}],
  ['transient identifying',{...absent,product_flow:{...absent.product_flow,
    connection:{phase:'identifying'}}},{}],
  ['temporary backend error',{...absent,error:'USB probe could not complete'},{}],
]) {
  const result=view(state,{...options,guestRequested:true});
  assert.equal(result.guestMode,true,label+' must not eject the player from XMB');
  assert.equal(result.waiting,false,label+' must keep XMB visible');
  assert.equal(result.canStartGuest,false,label+' is not a reason to offer fresh entry');
}

const events=[];
const nodes={
  'web-port-frame':{src:'https://hexgl.bkcore.com/play/'},
  'web-port-view':{hidden:false},
};
Object.assign(scope,{
  ui:{
    state:absent, guestRequested:false,storageSetupRequested:false,
    guestTransitioning:false,appClientId:'a'.repeat(32),
    webPortActive:null,webPortHostActive:false,webPortCatalogError:'',
    webPorts:[{id:'hexgl',title:'HexGL',platform:'Web',genre:'Carreras',author:'BKCore',
      controls:['keyboard'],embed:'iframe',missing_on_psp:[]}],
    appClosing:false,appClosed:false,serviceOffline:false,busy:false
  },
  xmb:{nav:{cat:2,idx:{},level:'list',subIdx:0,side:null,confirm:null},sub:null,side:null,confirm:null},
  window:{chrome:{webview:{postMessage:message=>events.push(message)}}},
  $:id=>nodes[id]||={},
  xmbRender:()=>events.push('render'),
  xmbLoadWebPorts:()=>events.push('catalog'),
  request:async (route,data)=>{
    assert.equal(route,'/api/guest-mode');
    assert.equal(data.client_id,'a'.repeat(32));
    events.push('guest:'+data.enabled);
    return {ok:true,guest_mode:data.enabled};
  },
});
const runtimeStart = source.indexOf('function xmbConnectionView(');
const runtimeEnd = source.indexOf('function xmbRenderConnection(',runtimeStart);
vm.runInContext(source.slice(runtimeStart,runtimeEnd),scope);
async function main() {
await scope.xmbStartGuest();
assert(events.includes('guest:true'), 'guest input must pause PSP USB polling in the backend');
assert.equal(scope.ui.guestRequested,true);
assert.equal(scope.xmb.nav.cat,2);
assert.equal(scope.xmbConnectionView().guestMode,true);

const itemsStart=source.indexOf('function xmbWebPortItems()');
const itemsEnd=source.indexOf('function xmbBuildSections()',itemsStart);
vm.runInContext(source.slice(itemsStart,itemsEnd),scope);
const sections=scope.xmbGuestSections();
assert.deepEqual(Array.from(sections,s=>s.id),['ajustes','dock','webports']);
assert.equal(sections[2].items[0].t,'Buscar juego por nombre');
assert.equal(sections[2].items[4].t,'HexGL');
assert(sections[1].items.some(row=>row.key==='guest-connect'));
assert(sections[1].items.some(row=>row.key==='guest-close'));
assert(!JSON.stringify(sections.map(s=>s.id)).includes('retro'));
assert(!JSON.stringify(sections.map(s=>s.id)).includes('partidas'));

const webStart=source.indexOf('function xmbWebPortLaunch(');
const webEnd=source.indexOf('function xmbWebPortClose(',webStart);
Object.assign(scope,{
  xmbValidatedWebPort:()=>true,
  xmbPreviewGame:()=>{},
  toast:message=>events.push('toast:'+message),
  xmbConfirm:()=>events.push('unexpected-confirm'),
});
vm.runInContext(source.slice(webStart,webEnd),scope);
scope.xmbWebPortLaunch(scope.ui.webPorts[0]);
assert(events.some(e=>e?.type==='launch-web-port'&&e.id==='hexgl'),
  'browser game must launch despite the ordinary PSP product waiting gate');

// A real PSP appears while the guest web game is active. End guest, unload
// iframe, request native WebView2 to close, and return to normal connection.
scope.ui.webPortActive='hexgl';
scope.ui.webPortHostActive=true;
scope.ui.state=connect;
assert.equal(scope.xmbConnectionView().guestMode,false);
assert.equal(scope.ui.guestRequested,false);
assert.equal(scope.ui.webPortActive,null);
assert.equal(nodes['web-port-frame'].src,'about:blank');
assert.equal(nodes['web-port-view'].hidden,true);
assert(events.some(e=>e?.type==='close-web-port'));
scope.ui.state=absent;
assert.equal(scope.xmbConnectionView().waiting,true,
  'disconnecting must not reactivate guest without pressing the button');

await scope.xmbStartGuest();
assert.equal(scope.xmbConnectionView().guestMode,true);
await scope.xmbExitGuest();
assert(events.includes('guest:false'), 'Conectar PSP must explicitly resume USB polling');
assert.equal(scope.ui.guestRequested,false);
assert.equal(scope.xmbConnectionView().waiting,true);
scope.ui.state={...absent,guest_mode:true};
assert.equal(scope.xmbConnectionView().guestMode,true,
  'a reloaded client must adopt its current backend guest session');
await scope.xmbExitGuest();
console.log('PSP-free guest mode: explicit entry, web-only XMB, reconnect and exit guards OK');
}
main().catch(error=>{console.error(error);process.exitCode=1});
