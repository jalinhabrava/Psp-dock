// SRAM actions are double-confirmed, never initiated by TRIANGLE alone.
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const code=fs.readFileSync(path.resolve(__dirname,'../web/app.js'),'utf8');
const start=code.indexOf('function xmbRetroSaveMenu(');
const end=code.indexOf('\nfunction xmbGoto(',start);
assert(start>0 && end>start);
const calls=[];
const row={rom_id:'a'.repeat(24),title:'Golden',status:'conflict',revision:'b'.repeat(64),
  psp_present:true,pc_present:true,source_relative:'PSP/GAME/gpSP/SAVES/Golden.sav',
  suggested_path:false,skipped:false};
const state={retro_saves:{offline:false,rows:[row]},selected_device:'X:',
  session:{exit_requested:true}};
const scope={ui:{state,busy:false,serviceOffline:false},
  xmbSide:(title,choices,pick)=>{calls.push('open side');scope.choices=choices;scope.pick=pick;},
  xmbConfirm:(text,yes,done)=>{calls.push('confirm');scope.done=done;scope.text=text;scope.yes=yes;},
  mutate:(url,body)=>{calls.push('post');scope.url=url;scope.body=body;},
  xmbEscape:(text)=>String(text).replaceAll('<','&lt;').replaceAll('>','&gt;'),
  toast:(text)=>calls.push('toast:'+text),
};
vm.createContext(scope);
vm.runInContext(code.slice(start,end),scope);
scope.xmbRetroSaveMenu(row);
assert.equal(calls.includes('post'),false);
assert.equal(scope.choices.length,4);
assert.equal(scope.choices[0].choice,'psp');
assert.equal(scope.choices[1].choice,'pc');
assert.equal(scope.choices[2].choice,'skip');
scope.pick(1);
assert.equal(calls.includes('confirm'),true);
assert.equal(calls.includes('post'),false);
assert.equal(scope.yes,false);
assert(scope.text.includes('PC → PSP'));
scope.done();
assert.equal(scope.url,'/api/retro-save-sync');
assert.equal(scope.body.choice,'pc');
assert.equal(scope.body.revision,row.revision);
state.retro_saves.offline=true;
scope.xmbRetroSaveMenu(row);
scope.pick(0);
assert.equal(calls.filter(v=>v==='post').length,1);
assert(calls.some(v=>v.startsWith('toast:Vuelve')));
console.log('Retro SRAM right-side menu: explicit direction, confirmation, revision and offline safety OK');
