"""Isolated Edge check of the real XMB page. No PSP, emulator or LIVE data.

The fake HTTP boundary serves synthetic images and a muted silent WAV. Actual
HTMLMediaElement playback must start, stop synchronously on launch, and resume
only after the fake process exits. No autoplay policy override is used.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GAME = 'a' * 24


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    browser = next((p for p in (
        Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe') if p.is_file()), None)
    if browser is None:
        raise SystemExit('Microsoft Edge is required for this Windows-only check.')
    def png(size):
        buffer = io.BytesIO()
        Image.new('RGB', size, (24, 53, 76)).save(buffer, 'PNG')
        return buffer.getvalue()
    original, hd = png((480, 272)), png((3840, 2176))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * 22050)
    silence = buffer.getvalue()
    running = [False]
    check = r'''
    const nap=ms=>new Promise(resolve=>setTimeout(resolve,ms));
    async function until(test) { for(let n=0;n<100;n++){if(test())return true;await nap(50);}return false; }
    (async()=>{
      await new Promise(resolve=>{
        const button=document.createElement('button');button.id='media-test-start';
        button.textContent='Start isolated media test';
        button.style.cssText='position:fixed;left:0;top:0;width:220px;height:50px;z-index:99999';
        button.addEventListener('click',()=>{button.remove();resolve();},{once:true});
        document.body.append(button);
      });
      const audio=document.getElementById('xmb-game-audio'); audio.muted=true;
      await refresh(); xmb.mediaSound=true; xmbPreviewGame(null); xmbPreviewGame('aaaaaaaaaaaaaaaaaaaaaaaa');
      const playing=await until(()=>!audio.paused);
      const hdReady=await until(()=>document.getElementById('xmb-game-background').naturalWidth===3840);
      const loopBefore=audio.loop;
      const task=mutate('/api/launch',{id:'aaaaaaaaaaaaaaaaaaaaaaaa'});
      const stoppedImmediately=audio.paused && !audio.getAttribute('src') && !audio.loop;
      await task;
      const silentDuringGame=audio.paused && !audio.getAttribute('src') && ui.state.session.running;
      await request('/fixture/end',{}); await refresh();
      const resumed=await until(()=>!audio.paused);
      const result={playing,loopBefore,hdReady,stoppedImmediately,silentDuringGame,resumed,
        passed:!!(playing && loopBefore && hdReady && stoppedImmediately && silentDuringGame && resumed)};
      xmbPreviewGame(null);ui.appClosed=true;
      const pre=document.createElement('pre');pre.id='media-test-result';pre.textContent=JSON.stringify(result);document.body.append(pre);
    })().catch(error=>{
      const pre=document.createElement('pre');pre.id='media-test-result';pre.textContent=JSON.stringify({passed:false,error:String(error)});document.body.append(pre);
    });
    '''
    def state():
        return {'demo':False, 'devices':[], 'selected_device':None, 'scanning':False,
            'error':None,'recovery_required':False,'runtime_prepared':True,'can_launch_game':True,
            'session':{'running':running[0]}, 'emulator':{'available':True},
            'games':[{'id':GAME,'title':'Synthetic media test','size':100,'format':'ISO',
                      'availability':'ready','on_pc':True,'on_psp':True}],
            'retro':{'roms':[],'emulators':[]},'saves':{'actions':[],'conflicts':[]},
            'retro_saves':{'rows':[]}, 'preflight':{'saves_prepared':True},
            'controller_status':{'connected':True,'mode':'native_dock','can_play':True,
                'synthetic_storage':False,'product_id':461,'protocol_version':3,
                'firmware_revision':775,'capabilities':15,'build_id':0x03070001,
                'host_confirmed':True,'phase':2},
            'product_flow':{'enabled':True,'phase':'ready','purpose':'initial','choice':'none',
                'use_pc_saves':True,'blocking':False,'offer_id':'synthetic-offer'}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def send(self, value, kind='application/json'):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.path=='/':
                value=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('</head>','<script src="/check.js" defer></script></head>')
                return self.send(value.encode(),'text/html; charset=utf-8')
            if self.path in ('/app.js','/style.css'):
                return self.send((ROOT/'web'/self.path[1:]).read_bytes(), 'text/javascript' if self.path.endswith('.js') else 'text/css')
            if self.path=='/check.js':return self.send(check.encode(),'text/javascript')
            if self.path=='/api/state':return self.send(state())
            if self.path=='/api/token':return self.send({'token':'fixture-only'})
            if self.path=='/api/settings':return self.send({'backend':'directx11','scale':3})
            if self.path=='/api/controller-input':return self.send({'connected':False})
            if self.path.startswith('/api/media-hd/'):return self.send(hd,'image/png')
            if self.path.startswith('/api/media/'):
                return self.send(silence,'audio/wav') if self.path.endswith('/sound') else self.send(original,'image/png')
            if self.path.startswith('/api/icon/'):return self.send(original,'image/png')
            if self.path.startswith('/api/graphics'):return self.send({'choice':{'quality':'auto','aspect':'fit','texture':'off'},'plan':{}})
            return self.send({})
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            if self.path=='/api/launch':running[0]=True
            if self.path=='/fixture/end':running[0]=False
            self.send({'ok':True})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='pspdock-media-browser-') as profile:
            url=f'http://127.0.0.1:{server.server_port}/'
            with (Path(profile)/'browser-stderr.txt').open('w') as log:
                process=subprocess.Popen([str(browser),'--headless','--disable-gpu','--mute-audio',
                    '--no-first-run','--disable-background-networking','--disable-extensions',
                    '--user-data-dir='+profile,'--remote-debugging-port=0',url],
                    stdout=subprocess.DEVNULL,stderr=log)
                try:
                    portfile=Path(profile)/'DevToolsActivePort'
                    deadline=time.monotonic()+15
                    while not portfile.exists():
                        if time.monotonic()>deadline:raise RuntimeError('Test browser did not become ready')
                        time.sleep(.05)
                    port=portfile.read_text().splitlines()[0]
                    result=subprocess.run(['node',str(ROOT/'tools/drive_xmb_media_browser.js'),port,url],
                        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40)
                    evidence=json.loads(result.stdout) if result.stdout.strip() else {
                        'passed':False,'error':'No browser result','driver_exit':result.returncode,
                        'stderr_tail':result.stderr[-2000:]}
                finally:
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate();process.wait(timeout=5)
    finally:
        server.shutdown();server.server_close();thread.join()
    print(json.dumps(evidence,indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    raise SystemExit(0 if evidence.get('passed') else 1)


if __name__=='__main__':main()
