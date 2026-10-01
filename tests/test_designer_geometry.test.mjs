import test from 'node:test';
import assert from 'node:assert/strict';
import { canResize, clientDeltaToArtboard, clientToArtboard, resizeElement, snapResize, photoFrameRect, photoFit, panPhotoCrop, resolveFrameLayout } from '../frontend/js/designer_geometry.mjs';

const element = { id: 'shape-1', x: 100, y: 200, width: 80, height: 40 };

test('all eight handles resize the expected axes and preserve their opposite anchors', () => {
  const cases = {
    nw: { dx: 10, dy: 8, x: 110, y: 208, width: 70, height: 32, anchorX: 180, anchorY: 240 },
    n:  { dx: 0, dy: 8, x: 100, y: 208, width: 80, height: 32, anchorX: 140, anchorY: 240 },
    ne: { dx: 10, dy: 8, x: 100, y: 208, width: 90, height: 32, anchorX: 100, anchorY: 240 },
    w:  { dx: 10, dy: 0, x: 110, y: 200, width: 70, height: 40, anchorX: 180 },
    e:  { dx: 10, dy: 0, x: 100, y: 200, width: 90, height: 40, anchorX: 100 },
    sw: { dx: 10, dy: 8, x: 110, y: 200, width: 70, height: 48, anchorX: 180, anchorY: 200 },
    s:  { dx: 0, dy: 8, x: 100, y: 200, width: 80, height: 48, anchorX: 140, anchorY: 200 },
    se: { dx: 10, dy: 8, x: 100, y: 200, width: 90, height: 48, anchorX: 100, anchorY: 200 },
  };
  for (const [handle, expected] of Object.entries(cases)) {
    const result = resizeElement(element, handle, expected.dx, expected.dy);
    for (const key of ['x', 'y', 'width', 'height']) assert.equal(result[key], expected[key], `${handle} ${key}`);
    if (expected.anchorX === 180) assert.equal(result.x + result.width, 180, `${handle} right anchor`);
    if (expected.anchorX === 100) assert.equal(result.x, 100, `${handle} left anchor`);
    if (expected.anchorY === 240) assert.equal(result.y + result.height, 240, `${handle} bottom anchor`);
    if (expected.anchorY === 200) assert.equal(result.y, 200, `${handle} top anchor`);
  }
});

test('resize enforces minimum size and keeps the opposite edge fixed', () => {
  const result = resizeElement(element, 'nw', 1000, 1000, { minimum: 24 });
  assert.equal(result.width, 24);
  assert.equal(result.height, 24);
  assert.equal(result.x + result.width, element.x + element.width);
  assert.equal(result.y + result.height, element.y + element.height);
});

test('aspect-locked corner resize preserves ratio and opposite corner', () => {
  const result = resizeElement(element, 'se', 20, 1, { lockAspect: true });
  assert.equal(result.width / result.height, 2);
  assert.equal(result.x, element.x);
  assert.equal(result.y, element.y);
});

test('locked and hidden elements reject pointer resize', () => {
  assert.equal(canResize(element), true);
  assert.equal(canResize({ ...element, locked: true }), false);
  assert.equal(canResize({ ...element, visible: false }), false);
});

test('pointer conversion follows actual canvas rectangles at zoom levels and after scrolling', () => {
  const artboard = { width: 1200, height: 1600 }, canvasSize = { width: 600, height: 800 };
  for (const zoom of [0.5, 0.75, 1, 1.25, 1.5]) {
    const rect = { left: -zoom * 90, top: 31 - zoom * 160, width: 300 * zoom, height: 400 * zoom };
    const point = clientToArtboard(rect.left + rect.width / 3, rect.top + rect.height * 0.375, rect, canvasSize, artboard);
    assert.ok(Math.abs(point.x - 400) < 1e-8, `x at ${zoom}`);
    assert.ok(Math.abs(point.y - 600) < 1e-8, `y at ${zoom}`);
    const delta = clientDeltaToArtboard(12 * zoom, 8 * zoom, rect, canvasSize, artboard);
    assert.ok(Math.abs(delta.x - 48) < 1e-8, `dx at ${zoom}`);
    assert.ok(Math.abs(delta.y - 32) < 1e-8, `dy at ${zoom}`);
  }
  const fitRect = { left: 240, top: 100, width: 375, height: 500 };
  const fitPoint = clientToArtboard(240 + 125, 100 + 250, fitRect, canvasSize, artboard);
  assert.equal(fitPoint.x, 400);
  assert.equal(fitPoint.y, 800);
});

