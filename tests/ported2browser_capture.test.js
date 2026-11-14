'use strict';
// Verify the Chrome capture extension against controlled original-like DOM,
// without any browser networking, real cookies or user data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname,'..');
const dir = path.join(root,'tools/ported2browser_chrome_capture');
const manifest = JSON.parse(fs.readFileSync(
  path.join(dir,'manifest.json'),'utf8'));
assert.equal(manifest.manifest_version,3);
assert.deepEqual(manifest.permissions,['storage']);
assert.deepEqual(manifest.content_scripts[0].matches,
  ['https://ported2browser.com/*']);
assert(!manifest.host_permissions);
const code = fs.readFileSync(path.join(dir,'capture.js'),'utf8');
assert(!/\bfetch\s*\(/.test(code));
assert(!/\bXMLHttpRequest\b|\bWebSocket\b/.test(code));
assert(!/\bchrome\.cookies\b|\bdocument\.cookie\b/.test(code));
assert(!/\bwindow\.location\s*=|\blocation\.assign\s*\(/.test(code));

const ID = '01M6FJS3FP9ER9BWP5FTP5XP7H';
const KEY = 'pspdock_official_capture_v1';
const tasks = [];
const listeners = [];
let store = {};
const text = value => ({textContent:value});
const attr = value => ({getAttribute:name=>name==='href'?value:null});
const card = {
  getAttribute:name=>name==='data-port-id'?ID:null,
  classList:{contains:name=>name==='is-broken'?false:false},
  querySelector:query=>({
    'a.atropos':attr('https://ported2browser.com/ports/'+ID),
    '.card-box h2':text('SOCOM II'),
    '.card-box p':text('Socom Legacy')
  })[query]||null,
  querySelectorAll:()=>[]
};
const location = {
  origin:'https://ported2browser.com',
  href:'https://ported2browser.com/?platform=PlayStation%202',
  pathname:'/',
  search:'?platform=PlayStation%202'
};
const gameButton = {
  getAttribute:key=>({
    'href':'https://socomlegacy.com/',
    'data-id':ID,
    'data-page':'/ports/'+ID
  })[key]||null
};
const control = (name,supported) => ({
  classList:{
    contains:key=>key===(supported?'is-supported':'is-unsupported')
  },
  querySelector:query=>query==='.ctl-label'?text(name):null
});
const disc = {
  classList:{contains:key=>key==='is-disc'},
  textContent:'Requires disc image.'
};
let mode = 'listing';
const document = {
  documentElement:{},
  querySelector:query=>{
    if (mode!=='detail') return null;
    if (query==='.port-page') return {};
    if (query==='.port-page h1') return text('SOCOM II');
    return null;
  },
  querySelectorAll:query=>{
    if (query==='#catalog-results article.card[data-port-id]')
      return mode==='listing'?[card]:[];
    if (query==='template[data-catalog-preload]') return [];
    if (mode!=='detail') return [];
    if (query==='.port-actions .play-list a.button.play[data-id][data-page]')
      return [gameButton];
    if (query==='ul.controls li.ctl')
      return [control('Gamepad',true),control('Keyboard',false),
              control('Touch',false)];
    if (query==='.port-alert') return [disc];
    return [];
  }
};
class MutationObserver {
  constructor(fn){listeners.push(fn)}
  observe(){return undefined}
}
const chrome = {
  storage:{
    local:{
      async get(){return {[KEY]:store[KEY]}},
      async set(value){Object.assign(store,value)}
    }
  }
};
const sandbox = {
  URL,URLSearchParams,Set,Date,console,chrome,location,document,MutationObserver,
  window:{addEventListener:()=>{}},
  setTimeout:fn=>{tasks.push(fn);return tasks.length}
};
vm.createContext(sandbox);
vm.runInContext(code,sandbox);
const flush = async () => {
  assert(tasks.length>0);
  tasks.shift()();
  await new Promise(resolve=>setTimeout(resolve,30));
};
(async()=>{
  await flush();
  assert.equal(Object.keys(store[KEY].cards).length,1);
  assert.equal(store[KEY].cards[ID].detail_url,
               'https://ported2browser.com/ports/'+ID);
  assert.equal(store[KEY].cards[ID].original_platform,'PlayStation 2');
  assert.equal(Object.keys(store[KEY].details).length,0);

  mode='detail';
  location.href='https://ported2browser.com/ports/'+ID;
  location.pathname='/ports/'+ID;
  location.search='';
  listeners[0]();
  await flush();
  assert.equal(Object.keys(store[KEY].details).length,1);
  const game = store[KEY].details[ID];
  assert.equal(game.direct_url,'https://socomlegacy.com/');
  assert.equal(game.status,'direct_candidate');
  assert.equal(game.controls.gamepad,true);
  assert.equal(game.controls.keyboard,false);
  assert.equal(game.requires_disc_image,true);
  assert.equal(game.runtime_verified,false);
  assert.equal(game.gamepad_tested,false);

  // A page with no valid DOM has no authority to add or modify data.
  mode='empty';
  location.href='https://ported2browser.com/empty';
  location.pathname='/empty';
  listeners[0]();
  await flush();
  assert.equal(Object.keys(store[KEY].details).length,1);
  assert.equal(Object.keys(store[KEY].cards).length,1);
  console.log('Ported2Browser passive Chrome capture: original page, '
    +'PS2 filter, direct game link and privacy gates OK');
})().catch(e=>{console.error(e);process.exitCode=1});
