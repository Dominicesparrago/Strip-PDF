export function clientToArtboard(clientX, clientY, rect, canvasSize, artboard) {
  const scale = Math.min(canvasSize.width / artboard.width, canvasSize.height / artboard.height);
  const padX = (canvasSize.width - artboard.width * scale) / 2;
  const padY = (canvasSize.height - artboard.height * scale) / 2;
  const pixelX = (clientX - rect.left) * canvasSize.width / rect.width;
  const pixelY = (clientY - rect.top) * canvasSize.height / rect.height;
  return { x: (pixelX - padX) / scale, y: (pixelY - padY) / scale, scale };
}

export function clientDeltaToArtboard(dx, dy, rect, canvasSize, artboard) {
  const scale = Math.min(canvasSize.width / artboard.width, canvasSize.height / artboard.height);
  return {
    x: dx * canvasSize.width / rect.width / scale,
    y: dy * canvasSize.height / rect.height / scale,
  };
}

export function resizeElement(element, handle, dx, dy, { minimum = 16, lockAspect = false, aspectRatio = null } = {}) {
  const start = { x: Number(element.x), y: Number(element.y), width: Number(element.width), height: Number(element.height) };
  const west = handle.includes('w'), east = handle.includes('e');
  const north = handle.includes('n'), south = handle.includes('s');
  let width = start.width + (east ? dx : west ? -dx : 0);
  let height = start.height + (south ? dy : north ? -dy : 0);
  const ratio = Number.isFinite(Number(aspectRatio)) && Number(aspectRatio) > 0
    ? Number(aspectRatio) : start.width / start.height;

  if (lockAspect && ratio > 0 && (west || east || north || south)) {
    let factor;
    if ((west || east) && (north || south)) {
      const widthFactor = width / start.width;
      const heightFactor = height / start.height;
      factor = Math.abs(widthFactor - 1) >= Math.abs(heightFactor - 1) ? widthFactor : heightFactor;
    } else if (west || east) factor = width / start.width;
    else factor = height / start.height;
    factor = Math.max(minimum / start.width, minimum / start.height, factor);
    width = start.width * factor;
    height = start.height * factor;
  } else {
    width = Math.max(minimum, width);
    height = Math.max(minimum, height);
  }

  let x = start.x, y = start.y;
  if (west) x = start.x + start.width - width;
  else if (!east && lockAspect && (north || south)) x = start.x + (start.width - width) / 2;
  if (north) y = start.y + start.height - height;
  else if (!south && lockAspect && (west || east)) y = start.y + (start.height - height) / 2;
  return { ...element, x, y, width, height };
}

const close = (a, b, threshold) => Math.abs(a - b) <= threshold;

