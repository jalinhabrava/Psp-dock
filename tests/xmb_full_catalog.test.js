// Large catalogue: every indexed title remains visible without making a
// directory entry look like a playable WebView2 runtime.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname,'..');
const code = fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const source = JSON.parse(fs.readFileSync(path.join(root,'web/catalog-snapshot.json'),'utf8'));
const curated = JSON.parse(fs.readFileSync(path.join(root,'web/ports.json'),'utf8'));
assert(source.games.length >= 1300);
assert(curated.games.some(g=>g.id==='nfs-underground-2'));
const scope={URL,ui:{
  webPorts:source.games.slice(),webPortFilter:'all',webPortLetter:'',
  webPortSearch:'',webPortCatalogCount:source.games.length,
  webPortListRows:[],webPortListKey:'',
}};
vm.createContext(scope);
const start=code.indexOf('function xmbWebPortItemsExpanded(');
const end=code.indexOf('function xmbGuestSections(',start);
assert(start>0&&end>start);
vm.runInContext(code.slice(start,end),scope);
const first=scope.xmbWebPortItemsExpanded();
assert.equal(first.length,source.games.length+4);
assert.equal(first[0].t,'Buscar juego por nombre');
assert.equal(first[1].t,'Filtrar juegos');
assert.equal(first[2].t,'Carpeta de ISOs propias');
assert.equal(scope.xmbWebPortItemsExpanded(),first,
  'The UI must memoize the >1300 rows across periodic PSP-Dock refreshes');
assert(first.slice(4).every(row=>row.chip[1]==='FICHA'||row.chip[1]==='DATOS'),
  'Unverified directory listings must not be shown as verified playable games');

scope.ui.webPortFilter='iso';
let rows=scope.xmbWebPortItemsExpanded();
assert(rows.length>=4);
assert(rows.slice(4).every(row=>row.webPort.requires_files===true));
scope.ui.webPortFilter='all';
scope.ui.webPortLetter='M';
rows=scope.xmbWebPortItemsExpanded();
assert(rows.slice(4).every(row=>row.webPort.title.toUpperCase().startsWith('M')));
scope.ui.webPortLetter='';
scope.ui.webPortSearch='minecraft classic';
rows=scope.xmbWebPortItemsExpanded();
assert(rows.slice(4).some(row=>row.webPort.id==='minecraft-classic'));
scope.ui.webPortSearch='zzzzzzzz-unlikely';
rows=scope.xmbWebPortItemsExpanded();
assert.equal(rows.length,5);
assert.equal(rows[4].t,'No se han encontrado juegos');

const validatorStart=code.indexOf('function xmbValidatedWebPort(');
const validatorEnd=code.indexOf('async function xmbLoadWebPorts(',validatorStart);
vm.runInContext(code.slice(validatorStart,validatorEnd),scope);
assert(source.games.every(row=>scope.xmbValidatedWebPort(row)),
  'The entire imported snapshot must survive the frontend URL validator');
const sample=source.games[0];
assert.equal(scope.xmbValidatedWebPort({...sample,
  source_page:'https://127.0.0.1/games/'+sample.id+'/'}),false);
assert.equal(scope.xmbValidatedWebPort({...sample,
  url:'https://socomlegacy.com/'}),false,
  'A metadata-only entry must not silently acquire an executable URL');
console.log('Full imported XMB catalogue ('+source.games.length+
  ') filters, memoization and non-playable gating: OK');
