/** Decode and share display thumbnails without losing failed requests. */
export class ThumbnailPipeline {
  constructor(fetchDataUrl, { createImage = () => new Image(), timeoutMs = 20000, onState = () => {} } = {}) {
    this.fetchDataUrl = fetchDataUrl;
    this.createImage = createImage;
    this.timeoutMs = timeoutMs;
    this.onState = onState;
    this.ready = new Map();
    this.pending = new Map();
    this.states = new Map();
  }

  stateOf(imageId) {
    return this.states.get(imageId) || 'loading';
  }

  async load(imageId, { retry = false } = {}) {
    if (!imageId) throw new Error('A photo ID is required.');
    if (this.pending.has(imageId)) return this.pending.get(imageId);
    if (!retry && this.ready.has(imageId)) return this.ready.get(imageId);
    if (!retry && this.states.get(imageId) === 'failed') throw new Error('Thumbnail failed. Retry to load it again.');
    if (retry) this.ready.delete(imageId);
    this.#setState(imageId, 'loading');
    const pending = this.#withTimeout((async () => {
      const dataUrl = await this.fetchDataUrl(imageId, { retry });
      if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/')) {
        throw new Error('Thumbnail data is unavailable.');
      }
      const image = this.createImage();
      await new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error('Thumbnail could not be decoded.'));
        image.src = dataUrl;
      });
      if (typeof image.decode === 'function') await image.decode();
      return image;
    })()).then(image => {
      this.ready.set(imageId, image);
      this.#setState(imageId, 'ready', image);
      return image;
    }).catch(error => {
      this.ready.delete(imageId);
      this.#setState(imageId, 'failed');
      throw error;
    }).finally(() => this.pending.delete(imageId));
    this.pending.set(imageId, pending);
    return pending;
  }

  clear(imageId) {
    this.ready.delete(imageId);
    this.states.delete(imageId);
  }

  #setState(imageId, state, image) {
    this.states.set(imageId, state);
    this.onState(imageId, state, image);
  }

  #withTimeout(operation) {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Thumbnail loading timed out.')), this.timeoutMs);
    });
    return Promise.race([operation, timeout]).finally(() => clearTimeout(timer));
  }
}
