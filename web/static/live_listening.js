/* Small lookahead player: URLs wait in the queue; only six seconds are decoded ahead.
   Each WAV has at most two seconds of samples. Final downloads use the normal master. */
class LiveListening {
  constructor({Context = globalThis.AudioContext || globalThis.webkitAudioContext,
               fetchAudio = globalThis.fetch.bind(globalThis),
               status = () => {}, later = (fn, ms) => globalThis.setTimeout(fn, ms),
               cancel = timer => globalThis.clearTimeout(timer)} = {}) {
    this.context = new Context();
    this.ready = this.context.resume(); // Called from Generate's user gesture.
    this.fetchAudio = fetchAudio;
    this.status = status;
    this.later = later;
    this.cancel = cancel;
    this.abort = new AbortController();
    this.queue = [];
    this.sources = new Set();
    this.nextTime = 0;
    this.closed = false;
    this.complete = false;
    this.busy = false;
    this.status('Waiting for the first line…');
    Promise.resolve(this.ready).catch(() => this.fail());
  }
  enqueue(piece) {
    if (this.closed) return;
    this.queue.push(piece);
    this.pump();
  }
  finish() {
    this.complete = true;
    this.pump();
  }
  fail() {
    this.stop();
    this.status('Listening unavailable. The full recording will still be prepared.');
  }
  stop() {
    if (this.closed) return;
    this.closed = true;
    this.abort.abort();
    this.cancel(this.timer);
    this.queue.length = 0;
    for (const source of this.sources) { source.onended = null; source.stop(); }
    this.sources.clear();
    Promise.resolve(this.context.close()).catch(() => {});
    this.status('');
  }
  async pump() {
    if (this.closed || this.busy) return;
    this.cancel(this.timer);
    this.busy = true;
    try {
      await this.ready;
      while (!this.closed && this.queue.length && Math.max(this.nextTime, this.context.currentTime + 0.05) + (Number(this.queue[0].duration) || 2) <= this.context.currentTime + 6.05) {
        const piece = this.queue.shift();
        const response = await this.fetchAudio(piece.url, {signal:this.abort.signal});
        if (!response.ok) throw new Error('Live audio unavailable');
        const buffer = await this.context.decodeAudioData(await response.arrayBuffer());
        if (this.closed) return;
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context.destination);
        const start = Math.max(this.nextTime, this.context.currentTime + 0.05);
        this.nextTime = start + buffer.duration;
        this.sources.add(source);
        source.onended = () => { this.sources.delete(source); source.disconnect(); this.pump(); };
        source.start(start);
        this.status('Listening while preparing. Uncheck to stop listening.');
      }
      if (!this.closed) {
        if (this.complete && !this.queue.length && !this.sources.size) {
          this.stop();
          this.status('Listening finished. Use Play to hear the completed recording again.');
        } else {
          this.timer = this.later(() => this.pump(), 100);
        }
      }
    } catch (_) {
      if (!this.closed) this.fail();
    } finally {
      this.busy = false;
    }
  }
}
if (typeof module !== 'undefined' && module.exports) module.exports = LiveListening;
else globalThis.LiveListening = LiveListening;