export function snapResize(element, proposal, handle, design, threshold = 12) {
  const board = design.artboard || { width: 1200, height: 1600 };
  const others = (design.elements || []).filter(item => item.id !== element.id && item.visible !== false);
  const guides = { x: null, y: null };
  const result = { ...proposal };
  const west = handle.includes('w'), east = handle.includes('e');
  const north = handle.includes('n'), south = handle.includes('s');
  const xEdge = west ? 'left' : east ? 'right' : null;
  const yEdge = north ? 'top' : south ? 'bottom' : null;
  const xPoint = xEdge === 'left' ? result.x : result.x + result.width;
  const yPoint = yEdge === 'top' ? result.y : result.y + result.height;
  const xTargets = [
    { value: 0, guide: 0 }, { value: board.width, guide: board.width }, { value: board.width / 2, guide: board.width / 2 },
  ];
  const yTargets = [
    { value: 0, guide: 0 }, { value: board.height, guide: board.height }, { value: board.height / 2, guide: board.height / 2 },
  ];
  for (const item of others) {
    xTargets.push({ value: item.x, guide: item.x }, { value: item.x + item.width / 2, guide: item.x + item.width / 2 }, { value: item.x + item.width, guide: item.x + item.width });
    yTargets.push({ value: item.y, guide: item.y }, { value: item.y + item.height / 2, guide: item.y + item.height / 2 }, { value: item.y + item.height, guide: item.y + item.height });
  }
  const nearest = (value, targets) => targets.reduce((best, target) => {
    const distance = Math.abs(target.value - value);
    return distance < best.distance ? { ...target, distance } : best;
  }, { value, guide: null, distance: Infinity });
  if (xEdge) {
    const target = nearest(xPoint, xTargets);
    if (target.guide !== null && close(xPoint, target.value, threshold)) {
      const width = xEdge === 'left' ? result.width + result.x - target.value : target.value - result.x;
      if (width >= 16) {
        if (xEdge === 'left') { result.width = width; result.x = target.value; }
        else result.width = width;
        guides.x = target.guide;
      }
    }
  }
  if (yEdge) {
    const target = nearest(yPoint, yTargets);
    if (target.guide !== null && close(yPoint, target.value, threshold)) {
      const height = yEdge === 'top' ? result.height + result.y - target.value : target.value - result.y;
      if (height >= 16) {
        if (yEdge === 'top') { result.height = height; result.y = target.value; }
        else result.height = height;
        guides.y = target.guide;
      }
    }
  }
  return { element: result, guides };
}

export function resizeHandleAtPoint(point, box, tolerance = 9) {
  const handles = {
    nw: [box.left, box.top], n: [box.left + box.width / 2, box.top], ne: [box.left + box.width, box.top],
    w: [box.left, box.top + box.height / 2], e: [box.left + box.width, box.top + box.height / 2],
    sw: [box.left, box.top + box.height], s: [box.left + box.width / 2, box.top + box.height], se: [box.left + box.width, box.top + box.height],
  };
  for (const [direction, [x, y]] of Object.entries(handles)) {
    if (Math.hypot(point.x - x, point.y - y) <= tolerance) return direction;
  }
  return null;
}

export function canResize(element) {
  return Boolean(element && element.visible !== false && !element.locked);
}

const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const clamp = (value, lower, upper) => Math.min(upper, Math.max(lower, value));

export function photoFrameRect(element, image = null) {
  return photoFrameRectForSource(element, image);
}

export function sourceAspectRatio(image) {
  const width = finite(image?.width ?? image?.naturalWidth, 0);
  const height = finite(image?.height ?? image?.naturalHeight, 0);
  if (width > 0 && height > 0) return width / height;
  const ratio = finite(image?.aspectRatio, 0);
  return ratio > 0 ? ratio : null;
}

export function frameAspectRatio(element, image = null) {
  const fixed = { '1:1': 1, '4:3': 4 / 3, '16:9': 16 / 9 }[element?.aspectRatio];
  if (fixed) return fixed;
  if (element?.aspectRatio === 'original') return sourceAspectRatio(image) || null;
  return null;
}

export function photoFrameRectForSource(element, image = null) {
  let { x, y, width, height } = element;
  x = finite(x, 0); y = finite(y, 0);
  width = Math.max(1, finite(width, 1)); height = Math.max(1, finite(height, 1));
  const ratio = frameAspectRatio(element, image);
  if (ratio) {
    if (width / height > ratio) { const next = height * ratio; x += (width - next) / 2; width = next; }
    else { const next = width / ratio; y += (height - next) / 2; height = next; }
  }
  const padding = Math.min(Math.max(0, finite(element.padding, 0)), width / 2 - 0.5, height / 2 - 0.5);
  return { x: x + padding, y: y + padding, width: width - 2 * padding, height: height - 2 * padding };
}

const frameSlotIndex = (element, fallback = 0) => {
  const direct = Number(element?.slotIndex);
  if (Number.isInteger(direct) && direct >= 0) return direct;
  const suffix = String(element?.id || '').match(/(\d+)$/)?.[1];
  return suffix ? Math.max(0, Number(suffix) - 1) : fallback;
};

