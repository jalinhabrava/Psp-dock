// PSP Dock's XMB never downloads content automatically without the explicit
// policy setting or the manual right-hand confirmation. Source contract.
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const source=fs.readFileSync(path.resolve(__dirname,'../web/app.js'),'utf8');
const html=fs.readFileSync(path.resolve(__dirname,'../web/index.html'),'utf8');
const retro=source.slice(source.indexOf('const retro = state.retro || {}'),
                        source.indexOf('const partidas = []'));
assert(retro.includes('requires_license_review'));
assert(retro.includes('licencia no comercial'));
assert(retro.includes('installer.managed_path'));
assert(retro.includes('xmbConfirm('));
assert(retro.includes("'/api/install-retro'"));
assert(retro.includes('No se descargan ROMs ni BIOS'));
assert(source.includes("'retro_install_policy'"));
assert(source.includes("'/api/retro-install-status'"));
assert(html.includes('name="retro_install_policy"'));
assert(html.includes('value="auto"'));
assert(html.includes('value="ask"'));
assert(html.includes('value="off"'));
assert(source.indexOf('xmbConfirm(') >= 0);
console.log('Retro downloader XMB: opt-in policy, source, licensing, and confirmation OK');
