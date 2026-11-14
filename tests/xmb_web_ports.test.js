// Web-port XMB bridge: curated URLs, PS2 shoulder warning and same-window
// navigation. No real browser, networking, controller or USB device.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'web/ports.json'), 'utf8'));
const start = source.indexOf('function xmbValidatedWebPort(');
const end = source.indexOf('\nfunction xmbGoto(', start);
assert(start >= 0 && end > start);
assert(source.includes("['retro', 'Retro'], ['webports', 'Juegos Web']"));

const events = [], elements = {};
const element = id => elements[id] ||= {textContent:'',src:'',hidden:true,focus:()=>events.push('focus')};
const ui = {state:{session:{running:false}},busy:false,serviceOffline:false,
  webPorts:[], webPortCatalogError:'',webPortActive:null,webPortHostActive:false,
  webPortComboAt:0,appClosed:false};
const scope = {
  URL, ui, $:element,
  window:{chrome:{webview:{postMessage: msg => events.push(['host',msg])}}},
  xmbPreviewGame:()=>events.push('preview-stopped'),
  xmbConnectionView:()=>({waiting:false,product:{blocking:false}}),
  xmbRender:()=>events.push('render'),
  xmbEscape: s=>String(s).replace(/</g,'&lt;'),
  toast: s=>events.push(['toast',s]),
  xmbConfirm:(html,yes,done)=>{events.push(['confirm',html,yes]);scope.continueLaunch=done;},
  xmbSide:()=>events.push('side'),
  request:async path => {assert.equal(path,'/api/web-ports');return catalog;},
};
vm.createContext(scope);
vm.runInContext(source.slice(start,end),scope);
scope.xmbWebIsoMatches=port=>events.push(['iso-match',port.id]);

async function main() {
  await scope.xmbLoadWebPorts();
  assert.equal(ui.webPorts.length,catalog.games.length);
  assert(!ui.webPorts.some(g=>g.url.includes('ported2browser')));
  const pendingPs2 = ui.webPorts.find(g=>g.id==='thps3');
  const iframe = ui.webPorts.find(g=>g.id==='wipeout');
  assert(pendingPs2 && iframe && pendingPs2.embed==='native');
  assert.deepEqual(Array.from(pendingPs2.missing_on_psp),['L2','R2']);
  scope.xmbWebPortLaunch(pendingPs2);
  assert(events.some(e=>e[0]==='toast' && /no está verificado/.test(e[1])));
  assert.equal(events.filter(e=>e[0]==='host').length,0,
    'A landing page that cannot reach gameplay must not be launched as a game');
  events.length=0;
  const ps2={...pendingPs2,launch_state:undefined};

  scope.xmbWebPortLaunch(ps2);
  assert.equal(events.filter(e=>e[0]==='host').length,0,
    'No PS2 launch before accepting the missing-trigger warning');
  assert(events.some(e=>Array.isArray(e) && e[0]==='confirm' &&
    e[1].includes('L2 y R2') && e[1].includes('otro mando')));
  scope.continueLaunch();
  const sent = events.find(e=>Array.isArray(e) && e[0]==='host');
  assert.equal(sent[1].id,'thps3');
  assert.equal(sent[1].type,'launch-web-port');
  assert(!('url' in sent[1]),'Only port IDs pass the privilege boundary');

  events.length=0;
  const needsIso = ui.webPorts.find(g=>g.id==='budokai-tenkaichi-3');
  scope.xmbWebPortLaunch(needsIso);
  assert(events.some(e=>e[0]==='iso-match' && e[1]===needsIso.id),
    'Use a private ISO index, never a generic browser landing page');
  assert.equal(events.filter(e=>e[0]==='host').length,0);
  events.length=0;
  const uncertain={...needsIso,launch_state:undefined};
  scope.xmbWebPortLaunch(uncertain);
  assert(events.some(e=>e[0]==='confirm' && /no verificados/.test(e[1])),
    'Unknown PS2 controller requirements must be clearly marked as uncertain');
  assert.equal(events.filter(e=>e[0]==='host').length,0);
  scope.continueLaunch();
  assert(events.some(e=>e[0]==='host' && e[1].id===uncertain.id));

  events.length=0;
  scope.xmbWebPortLaunch(iframe);
  assert(events.some(e=>Array.isArray(e) && e[0]==='host' && e[1].id==='wipeout'));
  assert(!events.some(e=>Array.isArray(e) && e[0]==='confirm'));

  assert.equal(scope.xmbValidatedWebPort({...ps2,url:'http://127.0.0.1:1234/'}),false);
  assert.equal(scope.xmbValidatedWebPort({...ps2,url:'https://evil.example/'}),false);
  assert.equal(scope.xmbValidatedWebPort({...ps2,url:'https://user:pass@phoboslab.org/'}),false);
  const nfs = ui.webPorts.find(g=>g.id==='nfs-underground-2');
  assert.equal(nfs.url,'https://nfsu2.xyz/');
  assert.equal(iframe.url,'https://phoboslab.org/wipegame/');
  assert.equal(ui.webPorts.length,5,'Do not import web-native minigames or third-party catalogues');
  assert(!ui.webPorts.some(g=>g.platform==='Web'));
  assert(!JSON.stringify(ui.webPorts).includes('ported2browser.sbs'));
  assert(!JSON.stringify(ui.webPorts).includes('ported2browser.org'));

  // Ordinary Chromium may run permitted upstream iframes; never iframe
  // the native-only PS2 titles that explicitly set SAMEORIGIN.
  scope.window.chrome=null;
  events.length=0;
  scope.xmbWebPortLaunch(ps2);
  scope.continueLaunch();
  assert(events.some(e=>e[0]==='toast' && /WebView2/.test(e[1])));
  assert.equal(element('web-port-frame').src,'');
  scope.xmbWebPortLaunch(iframe);
  assert.equal(element('web-port-frame').src,iframe.url);
  assert.equal(element('web-port-view').hidden,false);
  scope.xmbWebPortClose();
  assert.equal(element('web-port-frame').src,'about:blank');
  assert.equal(element('web-port-view').hidden,true);
  console.log('Web ports XMB, PS2 L2/R2 warning, URL boundary and iframe fallback: OK');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
