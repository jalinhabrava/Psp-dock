// Drive only the isolated validation browser created by the Python harness.
'use strict';
const [port, url] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async()=>{
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(item => item.type === 'page' && item.url === url);
  if (!target) throw Error('Validation target was not found');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const requests = new Map(); let serial=0;
  socket.addEventListener('message', event => {
    const message=JSON.parse(event.data), pending=requests.get(message.id);
    if (!pending) return;
    requests.delete(message.id);
    message.error ? pending.reject(Error(JSON.stringify(message.error))) : pending.resolve(message.result);
  });
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  const call = (method,params={}) => new Promise((resolve,reject)=>{
    const id=++serial;requests.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));
  });
  try {
    for(let n=0;n<100;n++) {
      const found=await call('Runtime.evaluate',{expression:'!!document.getElementById("media-test-start")',returnByValue:true});
      if(found.result.value)break;
      if(n===99)throw Error('Fixture start control missing');
      await sleep(50);
    }
    // A real browser input event unlocks playback. No autoplay policy switch.
    await call('Input.dispatchMouseEvent',{type:'mousePressed',x:80,y:25,button:'left',clickCount:1});
    await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:80,y:25,button:'left',clickCount:1});
    for(let n=0;n<500;n++) {
      const result=await call('Runtime.evaluate',{expression:'document.getElementById("media-test-result")?.textContent || ""',returnByValue:true});
      if(result.result.value){console.log(result.result.value);return;}
      await sleep(50);
    }
    throw Error('Media browser check timed out');
  } finally {
    // This endpoint belongs solely to the temporary test profile.
    socket.send(JSON.stringify({id:++serial,method:'Browser.close'}));
    await sleep(150);socket.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
