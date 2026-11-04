// Verify retro UI wiring: selecting/△ never copies or launches implicitly.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.resolve(__dirname,'../web/app.js'),'utf8');
const start = source.indexOf('function xmbRetroEmulatorMenu(');
const end = source.indexOf('\nfunction xmbGoto(',start);
assert(start > 0 && end > start);

const events = [];
const emulator = {id:'a'.repeat(24),name:'gpSP Kai',systems:['gba']};
const missing = {id:'b'.repeat(24),emulator_id:emulator.id,title:'Test ROM',
  system:'gba',system_name:'Game Boy Advance',format:'GBA',size:15,
  relative:'PSP/GAME/gpSP/ROMS/Test.gba',availability:'missing',
  windows_emulator:'RetroArch · mgba',core_available:false};
const ui = {busy:false,serviceOffline:false,state:{
  retro:{roms:[missing]},selected_device:'X:',recovery_required:false,
  session:{running:false},saves:{actions:[],conflicts:[]},
  preflight:{saves_prepared:true},controller_status:{connected:false},
  can_launch_retro:false,
}};
const scope={ui,bytes:(n)=>String(n)+' B',
  xmb:{settings:{retro_install_policy:'off'}},
  xmbRetroInstallState:()=>({active:false,policy:'off',installer:{needed:false,external:false},detail:'Descarga desactivada'}),
  xmbStartRetroInstall:()=>events.push('install'),
  xmbConnectionView:()=>({waiting:false,product:{enabled:false,blocking:false},diagnostic:false,nativeController:false,canPlay:true}),
  xmbSide:(title,opts,pick)=>{events.push('side');scope.options=opts;scope.pick=pick;},
  toast:(msg)=>events.push('toast: '+msg),
  openRetroTransfer:(rom)=>events.push('copy: '+rom.id),
  openSaves:()=>events.push('saves'),
  mutate:(path,body)=>events.push('launch: '+path),
};
vm.createContext(scope);
vm.runInContext(source.slice(start,end),scope);
scope.xmbRetroEmulatorMenu(emulator);
assert.equal(scope.options.length,1);
assert.equal(events.filter(v=>v.startsWith('copy')).length,0);
scope.pick(0); // missing ROM opens contextual menu, no copying
assert.equal(scope.options.length,4);
assert.equal(events.filter(v=>v.startsWith('copy')).length,0);
scope.pick(1); // explicitly choose Copiar
assert.equal(events.filter(v=>v.startsWith('copy')).length,1);

// A ready ROM uses the equivalent Windows core, only with prepared PDK1 HID.
const ready = {...missing,availability:'ready',core_available:true};
ui.state.retro.roms = [ready];
ui.state.selected_device=null;
ui.state.controller_status.connected=true;
ui.state.can_launch_retro=true;
scope.xmbRetroEmulatorMenu(emulator);
assert.equal(scope.options.length,1);
scope.pick(0); // ready ROM launches directly from emulator's side list
assert.equal(events.filter(v=>v.startsWith('launch')).length,1);
assert(events.includes('launch: /api/retro-launch'));

// An emulator may finish downloading while its ROM side panel is open.
// The menu must not capture a stale core_available=false or old status.
const awaitingCore = {...ready,core_available:false};
ui.state.retro.roms = [awaitingCore];
scope.xmbRetroEmulatorMenu(emulator);
assert.match(scope.options[0].s,/Descarga desactivada/);
ui.state.retro.roms = [ready];
assert.match(scope.options[0].s,/En el PC/);
assert.doesNotMatch(scope.options[0].s,/Descarga desactivada/);
scope.pick(0);
assert.equal(events.filter(v=>v.startsWith('launch')).length,2,
  'live ROM readiness launches from the already-open side menu');

// Neither missing cores nor a PSP still in USB storage can bypass the gate.
scope.xmbLaunchRetro({...ready,core_available:false});
assert.equal(events.filter(v=>v.startsWith('launch')).length,2);
assert.equal(events.filter(v=>v === 'install').length,0, 'opt-out prevents PC downloads');
ui.state.selected_device='X:';
scope.xmbLaunchRetro(ready);
assert.equal(events.filter(v=>v.startsWith('launch')).length,2);

assert(source.includes("['juegos', 'Juegos'], ['retro', 'Retro']"));
console.log('Retro XMB discovery, explicit copy and native Windows launch gates: OK');
