'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const inventory = JSON.parse(fs.readFileSync(path.join(__dirname, '../web/official-ports.json'), 'utf8'));
const entry = inventory.games.find(g => g.requires_original_files && g.link_status === 'active' && g.category === 'port_candidate');
const preview = {...inventory, demo:true, preview_web_ports:true,
  games:inventory.games.map(g => ({...g, preview_allowed:g.link_status !== 'broken' && g.category === 'port_candidate',
    preview_block_reason:g.link_status === 'broken' ? 'Enlace roto' : ''}))};
const events = [];
const ui = {state:{demo:true,preview_web_ports:true}, officialFilters:{category:'port_candidate'},
  officialCatalog:null,officialLoading:false,officialError:'',previewCatalog:null};
const scope = {ui,xmb:{nav:{idx:{}}},xmbRender:()=>{},
  request:async route => route === '/api/official-web-ports' ? inventory : preview,
  window:{chrome:{webview:{postMessage:msg=>events.push(msg)}}},
  toast:message=>events.push({toast:message}),xmbEscape:value=>value,
  xmbPreviewGame:()=>events.push({stopPreview:true}),
  xmbConfirm:(message,yes,callback)=>{scope.confirm={message,callback};},
  xmbSide:(title,opts,pick)=>{scope.side={opts,pick};},
  xmbWebIsoMatches:()=>{throw Error('Launching a test must not scan or load any ISO');},
};
vm.createContext(scope);
vm.runInContext(source.slice(source.indexOf('function xmbWebPortItems()'),
  source.indexOf('\nfunction xmbGuestSections()')),scope);
(async()=>{
  await scope.xmbLoadOfficialPorts();
  assert.equal(ui.previewCatalog,preview);
  assert.equal(scope.xmbOfficialFilteredGames().length,190);
  assert(scope.xmbOfficialFilteredGames().every(g=>g.category==='port_candidate'));
  scope.xmbOfficialDetails(entry);
  assert.equal(scope.side.opts[0].t,'Probar enlace · experimental');
  scope.side.pick(0);
  assert.equal(events.length,0,'No launch before explicit acknowledgement');
  assert(scope.confirm.message.includes('selección manual'));
  assert(scope.confirm.message.includes('sin certificar'));
  scope.confirm.callback();
  const sent = events.find(e=>e.type==='launch-preview-web-port');
  assert.equal(sent.id,entry.id);
  assert.deepEqual(Object.keys(sent).sort(),['id','type']);
  assert.equal(ui.previewCatalog.games.find(g=>g.id===entry.id).runtime_verified,false);
  events.length=0;
  const broken=preview.games.find(g=>g.link_status==='broken');
  scope.xmbOfficialPreviewLaunch(broken.id);
  assert.equal(events[0].toast,'Enlace roto');
  scope.window.chrome=null;
  events.length=0;
  scope.xmbOfficialPreviewLaunch(entry.id);
  assert(events[0].toast.includes('WebView2'));
  assert(events[0].toast.includes('No se fuerzan iframes'));
  ui.state.preview_web_ports=false;
  await scope.xmbLoadOfficialPorts();
  assert.equal(ui.previewCatalog,null);
  assert.equal(scope.xmbOfficialPreviewEntry(entry.id),null);
  scope.xmbOfficialDetails(entry);
  assert(!scope.side.opts.some(o=>o.t==='Probar enlace · experimental'));
  console.log('Preview opt-in, category separation, requirements, ID-only launch and browser fallback: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
