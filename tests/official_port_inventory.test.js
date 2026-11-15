'use strict';
// Inventory browsing exercises real callbacks without network, files or a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const start = source.indexOf('function xmbWebPortItems()');
const end = source.indexOf('\nfunction xmbGuestSections()',start);
assert(start >= 0 && end > start);
const game = (id, title, platform, category, state) => ({
  id,title,original_platforms:[platform],category,verification_state:state,
  author:'Example <author>',controls_declared:{gamepad:true,keyboard:false},
  direct_url:'https://example.com/<untrusted>',direct_candidates:[],
  requirements:[{type:'disc',text:'Own original image'}],requires_disc_image:false,
  additional_controls:{l2_r2:null,second_stick:true,mouse:false},
  description:'<script>untrusted</script>',link_status:'active',
  runtime_verified:false,gamepad_tested:false,
});
const catalog = {version:1,source:'https://ported2browser.com/',summary:{listed:3},games:[
  game('01ARZ3NDEKTSV4RRFFQ69G5FAV','Port','PS2','port_candidate','requires_original_files'),
  game('01ARZ3NDEKTSV4RRFFQ69G5FAW','Browser game','Web','web_native','direct_unverified'),
  game('01ARZ3NDEKTSV4RRFFQ69G5FAX','Other port','PC','port_candidate','pending'),
]};
catalog.games[0].requires_disc_image=true;
const events=[];
const ui={officialBrowse:false,officialCatalog:null,officialLoading:false,officialError:'',
  officialFilters:{platform:'',state:'',category:'port_candidate'},webPorts:[],appClosed:false};
const scope={ui,xmb:{nav:{idx:{}},settings:{}},
  xmbRender:()=>events.push('render'),
  request:async route=>{assert.equal(route,'/api/official-web-ports');return catalog;},
  xmbSide:(title,opts,pick)=>{scope.side={title,opts,pick};},
  xmbValues:(title,options,current,pick)=>{scope.values={title,options,current,pick};},
  xmbWebIsoMatches:port=>events.push(['iso',port]),
  xmbWebPortLaunch:()=>{throw Error('Inventory must never launch');},
  xmbWebPortMenu:()=>{throw Error('Inventory must never use launch menu');},
  xmbLoadWebPorts:()=>{},xmbWebIsoOpen:()=>{},
};
vm.createContext(scope);
vm.runInContext(source.slice(start,end),scope);
async function main() {
  // The inventory remains reachable while the curated list is unavailable.
  scope.xmbWebPortItems().find(row=>row.key==='official-inventory').activate();
  assert.equal(ui.officialBrowse,true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ui.officialCatalog,catalog);
  assert.equal(ui.webPorts.length,0,'Metadata must not extend the launch catalog');
  assert.deepEqual(Array.from(scope.xmbOfficialFilteredGames(),g=>g.title),['Port','Other port']);
  let rows=scope.xmbWebPortItems();
  assert(rows.find(row=>row.key==='official-category').s.includes('2 fichas'));
  rows.find(row=>row.key==='official-platform').activate();
  assert(scope.values.options.some(([value])=>value==='PS2'));
  scope.values.pick('PS2');
  assert.deepEqual(Array.from(scope.xmbOfficialFilteredGames(),g=>g.title),['Port']);
  rows=scope.xmbWebPortItems();
  rows.find(row=>row.key==='official-state').activate();
  scope.values.pick('pending');
  rows=scope.xmbWebPortItems();
  assert(rows.some(row=>row.key==='official-empty'));
  assert(rows.some(row=>row.key==='official-platform'));
  assert(rows.some(row=>row.key==='official-category'));
  scope.xmbOfficialFilter('platform','Platform',[['','All']]);scope.values.pick('');
  scope.xmbOfficialFilter('state','State',[['','All']]);scope.values.pick('');
  scope.xmbWebPortItems().find(row=>row.key==='official-category').activate();
  scope.values.pick('web_native');
  assert.deepEqual(Array.from(scope.xmbOfficialFilteredGames(),g=>g.title),['Browser game']);

  scope.xmbOfficialDetails(catalog.games[0]);
  const details=scope.side;
  assert(details.opts.some(row=>row.t==='Controles declarados por la fuente' && row.s==='gamepad'));
  assert(details.opts.some(row=>row.t==='Comprobado' && row.s.includes('sin verificar') && row.s.includes('no')));
  assert(details.opts.some(row=>row.t==='Descripción' && row.s==='<script>untrusted</script>'));
  assert(details.opts.some(row=>row.t==='URL directa (solo texto)' && row.s.includes('https:')));
  details.opts.forEach((row,index)=>details.pick(index));
  assert.equal(events.filter(event=>event[0]==='iso').length,1);
  assert.equal(events.find(event=>event[0]==='iso')[1].id,catalog.games[0].id);
  scope.xmbOfficialDetails(catalog.games[1]);
  assert(!scope.side.opts.some(row=>row.t.includes('ISOs')));
  scope.side.opts.forEach((row,index)=>scope.side.pick(index));
  assert.equal(events.filter(event=>event[0]==='iso').length,1);
  // Side-panel renderer uses textContent for both titles and metadata.
  assert(source.includes('small.textContent = opt.s'));

  for (const invalidSource of ['https://ported2browser.sbs/','https://ported2browser.org/','https://evil.example/']) {
    scope.request=async()=>({...catalog,source:invalidSource});
    await scope.xmbLoadOfficialPorts();
    assert.equal(ui.officialCatalog,null);
    assert(ui.officialError.includes('origen'));
    assert(scope.xmbWebPortItems().some(row=>row.key==='official-retry'));
  }
  scope.request=async()=>{throw Error('Human verification required');};
  await scope.xmbLoadOfficialPorts();
  assert.equal(ui.officialError,'Human verification required');
  scope.request=async()=>catalog;
  await scope.xmbWebPortItems().find(row=>row.key==='official-retry').activate();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ui.officialError,'');
  scope.xmbWebPortItems().find(row=>row.key==='official-back').activate();
  assert.equal(ui.officialBrowse,false);
  assert(scope.xmbWebPortItems().some(row=>row.key==='official-inventory'));
  console.log('Official inventory filters, source boundary, metadata-only details and ISO callback: OK');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