const ratioForMode = { '1:1': 1, '4:3': 4 / 3, '16:9': 16 / 9 };

function finiteOr(value, fallback, minimum = null) {
  const number = Number(value);
  return Number.isFinite(number) && (minimum === null || number >= minimum) ? number : fallback;
}

function visibleSourceBounds(sourceRatio, frameRatio, crop, mode) {
  const zoom = clamp(finiteOr(crop?.zoom, 1, 1), 1, 4);
  const offsetX = clamp(finiteOr(crop?.offsetX, 0), -1, 1);
  const offsetY = clamp(finiteOr(crop?.offsetY, 0), -1, 1);
  let width, height;
  if (mode === 'contain') {
    const drawnWidth = Math.min(1, sourceRatio / frameRatio);
    const drawnHeight = Math.min(1, frameRatio / sourceRatio);
    width = Math.min(1, 1 / (drawnWidth * zoom));
    height = Math.min(1, 1 / (drawnHeight * zoom));
  } else if (sourceRatio > frameRatio) {
    width = frameRatio / sourceRatio / zoom;
    height = 1 / zoom;
  } else {
    width = 1 / zoom;
    height = sourceRatio / frameRatio / zoom;
  }
  width = Math.min(1, Math.max(0, width));
  height = Math.min(1, Math.max(0, height));
  const leftFree = Math.max(0, 1 - width), topFree = Math.max(0, 1 - height);
  const centerX = 0.5 + offsetX * leftFree / 2, centerY = 0.5 + offsetY * topFree / 2;
  const left = Math.min(Math.max(0, centerX - width / 2), 1 - width);
  const top = Math.min(Math.max(0, centerY - height / 2), 1 - height);
  return { left, top, right: left + width, bottom: top + height, fraction: width * height };
}

