'use strict';
// Exercise the actual backdrop and hints: one loop, lifecycle, reduced motion,
// high-DPI bound and correct labels for the active controller.
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const slice=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const frames=new Map();let next=0,paints=0,reduced=false;
const stops=[];
const gradient={addColorStop(position,color){stops.push(color)}};
const ctx=new Proxy({clearRect(){paints++},createRadialGradient:()=>gradient,createLinearGradient:()=>gradient},
  {get:(target,key)=>key in target?target[key]:()=>{}});
const canvas={clientWidth:800,clientHeight:450,getContext:()=>ctx};
const element=()=>({dataset:{},children:[],append(node){this.children.push(node)},replaceChildren(...nodes){this.children=nodes}});
const footer=element();
const properties={};
const root={style:{setProperty:(key,value)=>properties[key]=value}};
let month=10,hour=13;
const scope={document:{hidden:false,createElement:element},ui:{state:{session:{running:false}}},
  xmb:{nav:{cat:0,level:'list'}},devicePixelRatio:3,
  $:id=>id==='xmb-waves'?canvas:id==='xmb'?root:footer,xmbReducedMotion:()=>reduced,
  Date:class {getMonth(){return month}getHours(){return hour}},
  xmbConnectionView:()=>({diagnostic:false,guestMode:false}),
  requestAnimationFrame:fn=>{frames.set(++next,fn);return next},cancelAnimationFrame:id=>frames.delete(id)};
vm.createContext(scope);
vm.runInContext(slice('function xmbMonthColors(','// ==== fin del modelo puro ===='),scope);
vm.runInContext(slice('function xmbApplyTheme(','let xmbWavesFrameId ='),scope);
vm.runInContext(slice('let xmbWavesFrameId =','function xmbEscape('),scope);
vm.runInContext(slice('function xmbRenderHints(','function xmbApplyTheme('),scope);
const step=time=>{const [id,fn]=frames.entries().next().value;frames.delete(id);fn(time)};
scope.xmbWaves();scope.xmbWaves();assert.equal(frames.size,1,'Repeated renders share one loop');
step(0);assert.equal(paints,1);assert.equal(canvas.width,1600);assert.equal(canvas.height,900);
step(16);assert.equal(paints,1,'Paint rate stays below the navigation refresh rate');
step(34);assert.equal(paints,2);
for(const [object,key] of [[scope.document,'hidden'],[scope.ui,'appClosed'],[scope.ui,'appPageHidden'],
  [scope.ui.state.session,'running'],[scope.ui,'webPortActive'],[scope.ui,'webPortHostActive']]){
  object[key]=true;scope.xmbWaves();assert.equal(frames.size,0,'Pause '+key);
  object[key]=false;scope.xmbWaves();assert.equal(frames.size,1,'Resume '+key);
}
reduced=true;step(100);assert.equal(frames.size,0,'Reduced motion paints once');
reduced=false;scope.xmbWaves();step(150);assert.equal(frames.size,1);
const glyphs=()=>footer.children.flatMap(node=>node.children.filter(n=>n.className==='xmb-hint-glyph'));
const labels=()=>footer.children.flatMap(node=>node.children.filter(n=>!n.className).map(n=>n.textContent));
scope.xmbRenderHints({game:{availability:'ready'}});
assert.equal(footer.dataset.controller,'psp');assert.deepEqual(labels(),['Sección','Elegir','Jugar','Opciones']);
assert(glyphs().some(n=>n.dataset.action==='menu'&&n.innerHTML.includes('<path')));
scope.xmb.nav.confirm={};scope.xmbRenderHints(null);
assert(glyphs().some(n=>n.dataset.action==='back'&&n.innerHTML.includes('r="5.5"')));
scope.ui.gamepadId='Xbox Wireless Controller';scope.xmbRenderHints(null);
assert.equal(footer.dataset.controller,'xbox');assert(glyphs().some(n=>n.innerHTML.includes('>A</text>')));
assert(glyphs().some(n=>n.innerHTML.includes('>B</text>')));
scope.ui.nativePadConnected=true;scope.xmbRenderHints(null);assert.equal(footer.dataset.controller,'psp');
scope.ui.state.session.running=true;scope.xmbRenderHints(null);
assert.deepEqual(labels(),['PPSSPP en ejecución']);assert.equal(glyphs().length,0);
console.log('SOCOM XMB backdrop lifecycle, DPR and PSP/Xbox hints: OK');

// Simulate the actual clock update, not just the pure calendar palette.
scope.ui.state.session.running=false;scope.xmb.theme='month';reduced=true;step(200);
const monthly=[];
for(month=0;month<12;month++){
  scope.xmbApplyTheme();assert.equal(frames.size,1,'A calendar update repaints even with reduced motion');
  const palette=scope.xmb.wavePalette;monthly.push(palette.top);
  assert.equal(properties['--xmb-top'],palette.top);
  assert.equal(properties['--xmb-bottom'],palette.bottom);
  stops.length=0;step(250+month*40);
  assert(stops.some(color=>color.includes(palette.glow)), 'Glow uses the calendar colour');
  assert(stops.some(color=>color.includes(palette.edge)), 'Contours use the calendar colour');
  assert(stops.some(color=>color.includes(palette.accent)), 'Accent follows the active palette');
  assert.equal(frames.size,0,'Calendar repaint does not restart motion');
}
assert.equal(new Set(monthly).size,12,'Every month has its own colour');
month=6;hour=13;scope.xmbApplyTheme();step(800);const noon=scope.xmb.wavePalette.top;
hour=1;scope.xmbApplyTheme();step(850);
assert(parseInt(scope.xmb.wavePalette.top.slice(1),16)<parseInt(noon.slice(1),16),'Night is darker than daytime');
assert.notEqual(scope.xmbBackdropPalette('month',6,13).accent,scope.xmbBackdropPalette('month',10,13).accent);
assert.deepEqual(scope.xmbBackdropPalette('dock',0,1),scope.xmbBackdropPalette('dock',10,13),'Fixed Dock theme stays fixed');
console.log('All 12 calendar colours, day/night and stationary canvas refresh: OK');
