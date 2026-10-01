import test from 'node:test';
import assert from 'node:assert/strict';
import { ThumbnailPipeline } from '../frontend/js/thumbnail_pipeline.mjs';

const DATA_URL = 'data:image/webp;base64,UklGRgAAAAA=';

class FakeImage {
  set src(value) {
    this._src = value;
    queueMicrotask(() => value.includes('broken') ? this.onerror?.() : this.onload?.());
  }
  get src() { return this._src; }
  decode() { return Promise.resolve(); }
}

test('one fetch and decode supplies concurrent photo cards and designer preview', async () => {
  let calls = 0;
  const states = [];
  const pipeline = new ThumbnailPipeline(async () => { calls++; return DATA_URL; }, {
    createImage: () => new FakeImage(), onState: (_id, state) => states.push(state),
  });
  const [card, designer] = await Promise.all([pipeline.load('photo-1'), pipeline.load('photo-1')]);
  assert.equal(card, designer);
  assert.equal(calls, 1);
  assert.deepEqual(states, ['loading', 'ready']);
  assert.equal(await pipeline.load('photo-1'), card);
  assert.equal(calls, 1);
});

test('a missing thumbnail reaches failed state and explicit retry regenerates it', async () => {
  const requests = [];
  const states = [];
  const pipeline = new ThumbnailPipeline(async (_id, options) => {
    requests.push(options);
    return options.retry ? DATA_URL : null;
  }, { createImage: () => new FakeImage(), onState: (_id, state) => states.push(state) });
  await assert.rejects(pipeline.load('photo-2'), /unavailable/);
  assert.equal(pipeline.stateOf('photo-2'), 'failed');
  await assert.rejects(pipeline.load('photo-2'), /Retry/);
  assert.equal(requests.length, 1);
  await pipeline.load('photo-2', { retry: true });
  assert.equal(pipeline.stateOf('photo-2'), 'ready');
  assert.deepEqual(requests, [{ retry: false }, { retry: true }]);
  assert.deepEqual(states, ['loading', 'failed', 'loading', 'ready']);
});

test('decode errors and stalled bridge requests leave loading state', async () => {
  const decode = new ThumbnailPipeline(async () => 'data:image/webp;base64,broken', {
    createImage: () => new FakeImage(),
  });
  await assert.rejects(decode.load('photo-3'), /decoded/);
  assert.equal(decode.stateOf('photo-3'), 'failed');

  const stalled = new ThumbnailPipeline(() => new Promise(() => {}), {
    createImage: () => new FakeImage(), timeoutMs: 5,
  });
  await assert.rejects(stalled.load('photo-4'), /timed out/);
  assert.equal(stalled.stateOf('photo-4'), 'failed');
});
