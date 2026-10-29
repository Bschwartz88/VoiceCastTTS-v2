const test = require('node:test');
const assert = require('node:assert/strict');
const LiveListening = require('../../web/static/live_listening.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(fetchAudio) {
  const starts=[], timers=new Map(), statuses=[];
  const context={currentTime:0,destination:{},closed:false,
    resume:async()=>{},close:async function(){this.closed=true;},
    decodeAudioData:async()=>({duration:2}),
    createBufferSource(){return {connect(){},disconnect(){},stop(){this.stopped=true;},start(at){starts.push({at,source:this});}}}};
  const player=new LiveListening({Context:function(){return context;},
    fetchAudio:fetchAudio || (async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(0)})),
    status:text=>statuses.push(text),later:fn=>{const key=Symbol();timers.set(key,fn);return key;},cancel:key=>timers.delete(key)});
  return {player,context,starts,timers,statuses};
}
test('decodes only six seconds ahead even when the full story is ready',async()=>{
 const f=fixture();for(let i=0;i<100;i++)f.player.enqueue({url:`/${i}`});await tick();
 assert.equal(f.starts.length,3);assert.equal(f.player.queue.length,97);
 assert.deepEqual(f.starts.map(s=>s.at),[.05,2.05,4.05]);
 f.context.currentTime=2.1;f.starts[0].source.onended();await tick();
 assert.equal(f.starts.length,4);f.player.stop();assert.equal(f.timers.size,0);
});
test('slow synthesis starts the next chunk in the future without skipping its beginning',async()=>{
 const f=fixture();f.player.enqueue({url:'/one'});await tick();
 f.context.currentTime=10;f.starts[0].source.onended();f.player.enqueue({url:'/two'});await tick();
 assert.equal(f.starts[1].at,10.05);f.player.stop();
});
test('completion drains queued audio exactly once, then releases context',async()=>{
 const f=fixture();f.player.enqueue({url:'/one'});f.player.finish();await tick();
 assert.equal(f.context.closed,false);f.context.currentTime=2.1;f.starts[0].source.onended();await tick();
 assert.equal(f.context.closed,true);assert.equal(f.starts.length,1);assert.equal(f.player.sources.size,0);
});
test('stop during fetch prevents late playback and aborts the request',async()=>{
 let release,signal;const f=fixture((url,options)=>{signal=options.signal;return new Promise(resolve=>release=resolve);});
 f.player.enqueue({url:'/one'});await tick();f.player.stop();
 release({ok:true,arrayBuffer:async()=>new ArrayBuffer(0)});await tick();
 assert.equal(signal.aborted,true);assert.equal(f.starts.length,0);assert.equal(f.context.closed,true);
});
test('fetch failure stops preview and reports that full recording continues',async()=>{
 const f=fixture(async()=>({ok:false}));f.player.enqueue({url:'/one'});await tick();
 assert.equal(f.context.closed,true);assert.match(f.statuses.at(-1),/full recording/);
});
test('stopping clears scheduled sources and ignores later generation events',async()=>{
 const f=fixture();f.player.enqueue({url:'/one'});await tick();f.player.stop();f.player.enqueue({url:'/two'});f.player.finish();await tick();
 assert.equal(f.starts[0].source.stopped,true);assert.equal(f.starts.length,1);assert.equal(f.player.queue.length,0);
});
test('no-audio completion releases the context',async()=>{
 const f=fixture();f.player.finish();await tick();assert.equal(f.context.closed,true);
});

test('default browser timer adapters preserve the native global receiver',async()=>{
 const oldLater=globalThis.setTimeout,oldCancel=globalThis.clearTimeout;
 const f=fixture();f.player.stop();let timerCalls=0;
 try {
  globalThis.setTimeout=function(){assert.equal(this,globalThis);timerCalls++;return 1;};
  globalThis.clearTimeout=function(){assert.equal(this,globalThis);timerCalls++;};
  const player=new LiveListening({Context:function(){return f.context;},fetchAudio:async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(0)})});
  player.enqueue({url:'/one',duration:2});await tick();player.stop();
  assert.ok(timerCalls>=2);
 } finally {globalThis.setTimeout=oldLater;globalThis.clearTimeout=oldCancel;}
});