export function resolveFrameLayout(design, strip = null, images = [], { adaptive = true } = {}) {
  const board = design?.artboard || {};
  const boardWidth = finiteOr(board.width, 0, Number.EPSILON);
  const boardHeight = finiteOr(board.height, 0, Number.EPSILON);
  if (!(boardWidth > 0 && boardHeight > 0)) return { mode: adaptive ? 'adaptive' : 'legacy', frames: [] };
  const imageMap = new Map((Array.isArray(images) ? images : Object.values(images || {}))
    .filter(image => image && image.id != null).map(image => [String(image.id), image]));
  const slots = Array.isArray(strip?.slots) ? strip.slots : [];
  const records = (Array.isArray(design?.elements) ? design.elements : [])
    .map((element, order) => ({ element, order }))
    .filter(({ element }) => element?.type === 'image_frame')
    .map(({ element, order }) => {
      const x = finiteOr(element.x, 0), y = finiteOr(element.y, 0);
      const width = finiteOr(element.width, 1, Number.EPSILON), height = finiteOr(element.height, 1, Number.EPSILON);
      const slotIndex = frameSlotIndex(element, order);
      const slot = slots[slotIndex] || {};
      const imageId = slot.imageId == null ? null : String(slot.imageId);
      const image = slot.image || imageMap.get(imageId);
      const sourceRatio = sourceAspectRatio(image);
      const slotCrop = slot.crop && typeof slot.crop === 'object' ? slot.crop : {};
      const ratioMode = ['1:1', '4:3', '16:9', 'original'].includes(element.aspectRatio)
        ? element.aspectRatio : (slotCrop.ratio || 'original');
      const targetRatio = ratioForMode[ratioMode] || sourceRatio || width / height;
      const cropMode = slotCrop.mode === 'contain' || slotCrop.mode === 'cover'
        ? slotCrop.mode : (element.cropMode || (ratioMode === 'original' ? 'contain' : 'cover'));
      return { element, order, slotIndex, imageId, sourceRatio, ratioMode, targetRatio,
        cropMode, crop: slotCrop, base: { x, y, width, height } };
    });
  if (!records.length) return { mode: adaptive ? 'adaptive' : 'legacy', artboard: { width: boardWidth, height: boardHeight }, frames: [] };
  const ordered = records.sort((a, b) => a.slotIndex - b.slotIndex || a.base.y - b.base.y || a.order - b.order);
  const meta = design?.imageLayout && typeof design.imageLayout === 'object' ? design.imageLayout : {};
  const defaultLeft = Math.min(...ordered.map(record => record.base.x));
  const defaultRight = Math.max(...ordered.map(record => record.base.x + record.base.width));
  const defaultTop = Math.min(...ordered.map(record => record.base.y));
  const defaultBottom = Math.max(...ordered.map(record => record.base.y + record.base.height));
  const left = Math.min(boardWidth, Math.max(0, finiteOr(meta.left, defaultLeft, 0)));
  const rightPadding = Math.min(boardWidth - left, Math.max(0, finiteOr(meta.right, boardWidth - defaultRight, 0)));
  const top = Math.min(boardHeight, Math.max(0, finiteOr(meta.top, defaultTop, 0)));
  const bottomPadding = Math.min(boardHeight - top, Math.max(0, finiteOr(meta.bottom, boardHeight - defaultBottom, 0)));
  const contentWidth = Math.max(Number.EPSILON, boardWidth - left - rightPadding);
  const configuredFrameWidth = finiteOr(meta.frameWidth, contentWidth, Number.EPSILON);
  const baseFrameWidth = Math.min(contentWidth, configuredFrameWidth);
  const actualGaps = ordered.slice(0, -1).map((record, index) => Math.max(0,
    ordered[index + 1].base.y - (record.base.y + record.base.height)));
  const configuredGap = meta.gap == null ? null : finiteOr(meta.gap, actualGaps[0] || 0, 0);
  const gaps = ordered.slice(0, -1).map((_, index) => configuredGap == null ? actualGaps[index] : configuredGap);
  const availableHeight = Math.max(0, boardHeight - top - bottomPadding - gaps.reduce((sum, value) => sum + value, 0));
  const requiredImageHeight = ordered.reduce((sum, record) => sum + baseFrameWidth / record.targetRatio, 0);
  const scale = requiredImageHeight > 0 ? Math.max(0, Math.min(1, availableHeight / requiredImageHeight)) : 1;
  const frameWidth = baseFrameWidth * scale;
  const frameHeights = ordered.map(record => frameWidth / record.targetRatio);
  const imageStackHeight = frameHeights.reduce((sum, value) => sum + value, 0) + gaps.reduce((sum, value) => sum + value, 0);
  // availableHeight excludes gaps for scale fitting; alignment includes the
  // complete frame stack and therefore subtracts only the frame heights here.
  const unusedHeight = Math.max(0, availableHeight - frameHeights.reduce((sum, value) => sum + value, 0));
  const verticalAlign = String(meta.verticalAlign || 'middle').toLowerCase();
  const stackOffset = ['top', 'start'].includes(verticalAlign) ? 0
    : ['bottom', 'end'].includes(verticalAlign) ? unusedHeight : unusedHeight / 2;
  let cursorY = top + stackOffset;
  const frames = ordered.map((record, index) => {
    const box = adaptive ? { x: left + Math.max(0, (contentWidth - frameWidth) / 2), y: cursorY,
      width: frameWidth, height: frameHeights[index] } : { ...record.base };
    cursorY += frameHeights[index] + (gaps[index] || 0);
    const frameRatio = box.width / box.height;
    const visible = record.sourceRatio ? visibleSourceBounds(record.sourceRatio, frameRatio, record.crop, record.cropMode)
      : { left: 0, top: 0, right: 1, bottom: 1, fraction: 1 };
    return { elementId: String(record.element.id || ''), slotIndex: record.slotIndex, imageId: record.imageId,
      x: box.x, y: box.y, width: box.width, height: box.height, frameAspectRatio: frameRatio,
      ratioMode: record.ratioMode, sourceAspectRatio: record.sourceRatio, cropMode: record.cropMode,
      visibleSourceBounds: visible, visibleSourceFraction: visible.fraction, populated: Boolean(record.sourceRatio) };
  });
  return { mode: adaptive ? 'adaptive' : 'legacy', artboard: { width: boardWidth, height: boardHeight },
    content: { x: left, y: top, width: contentWidth, height: availableHeight, gaps,
      frameWidth, rightPadding, bottomPadding }, scale, requiredImageHeight, imageStackHeight, frames };
}

