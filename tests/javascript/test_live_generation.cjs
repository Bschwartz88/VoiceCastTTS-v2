const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {readFileSync}=require('node:fs'),{resolve}=require('node:path');
const source=readFileSync(resolve(__dirname,'../../web/static/studio.js'),'utf8');
const code=source.slice(source.indexOf('let liveListening = null;'),source.indexOf('function clearOutput()'));
async function run(events,{live=true,error=false}={}) {
 const calls=[],elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{value:id==='scriptInput'?'[Ben] Hello.':'1',checked:id==='liveListenCheckbox'&&live,style:{},focus(){},pause(){}});return elements.get(id);};
 const context=vm.createContext({Response,TextDecoder,console,
  document:{getElementById:element},
  LiveListening:class {constructor(){calls.push('start');}enqueue(data){calls.push(['audio',data.url]);}finish(){calls.push('finish');}stop(){calls.push('stop');}fail(){calls.push('listen_error');}},
  fetch:async(url,options)=>{calls.push(['request',url,JSON.parse(options.body||'{}')]);if(error)throw Error('disconnected');return new Response(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''));},
  resetTabTitle(){},stopPreview(){},setImportRendering(){},setBlenderBusy(){},setShown(){},icon(){return '';},
  voicesPayload(){return {};},activePronunciations(){return [];},
  showStatus(title){calls.push(['status',title]);},displayOutput(){calls.push('output');},alert(){}});
 vm.runInContext(code,context);
 const made=await vm.runInContext('generateAudio()',context);
 return {made,calls,element,context};
}
const audio={type:'audio',data:{url:'/piece.wav',duration:2}};
const complete={type:'complete',result:{success:true,audio_url:'/master.wav',lines:[]}};
test('opt-in request forwards pieces and drains preview without replaying master',async()=>{
 const r=await run([audio,complete]);assert.equal(r.made,true);
 assert.equal(r.calls.find(c=>Array.isArray(c)&&c[0]==='request')[2].live_listen,true);
 assert.deepEqual(r.calls.filter(c=>typeof c==='string'),['start','output','finish']);
 assert.ok(r.calls.some(c=>Array.isArray(c)&&c[0]==='audio'));
});
test('default generation creates no live player or extra audio request',async()=>{
 const r=await run([complete],{live:false});assert.equal(r.made,true);assert.ok(!r.calls.includes('start'));
 assert.equal(r.calls.find(c=>Array.isArray(c)&&c[0]==='request')[2].live_listen,false);
});
test('unchecking stops listening and further events cannot create another player',async()=>{
 const r=await run([audio,complete]);r.element('liveListenCheckbox').checked=false;
 vm.runInContext('liveListeningChanged()',r.context);assert.equal(r.calls.at(-1),'stop');
});
test('cancelled render stops listening and is not presented as a complete recording',async()=>{
 const r=await run([audio,{type:'complete',result:{cancelled:true,lines_done:1,total_lines:2}}]);
 assert.equal(r.made,false);assert.ok(r.calls.includes('stop'));assert.ok(!r.calls.includes('finish'));
});
test('network failure and premature EOF stop listening and show failure',async()=>{
 for(const options of [{error:true},{}]) {
  const r=await run([audio],options);assert.equal(r.made,false);assert.ok(r.calls.includes('stop'));
  assert.ok(r.calls.some(c=>Array.isArray(c)&&c[0]==='status'&&c[1]==='Generation failed'));
 }
});
test('Stop stops listening immediately and still sends render cancellation',async()=>{
 const r=await run([audio,complete]);await vm.runInContext('stopRender()',r.context);
 assert.ok(r.calls.some(c=>Array.isArray(c)&&c[0]==='request'&&c[1]==='/api/cancel'));
 assert.equal(r.calls[r.calls.length-2],'stop');
});