test('resize snaps the dragged edge to strip and peer edges and returns shared guides', () => {
  const source = { ...element, x: 100, y: 100, width: 80, height: 40 };
  const design = { artboard: { width: 300, height: 400 }, elements: [source, { id: 'peer', x: 190, y: 100, width: 50, height: 40 }] };
  const proposal = { ...source, width: 88, height: 40 };
  const result = snapResize(source, proposal, 'e', design);
  assert.equal(result.element.width, 90);
  assert.equal(result.guides.x, 190);
});

test('original mode resolves each frame to the assigned source ratio', () => {
  const design = {
    artboard: { width: 600, height: 1800 },
    imageLayout: { left: 18, right: 18, top: 18, bottom: 18, gap: 18 },
    elements: Array.from({ length: 4 }, (_, index) => ({
      id: `image-${index + 1}`,
      type: 'image_frame',
      x: 18,
      y: 18 + index * 445.5,
      width: 564,
      height: 427.5,
      aspectRatio: 'original',
      cropMode: 'contain',
    })),
  };
  const strip = {
    slots: Array.from({ length: 4 }, (_, index) => ({
      imageId: String(index),
      crop: { mode: 'contain', ratio: 'original', zoom: 1, offsetX: 0, offsetY: 0 },
    })),
  };
  const images = Array.from({ length: 4 }, (_, index) => ({ id: String(index), width: 1536, height: 1024 }));
  const layout = resolveFrameLayout(design, strip, images);
  assert.ok(layout.frames.every((frame) => Math.abs(frame.frameAspectRatio - 1.5) < 1e-9));
  assert.ok(layout.frames.every((frame) => frame.visibleSourceFraction === 1));
  assert.equal(layout.frames[0].width, 564);
  assert.equal(layout.frames[0].height, 376);
  assert.equal(layout.frames[0].y, 121);
  assert.deepEqual(
    photoFrameRect({ x: 18, y: 24, width: 564, height: 376, aspectRatio: 'original' }, { width: 1536, height: 1024 }),
    { x: 18, y: 24, width: 564, height: 376 },
  );
});

test('portrait and landscape cover preserve aspect and expose only available pan axes', () => {
  const portrait = photoFit(1080, 1920, 300, 200, { mode: 'cover', zoom: 1 });
  const landscape = photoFit(1920, 1080, 300, 200, { mode: 'cover', zoom: 1 });
  assert.ok(portrait.sourceHeight < 1920);
  assert.ok(Math.abs(portrait.sourceWidth - 1080) < 1e-9);
  assert.ok(landscape.sourceWidth < 1920);
  assert.ok(Math.abs(landscape.sourceHeight - 1080) < 1e-9);
  assert.equal(panPhotoCrop({ mode: 'cover', zoom: 1, offsetX: 0, offsetY: 0 }, 20, 0, 1080, 1920, 300, 200).offsetX, 0);
});

test('contain fits inside the frame and normalized zoom and pan persist', () => {
  const contain = photoFit(1920, 1080, 300, 200, { mode: 'contain', zoom: 1 });
  assert.equal(contain.width, 300);
  assert.ok(Math.abs(contain.height - 168.75) < 1e-9);
  const crop = panPhotoCrop({ mode: 'contain', zoom: 1, offsetX: 0, offsetY: 0 }, 0, 10, 1920, 1080, 300, 200);
  assert.ok(crop.offsetY > 0);
  const moved = photoFit(1920, 1080, 300, 200, crop);
  assert.ok(moved.y > contain.y);
  const zoomed = photoFit(1920, 1080, 300, 200, { ...crop, zoom: 2 });
  assert.equal(zoomed.width, contain.width * 2);
  assert.equal(zoomed.height, contain.height * 2);
});