export function photoFit(imageWidth, imageHeight, frameWidth, frameHeight, crop = {}, fallbackMode = 'cover') {
  const iw = Math.max(1, finite(imageWidth, 1)), ih = Math.max(1, finite(imageHeight, 1));
  const fw = Math.max(1, finite(frameWidth, 1)), fh = Math.max(1, finite(frameHeight, 1));
  const zoom = clamp(finite(crop.zoom, 1), 1, 4);
  const offsetX = clamp(finite(crop.offsetX, 0), -1, 1);
  const offsetY = clamp(finite(crop.offsetY, 0), -1, 1);
  const mode = crop.mode === 'contain' || crop.mode === 'cover' ? crop.mode : fallbackMode;
  if (mode === 'contain') {
    const scale = Math.min(fw / iw, fh / ih) * zoom;
    const drawWidth = iw * scale, drawHeight = ih * scale;
    return { mode, zoom, x: (fw - drawWidth) / 2 + offsetX * Math.abs(fw - drawWidth) / 2,
      y: (fh - drawHeight) / 2 + offsetY * Math.abs(fh - drawHeight) / 2,
      width: drawWidth, height: drawHeight };
  }
  const baseScale = Math.max(fw / iw, fh / ih);
  const sourceWidth = fw / (baseScale * zoom), sourceHeight = fh / (baseScale * zoom);
  const freeX = Math.max(0, iw - sourceWidth), freeY = Math.max(0, ih - sourceHeight);
  return { mode: 'cover', zoom,
    sourceX: clamp((iw - sourceWidth) / 2 + offsetX * freeX / 2, 0, freeX),
    sourceY: clamp((ih - sourceHeight) / 2 + offsetY * freeY / 2, 0, freeY),
    sourceWidth, sourceHeight, width: fw, height: fh };
}

export function panPhotoCrop(crop, dx, dy, imageWidth, imageHeight, frameWidth, frameHeight, fallbackMode = 'cover') {
  const fit = photoFit(imageWidth, imageHeight, frameWidth, frameHeight, crop, fallbackMode);
  const next = { ...crop };
  const iw = Math.max(1, finite(imageWidth, 1)), ih = Math.max(1, finite(imageHeight, 1));
  const fw = Math.max(1, finite(frameWidth, 1)), fh = Math.max(1, finite(frameHeight, 1));
  if (fit.mode === 'contain') {
    const freeX = Math.abs(fw - fit.width), freeY = Math.abs(fh - fit.height);
    next.offsetX = freeX > 0.001 ? clamp(finite(crop.offsetX, 0) + 2 * dx / freeX, -1, 1) : 0;
    next.offsetY = freeY > 0.001 ? clamp(finite(crop.offsetY, 0) + 2 * dy / freeY, -1, 1) : 0;
  } else {
    const freeX = iw - fit.sourceWidth, freeY = ih - fit.sourceHeight;
    next.offsetX = freeX > 0.001 ? clamp(finite(crop.offsetX, 0) - 2 * dx * fit.sourceWidth / (fw * freeX), -1, 1) : 0;
    next.offsetY = freeY > 0.001 ? clamp(finite(crop.offsetY, 0) - 2 * dy * fit.sourceHeight / (fh * freeY), -1, 1) : 0;
  }
  return next;
}
