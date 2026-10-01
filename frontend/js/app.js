import { invoke, invokeAndWait } from './api.js';
import { getState, setState, project } from './state.js';
import { clientToArtboard, clientDeltaToArtboard, resizeElement, resizeHandleAtPoint, snapResize, canResize, photoFrameRect, photoFit, panPhotoCrop, resolveFrameLayout, frameAspectRatio } from './designer_geometry.mjs';
import { ThumbnailPipeline } from './thumbnail_pipeline.mjs';

// GLM's presentation layer gates the Text and Apply-to-All controls on this
// semantic capability handshake. Both capabilities are backed by live APIs.
window.__studio_capabilities = window.__studio_capabilities || {
  design: { addElementType: ['text'], applyToAll: true, liveSync: false },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const bind = name => $(`[data-bind="${name}"]`);
const setText = (name, value) => { const node = bind(name); if (node) node.textContent = value ?? ''; };
const setValue = (name, value) => { const node = bind(name); if (node && value !== undefined && value !== null) node.value = value; };
const setChecked = (name, value) => { const node = bind(name); if (node) node.checked = Boolean(value); };
const thumbnailPipeline = new ThumbnailPipeline(async (imageId, { retry }) => {
  const value = await invoke('images.get_thumbnail', { imageId, retry });
  return value?.dataUrl;
}, { onState: (imageId, state, photo) => {
  for (const node of $$('img[data-image-id]')) {
    if (node.dataset.imageId !== imageId) continue;
    node.dataset.thumbnailState = state;
    if (state === 'ready' && photo) node.src = photo.src;
    else if (state === 'failed') node.removeAttribute('src');
  }
  for (const node of $$('li[data-image-id]')) {
    if (node.dataset.imageId === imageId) node.dataset.thumbnailState = state;
  }
  document.dispatchEvent(new CustomEvent('studio:thumbnail-state', { detail: { imageId, state } }));
} });
const thumbnailCache = thumbnailPipeline.ready;
let aspectLockEnabled = true;
const thumbnailObserver = typeof IntersectionObserver !== 'undefined' ? new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    thumbnailObserver.unobserve(entry.target);
    void fetchThumbnail(entry.target, entry.target.dataset.imageId);
  }
}) : null;

async function fetchThumbnail(node, imageId) {
  if (!imageId) return;
  try {
    const photo = await thumbnailPipeline.load(imageId);
    if (node.dataset.imageId === imageId) node.src = photo.src;
  } catch (error) {
    // The card owns the visible failed state and its Retry action.
    console.warn(`Thumbnail failed for ${imageId}:`, error);
  }
}

function status(message) { setText('app-status', message); }
function dispatch(name, detail) {
  if (name === 'studio:element-selected') detail = { ...detail, stripId: getState().selectedStripId };
  document.dispatchEvent(new CustomEvent(name, { detail }));
}
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c])); }

async function call(method, params = {}, { wait = false, label = method } = {}) {
  status(`${label}…`);
  try {
    const data = wait ? await invokeAndWait(method, params, job => dispatch('studio:job-progress', job)) : await invoke(method, params);
    if (data?.history) setState({ history: data.history });
    status('Ready');
    return data;
  } catch (error) {
    status(error.message || 'Request failed');
    throw error;
  }
}

async function requestStudioDialog(spec) {
  const request = window.__studio_dialog?.request;
  if (typeof request !== 'function') {
    const error = new Error('The in-app dialog is unavailable; the action was not performed.');
    status(error.message);
    throw error;
  }
  return request(spec);
}

async function requestTemplateName({ title, message, value = '', confirmLabel = 'Save', selectOnFocus = false }) {
  const result = await requestStudioDialog({
    kind: 'informational', title, message, confirmLabel, cancelLabel: 'Cancel',
    input: {
      type: 'text', label: 'Template name', value, placeholder: 'Enter a template name',
      required: true, requiredMessage: 'Template name cannot be empty.', maxLength: 100, selectOnFocus,
    },
  });
  if (!result?.confirmed) return null;
  const name = String(result.value ?? '').trim();
  return name || null;
}

async function editTextElement(element) {
  if (!element || element.type !== 'text' || element.locked) return;
  const original = String(element.text ?? element.content ?? 'Text');
  const result = await requestStudioDialog({
    kind: 'informational', title: 'Edit text', message: 'Update the text shown on this strip.',
    confirmLabel: 'Save', cancelLabel: 'Cancel',
    input: { type: 'text', label: 'Text content', value: original, maxLength: 10000, selectOnFocus: true },
  });
  if (!result?.confirmed) return;
  const text = String(result.value ?? '');
  if (text === original) return;
  await applyDesignChanges({ elements: [{ id: element.id, text }] }, 'Edit text content');
}

async function refreshState() {
  const data = await invoke('app.get_state', {});
  const p = data.project || data;
  const currentId = getState().selectedElementId;
  const selectedId = p.design?.elements?.some(item => item.id === currentId)
    ? currentId
    : (getState().project ? (currentId === null ? null : p.design?.elements?.[0]?.id || null) : p.design?.elements?.[0]?.id || null);
  setState({ project: p, history: data.history || { canUndo: false, canRedo: false }, selectedElementId: selectedId });
  renderAll();
}

function renderAll() {
  const p = project();
  if (!p) return;
  setText('project-name', p.name || 'Untitled Project');
  setText('image-count', (p.images || []).length);
  setText('strip-count', (p.strips || []).length);
  setValue('slots-per-strip', p.slotsPerStrip || 4);
  setValue('incomplete-handling', p.incompleteHandling || 'empty');
  const theme = p.theme?.mode || 'system';
  const themeInput = $('[data-action="theme-set"]');
  if (themeInput) themeInput.value = theme;
  renderImages(); renderStrips(); renderDesign(); renderPrintSettings();
  const undo = $('[data-action="design-undo"]'), redo = $('[data-action="design-redo"]');
  if (undo) undo.disabled = !getState().canUndo;
  if (redo) redo.disabled = !getState().canRedo;
}

function renderImages() {
  const p = project(); const list = bind('image-list');
  if (!list || !p) return;
  list.querySelectorAll('img[data-image-id]').forEach(node => thumbnailObserver?.unobserve(node));
  list.replaceChildren();
  for (const image of p.images || []) {
    const li = document.createElement('li');
    const imageId = image.id || image.internalId;
    const displayName = image.displayName || image.originalName || image.name || 'Image';
    li.dataset.imageId = imageId;
    li.dataset.orderIndex = image.orderIndex ?? '';
    li.dataset.orientation = image.orientation || 'unknown';
    li.dataset.status = image.status || 'ready';
    li.dataset.thumbnailState = thumbnailPipeline.stateOf(imageId);
    const thumbnail = document.createElement('img'); thumbnail.width = 56; thumbnail.height = 56;
    thumbnail.alt = displayName; thumbnail.loading = 'lazy'; thumbnail.dataset.imageId = imageId;
    thumbnail.dataset.thumbnailState = li.dataset.thumbnailState;
    thumbnail.style.objectFit = image.previewFit || 'contain';
    const cached = thumbnailCache.get(imageId);
    if (cached) thumbnail.src = cached.src;
    else if (li.dataset.thumbnailState !== 'failed') {
      if (thumbnailObserver) thumbnailObserver.observe(thumbnail);
      else void fetchThumbnail(thumbnail, imageId);
    }
    const name = document.createElement('span'); name.textContent = `${displayName} (${image.width || '?'}×${image.height || '?'})`;
    name.dataset.displayName = displayName;
    const up = document.createElement('button'); up.type = 'button'; up.dataset.action = 'image-move-up'; up.dataset.imageId = imageId; up.setAttribute('aria-label', `Move ${displayName} up`); up.textContent = 'Up';
    const down = document.createElement('button'); down.type = 'button'; down.dataset.action = 'image-move-down'; down.dataset.imageId = imageId; down.setAttribute('aria-label', `Move ${displayName} down`); down.textContent = 'Down';
    const remove = document.createElement('button'); remove.type = 'button'; remove.dataset.action = 'image-remove'; remove.dataset.imageId = imageId; remove.setAttribute('aria-label', `Remove ${displayName}`); remove.textContent = 'Remove';
    li.append(thumbnail, name, up, down, remove); list.append(li);
  }
}

function renderStrips() {
  const p = project(); const list = bind('strip-list');
  if (!list || !p) return;
  list.replaceChildren();
  (p.strips || []).forEach((strip, index) => {
    const li = document.createElement('li'); li.dataset.stripId = strip.id;
    const button = document.createElement('button'); button.type = 'button'; button.dataset.action = 'select-strip'; button.dataset.stripId = strip.id;
    button.textContent = `Strip ${index + 1}: ${(strip.slots || []).filter(slot => slot?.imageId).length}/${(strip.slots || []).length} photos`;
    li.append(button); list.append(li);
  });
  const strips = p.strips || [];
  if (!strips.some(strip => strip.id === getState().selectedStripId)) {
    const nextStripId = strips[0]?.id || null;
    if (getState().selectedStripId !== nextStripId) setState({ selectedStripId: nextStripId });
  }
}

function currentDesign() {
  const p = project();
  const strip = p?.strips?.find(value => value.id === getState().selectedStripId);
  return strip?.designOverride || p?.design || {};
}
function selectedElement() { return currentDesign().elements?.find(item => item.id === getState().selectedElementId) || null; }

let transientFrameLayout = false;

function activeFrameLayout() {
  const p = project();
  const strip = p?.strips?.find(value => value.id === getState().selectedStripId);
  const design = strip?.designOverride || p?.design || {};
  if (transientFrameLayout || !p?.resolvedFrameLayouts) {
    return resolveFrameLayout(design, strip, p?.images || [], { adaptive: p?.frameLayoutMode !== 'legacy' });
  }
  return p.resolvedFrameLayouts[String(getState().selectedStripId)]
    || p.resolvedFrameLayouts.__shared__
    || resolveFrameLayout(design, strip, p?.images || [], { adaptive: p?.frameLayoutMode !== 'legacy' });
}

function effectiveFrameElement(element) {
  if (!element || element.type !== 'image_frame') return element;
  const resolved = activeFrameLayout()?.frames?.find(item => item.elementId === element.id);
  return resolved ? { ...element, x: resolved.x, y: resolved.y, width: resolved.width, height: resolved.height } : element;
}

function sourceForFrame(element) {
  const p = project();
  const strip = p?.strips?.find(value => value.id === getState().selectedStripId);
  const slotIndex = Number(element?.slotIndex ?? (Number((element?.id || '').match(/(\d+)$/)?.[1] || 1) - 1));
  const imageId = strip?.slots?.[slotIndex]?.imageId;
  return p?.images?.find(image => image.id === imageId) || null;
}

function selectStrip(stripId) {
  const previous = selectedElement();
  const logicalKey = previous?.syncKey || previous?.id;
  const strip = project()?.strips?.find(value => value.id === stripId);
  const design = strip?.designOverride || project()?.design || {};
  const equivalent = design.elements?.find(item => item.syncKey === logicalKey || item.id === logicalKey);
  const selectedElementId = equivalent?.id || design.elements?.[0]?.id || null;
  setState({ selectedStripId: stripId, selectedElementId });
  dispatch('studio:element-selected', { id: selectedElementId });
}

function renderDesign() {
  const design = currentDesign(); const canvas = bind('designer-canvas');
  const elements = design.elements || [];
  const renderElements = elements.map(item => effectiveFrameElement(item));
  const board = design.artboard || { width: 600, height: 1800 };
  const boardWidth = Number(board.width), boardHeight = Number(board.height);
  if (canvas && boardWidth > 0 && boardHeight > 0 && Number.isFinite(boardWidth * boardHeight)) {
    const rasterWidth = 600, rasterHeight = Math.max(1, Math.round(rasterWidth * boardHeight / boardWidth));
    if (canvas.width !== rasterWidth || canvas.height !== rasterHeight) { canvas.width = rasterWidth; canvas.height = rasterHeight; }
    canvas.style.setProperty('--artboard-ratio', String(boardWidth / boardHeight));
  }
  // Presentation-only layer mirror; functional editing continues to use the
  // parked select and the structured design in application state.
  window.__studio_design_snapshot = { ...design, elements: renderElements };
  const elementList = bind('design-element-list');
  if (elementList) {
    const selectedId = getState().selectedElementId;
    const current = elements.some(item => item.id === selectedId) ? selectedId : (selectedId ? elements[0]?.id || null : null);
    elementList.innerHTML = elements.map(item => `<option value="${esc(item.id)}">${esc(item.id)} · ${esc(item.type)}</option>`).join('');
    elementList.value = current || '';
    if (current !== selectedId) setState({ selectedElementId: current });
  }
  const element = selectedElement();
  const inspector = $('[data-panel="design-properties"]');
  if (inspector) inspector.disabled = !element;
  const inspectorTarget = $('[data-inspector-target]');
  if (!element && inspectorTarget) inspectorTarget.textContent = 'Nothing selected';
  if (element) {
    const inspectorElement = effectiveFrameElement(element);
    for (const key of ['x','y','width','height']) setValue(`element-${key}`, inspectorElement[key]);
    const fill = bind('element-fill'); if (fill && /^#[0-9a-f]{6}$/i.test(element.fill || '')) fill.value = element.fill;
    const border = bind('element-border-color'); if (border && /^#[0-9a-f]{6}$/i.test(element.borderColor || '')) border.value = element.borderColor;
    setValue('element-border-width', element.borderWidth ?? 0); setValue('element-opacity', element.opacity ?? 1);
    setValue('element-border-radius', element.borderRadius ?? 0); setValue('element-padding', element.padding ?? 0);
    if (element.type === 'text') {
      setValue('text-content', element.text ?? element.content ?? 'Text');
      setValue('text-font-family', element.fontFamily || 'Arial');
      setValue('text-font-size', element.fontSize ?? 64);
      setValue('text-font-weight', element.fontWeight || '400');
      setValue('text-font-style', element.fontStyle || 'normal');
      setValue('text-color', element.fill || '#000000');
      setValue('text-align', element.textAlign || 'center');
      setValue('text-vertical-align', element.verticalAlign || 'middle');
      setValue('text-letter-spacing', element.letterSpacing ?? 0);
      setValue('text-line-height', element.lineHeight ?? 1.2);
    }
    const strip = project()?.strips?.find(value => value.id === getState().selectedStripId);
    const crop = element.type === 'image_frame' ? strip?.slots?.[selectedSlotIndex()]?.crop : null;
    setValue('image-ratio', element.aspectRatio || crop?.ratio || 'original');
    setValue('crop-mode', crop?.mode || element.cropMode || 'cover');
    setValue('crop-zoom', crop?.zoom ?? 1);
    if (inspectorTarget) inspectorTarget.textContent = `${element.id} · ${element.type}${element.locked ? ' · Locked' : ''}`;
  }
  const activeElement = selectedElement();
  const snapToggle = $('[data-action="design-snap"]');
  if (snapToggle) snapToggle.checked = design.snap !== false;
  const aspectLock = $('[data-action="design-aspect-lock"]');
  if (aspectLock) aspectLock.checked = aspectLockEnabled;
  if (element) {
    const locked = Boolean(element.locked);
    const activeStrip = project()?.strips?.find(value => value.id === getState().selectedStripId);
    const activeCrop = element.type === 'image_frame' ? activeStrip?.slots?.[selectedSlotIndex()]?.crop : null;
    for (const node of $$('[data-bind^="element-"], [data-bind^="text-"], [data-bind="image-ratio"], [data-bind^="pattern-"]')) node.disabled = locked;
    for (const node of $$('[data-bind="crop-mode"], [data-bind="crop-zoom"], [data-action="crop-reset"], [data-action="crop-center"], [data-crop-mode]')) node.disabled = element.type !== 'image_frame' || !activeStrip?.slots?.[selectedSlotIndex()]?.imageId;
    $$('[data-crop-mode]').forEach(node => {
      const active = node.dataset.cropMode === (activeCrop?.mode || element.cropMode || 'cover');
      node.classList.toggle('is-active', active); node.setAttribute('aria-pressed', String(active));
    });
    const frameLockNote = $('[data-frame-lock-note]');
    if (frameLockNote) frameLockNote.textContent = element.type === 'image_frame' ? (locked ? 'Frame geometry locked by template' : 'Frame geometry unlocked') : '';
    const unlockFrame = $('[data-action="frame-unlock"]');
    if (unlockFrame) { unlockFrame.hidden = element.type !== 'image_frame'; unlockFrame.textContent = locked ? 'Unlock Frame Layout' : 'Lock Frame Layout'; }
    for (const action of ['element-align', 'element-duplicate', 'element-delete', 'element-reset', 'element-edit-text', 'design-apply-all']) {
      for (const node of $$(`[data-action="${action}"]`)) node.disabled = locked;
    }
  }
  const pattern = activeElement?.type === 'pattern' ? activeElement : (design.pattern || {});
  setValue('pattern-type', pattern.patternType || pattern.type || 'none'); setValue('pattern-scale', pattern.scale ?? 1);
  setValue('pattern-spacing', pattern.spacing ?? 18); setValue('pattern-opacity', pattern.opacity ?? 0);
  setValue('pattern-offset-x', pattern.offsetX ?? 0); setValue('pattern-offset-y', pattern.offsetY ?? 0);
  const activePatternColor = pattern.color || (activeElement?.type === 'pattern' ? pattern.fill : undefined);
  const patternColor = bind('pattern-color'); if (patternColor && /^#[0-9a-f]{6}$/i.test(activePatternColor || '')) patternColor.value = activePatternColor;
  if (!canvas) return;
  const ctx = canvas.getContext('2d'); if (!ctx) return;
  const artboard = design.artboard || { width: 1200, height: 1600 };
  const scale = Math.min(canvas.width / artboard.width, canvas.height / artboard.height);
  const left = (canvas.width - artboard.width * scale) / 2, top = (canvas.height - artboard.height * scale) / 2;
  ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = '#777'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.save(); ctx.translate(left, top); ctx.scale(scale, scale);
  for (const item of renderElements) {
    if (item.visible === false) continue;
    if (item.type === 'text') {
      drawEditorText(ctx, item);
      continue;
    }
    ctx.globalAlpha = item.opacity ?? 1; ctx.fillStyle = item.fill || '#fff';
    ctx.beginPath();
    if (item.borderRadius > 0 && ctx.roundRect) ctx.roundRect(item.x || 0, item.y || 0, item.width || 0, item.height || 0, item.borderRadius);
    else ctx.rect(item.x || 0, item.y || 0, item.width || 0, item.height || 0);
    ctx.fill();
    if (item.type === 'background' && design.pattern?.type && design.pattern.type !== 'none') drawEditorPattern(ctx, design.pattern, artboard);
    if (item.type === 'pattern') {
      ctx.save(); ctx.beginPath(); ctx.rect(item.x, item.y, item.width, item.height); ctx.clip();
      drawEditorPattern(ctx, {
        type: item.patternType || 'dots', scale: item.scale || 1, spacing: item.spacing || 24,
        opacity: item.opacity ?? 1, color: item.color || item.fill || '#000000',
        offsetX: (Number(item.offsetX) || 0) + item.x, offsetY: (Number(item.offsetY) || 0) + item.y,
      }, artboard);
      ctx.restore();
    }
    if (item.type === 'image_frame') {
      const slot = Number(item.slotIndex ?? (Number((item.id || '').match(/(\d+)$/)?.[1] || 1) - 1));
      const strip = project()?.strips?.find(value => value.id === getState().selectedStripId);
      const imageId = strip?.slots?.[slot]?.imageId;
      const image = project()?.images?.find(value => value.id === imageId);
      if (image) {
        const cached = thumbnailCache.get(imageId);
        if (cached) drawEditorPhoto(ctx, cached, item, strip.slots[slot]?.crop || {}, image);
        else if (thumbnailPipeline.stateOf(imageId) !== 'failed' && !thumbnailPipeline.pending.has(imageId)) {
          void thumbnailPipeline.load(imageId).then(renderDesign).catch(error => {
            console.warn(`Designer photo preview failed for ${imageId}:`, error);
          });
        }
      }
    }
    if (item.borderWidth > 0) {
      ctx.beginPath();
      if (item.borderRadius > 0 && ctx.roundRect) ctx.roundRect(item.x || 0, item.y || 0, item.width || 0, item.height || 0, item.borderRadius);
      else ctx.rect(item.x || 0, item.y || 0, item.width || 0, item.height || 0);
      ctx.strokeStyle = item.borderColor || '#000'; ctx.lineWidth = item.borderWidth; ctx.stroke();
    }
  }
  ctx.globalAlpha = 1; ctx.restore();
}

function drawEditorPhoto(ctx, photo, element, crop, source = null) {
  // Source metadata resolves frame geometry. The cached thumbnail is display
  // data only and must never determine the frame ratio.
  const { x, y, width, height } = photoFrameRect(element, source || photo);
  const fit = photoFit(photo.naturalWidth, photo.naturalHeight, width, height, crop, element.cropMode || 'cover');
  ctx.save(); ctx.beginPath();
  if (element.borderRadius > 0 && ctx.roundRect) ctx.roundRect(x, y, width, height, element.borderRadius);
  else ctx.rect(x, y, width, height);
  ctx.clip();
  if (fit.mode === 'contain') {
    ctx.drawImage(photo, x + fit.x, y + fit.y, fit.width, fit.height);
  } else {
    ctx.drawImage(photo, fit.sourceX, fit.sourceY, fit.sourceWidth, fit.sourceHeight, x, y, width, height);
  }
  ctx.restore();
}

function measureEditorText(ctx, text, letterSpacing) {
  const chars = Array.from(text);
  return chars.reduce((sum, char) => sum + ctx.measureText(char).width, 0) + Math.max(0, chars.length - 1) * letterSpacing;
}

function wrapEditorText(ctx, text, width, letterSpacing) {
  const lines = [];
  for (const paragraph of String(text).split('\n')) {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (!words.length) { lines.push(''); continue; }
    let line = '';
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (!line || measureEditorText(ctx, candidate, letterSpacing) <= width) { line = candidate; continue; }
      lines.push(line);
      if (measureEditorText(ctx, word, letterSpacing) <= width) { line = word; continue; }
      let fragment = '';
      for (const char of Array.from(word)) {
        const next = fragment + char;
        if (fragment && measureEditorText(ctx, next, letterSpacing) > width) { lines.push(fragment); fragment = char; }
        else fragment = next;
      }
      line = fragment;
    }
    lines.push(line);
  }
  return lines.length ? lines : [''];
}

function drawEditorText(ctx, element) {
  const family = ['Arial', 'Georgia', 'Courier New'].includes(element.fontFamily) ? element.fontFamily : 'Arial';
  const fontSize = Math.max(1, Number(element.fontSize) || 64);
  const weight = String(element.fontWeight || '400');
  const style = element.fontStyle === 'italic' ? 'italic' : 'normal';
  const letterSpacing = Number(element.letterSpacing) || 0;
  const lineHeight = Math.max(0.5, Number(element.lineHeight) || 1.2);
  const x = Number(element.x) || 0, y = Number(element.y) || 0;
  const width = Math.max(1, Number(element.width) || 1), height = Math.max(1, Number(element.height) || 1);
  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(1, Number(element.opacity ?? 1)));
  ctx.fillStyle = element.fill || '#000000';
  ctx.font = `${style} ${weight} ${fontSize}px "${family}"`;
  ctx.textBaseline = 'top'; ctx.textAlign = 'left';
  ctx.beginPath(); ctx.rect(x, y, width, height); ctx.clip();
  const lines = wrapEditorText(ctx, element.text ?? element.content ?? 'Text', width, letterSpacing);
  const lineStep = fontSize * lineHeight, blockHeight = lineStep * lines.length;
  let lineY = element.verticalAlign === 'top' ? y
    : element.verticalAlign === 'bottom' ? y + height - blockHeight
      : y + (height - blockHeight) / 2;
  for (const line of lines) {
    const lineWidth = measureEditorText(ctx, line, letterSpacing);
    let cursor = element.textAlign === 'left' ? x
      : element.textAlign === 'right' ? x + width - lineWidth
        : x + (width - lineWidth) / 2;
    const chars = Array.from(line);
    chars.forEach((char, index) => {
      ctx.fillText(char, cursor, lineY);
      cursor += ctx.measureText(char).width + (index + 1 < chars.length ? letterSpacing : 0);
    });
    lineY += lineStep;
  }
  ctx.restore();
}

function drawEditorPattern(ctx, pattern, artboard) {
  const spacing = Math.max(2, Number(pattern.spacing) || 18), radius = Math.max(1, Number(pattern.scale) || 1);
  const offsetX = Number(pattern.offsetX) || 0, offsetY = Number(pattern.offsetY) || 0;
  ctx.save(); ctx.globalAlpha = Math.min(1, Math.max(0, Number(pattern.opacity) || 0)); ctx.fillStyle = pattern.color || '#000'; ctx.strokeStyle = pattern.color || '#000';
  const mod = (value, base) => ((value % base) + base) % base;
  if (pattern.type === 'lines' || pattern.type === 'grid') {
    ctx.lineWidth = radius;
    for (let x = mod(offsetX, spacing); x <= artboard.width; x += spacing) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, artboard.height); ctx.stroke(); }
    if (pattern.type === 'grid') for (let y = mod(offsetY, spacing); y <= artboard.height; y += spacing) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(artboard.width, y); ctx.stroke(); }
  } else if (pattern.type === 'dots') {
    for (let y = mod(offsetY, spacing) + spacing / 2; y <= artboard.height; y += spacing) for (let x = mod(offsetX, spacing) + spacing / 2; x <= artboard.width; x += spacing) {
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill();
    }
  }
  ctx.restore();
}

function renderPrintSettings() {
  // Preserve the control the user is editing while earlier async print
  // responses refresh project state. The queued request already captured the
  // edited form, so reverting its DOM value would lose a rapid second edit.
  const active = document.activeElement;
  const liveEdit = active?.closest?.('[data-view="print"]') && active.matches?.('[data-bind]');
  const liveValue = liveEdit ? (active.type === 'checkbox' ? active.checked : active.value) : null;
  const settings = project()?.printSettings || {};
  const spec = project()?.stripSpec || {};
  window.__studio_strip_spec = spec;
  setValue('strip-preset', spec.presetId || 'photobooth-2x6');
  setValue('strip-width-mm', spec.widthMm ?? 50.8); setValue('strip-height-mm', spec.heightMm ?? 152.4);
  setValue('paper-orientation', settings.orientation || 'landscape');
  setValue('copies-mode', settings.stripsPerPage === 'auto' ? 'auto' : 'manual');
  setValue('strips-per-page', settings.stripsPerPage === 'auto' ? (window.__studio_print_layout?.fit?.maximumCount || 5) : (settings.stripsPerPage ?? 5));
  setValue('page-margin-mm', settings.marginMm ?? 5);
  setValue('strip-gap-mm', settings.gapMm ?? 3); setValue('resolution-dpi', settings.resolutionDpi ?? 300); setChecked('cut-guides', settings.cutGuides ?? true);
  if (liveEdit) {
    if (active.type === 'checkbox') active.checked = liveValue;
    else active.value = liveValue;
  }
  syncPrintFormVisibility();
}

function syncPrintFormVisibility() {
  const custom = bind('strip-preset')?.value === 'custom';
  const manual = bind('copies-mode')?.value === 'manual';
  const customFields = $('[data-custom-print-size]');
  const manualField = $('[data-manual-copies]');
  if (customFields) customFields.hidden = !custom;
  if (manualField) manualField.hidden = !manual;
  const pageSize = $('[data-print-page-size]');
  if (pageSize) pageSize.textContent = bind('paper-orientation')?.value === 'portrait'
    ? 'A4 portrait · 210 × 297 mm' : 'A4 landscape · 297 × 210 mm';
}

async function loadTemplates() {
  const data = await invoke('templates.list', {}); const templates = data.templates || data || [];
  setState({ templates });
  const select = bind('template-list');
  if (select) select.innerHTML = templates.map(item => `<option value="${esc(item.templateId || item.id)}">${esc(item.name)}${item.builtIn || item.builtin ? ' (built-in)' : ''}</option>`).join('');
  if (select && project()?.selectedTemplateId) select.value = project().selectedTemplateId;
}

async function loadPresets() {
  const data = await invoke('print.presets.list', {});
  const presets = data.presets || [];
  const select = bind('print-presets');
  if (select) select.innerHTML = presets.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
  setState({ printPresets: presets });
}

async function showValidation() {
  const result = await call('print.validate', {}, { label: 'Validating print' });
  const validation = result.validation || result;
  setText('validation-status', validation.status);
  const list = bind('validation-checks');
  if (list) list.innerHTML = (validation.checks || []).map(item => `<li data-status="${esc(item.status)}"><strong>${esc(item.status)}</strong> ${esc(item.message)}</li>`).join('');
  const layout = validation.layout || result.layout;
  const fit = layout?.fit;
  window.__studio_print_layout = layout;
  const spec = project()?.stripSpec || {};
  const physical = `${Number(spec.widthMm || 50.8).toFixed(1)} × ${Number(spec.heightMm || 152.4).toFixed(1)} mm`;
  setText('fit-message', fit ? (fit.valid
    ? `${fit.effectiveCount} strips fit at ${physical}. Maximum: ${fit.maximumCount}.`
    : `Does not fit. Requested: ${fit.requestedCount}. Maximum safe: ${fit.maximumCount} at ${physical}. Use ${fit.maximumCount}, change orientation, or change paper size.`) : '');
  const useMax = $('[data-action="use-max-copies"]');
  if (useMax) useMax.hidden = !fit || fit.valid || fit.maximumCount < 1;
  dispatch('studio:print-layout-updated', layout);
  dispatch('studio:validation-updated', validation);
}

async function showPreview(page = getState().previewPage || 0) {
  const data = await call('print.preview', { page }, { wait: true, label: 'Rendering preview' });
  const preview = data.preview || data;
  setState({ preview, previewPage: preview.page ?? page }, 'studio:preview-updated');
  const image = bind('pdf-preview');
  if (image) {
    image.src = preview.dataUrl || preview.uri || '';
    image.hidden = !image.src;
  }
  setText('preview-page', (preview.page ?? page) + 1); setText('page-count', preview.pageCount || 1);
  dispatch('studio:preview-scale-change', { page: preview.page ?? page });
  return preview;
}

async function importImages() {
  let data = await call('images.pick', {}, { label: 'Importing photos' });
  if (data?.jobId) {
    data = await (await import('./api.js')).pollJob(data.jobId, job => status(`Importing photos ${job.progress ?? ''}`));
    if (data?.project) setState({ project: data.project });
  } else if (data?.project) setState({ project: data.project });
  if (data?.errors?.length) setText('image-errors', data.errors.map(item => item.error || item.message).join(' · '));
  else setText('image-errors', '');
  await refreshState();
}

let projectMutationQueue = Promise.resolve();
function enqueueProjectMutation(operation) {
  const pending = projectMutationQueue.then(operation);
  projectMutationQueue = pending.catch(() => {});
  return pending;
}

let designUpdateRevision = 0;
function applyDesignChanges(changes, label = 'Edit design') {
  const revision = ++designUpdateRevision;
  return enqueueProjectMutation(async () => {
    const result = await call('design.update', { changes, label, stripId: getState().selectedStripId });
    transientFrameLayout = false;
    if (result.project) setState({ project: result.project, ...(result.history ? { history: result.history } : {}) });
    else if (result.design) setState({ project: { ...project(), design: result.design }, ...(result.history ? { history: result.history } : {}) });
    else await refreshState();
    if (revision === designUpdateRevision) renderDesign();
    return result;
  });
}

let cropUpdateRevision = 0;
let pendingCropTimer = null;
let pendingCropParams = null;
function applyCropChange(params) {
  if (pendingCropTimer) clearTimeout(pendingCropTimer);
  pendingCropTimer = null; pendingCropParams = null;
  const revision = ++cropUpdateRevision;
  return enqueueProjectMutation(async () => {
    const result = await call('strips.set_crop', params);
    if (result.project) setState({ project: result.project, ...(result.history ? { history: result.history } : {}) });
    else await refreshState();
    if (revision === cropUpdateRevision) renderDesign();
  });
}

function currentPrintForm() {
  const custom = bind('strip-preset')?.value === 'custom';
  return {
    stripPresetId: bind('strip-preset')?.value || 'photobooth-2x6',
    ...(custom ? { customWidthMm: Number(bind('strip-width-mm')?.value), customHeightMm: Number(bind('strip-height-mm')?.value) } : {}),
    orientation: bind('paper-orientation')?.value || 'landscape',
    stripsPerPage: bind('copies-mode')?.value === 'manual' ? Number(bind('strips-per-page')?.value || 1) : 'auto',
    marginMm: Number(bind('page-margin-mm')?.value || 5),
    gapMm: Number(bind('strip-gap-mm')?.value || 3),
    resolutionDpi: Number(bind('resolution-dpi')?.value || 300),
    cutGuides: Boolean(bind('cut-guides')?.checked),
  };
}

let printSettingsQueue = Promise.resolve();
let printSettingsRevision = 0;
function persistPrintSettings({ validate = false } = {}) {
  const revision = ++printSettingsRevision;
  const settings = currentPrintForm();
  const update = printSettingsQueue.then(async () => {
    const result = await call('print.update_settings', { settings });
    // Updating history dispatches state-change before the print response arrives.
    // Keep renderAll from restoring values out of the previous project snapshot.
    if (revision === printSettingsRevision && result.project) setState({ project: result.project });
    if (validate && revision === printSettingsRevision) await showValidation();
    return result;
  });
  printSettingsQueue = update.catch(() => {});
  return update;
}

async function reorderFromButtons(imageId, direction) {
  const images = [...(project()?.images || [])];
  const index = images.findIndex(item => item.id === imageId), next = index + direction;
  if (index < 0 || next < 0 || next >= images.length) return;
  [images[index], images[next]] = [images[next], images[index]];
  const updated = await call('images.reorder', { imageIds: images.map(item => item.id) });
  if (updated.project) setState({ project: updated.project }); else await refreshState();
  renderImages();
}

async function handleAction(button) {
  const action = button.dataset.action;
  switch (action) {
    case 'import-images': await importImages(); break;
    case 'project-new': {
      const dirty = project()?.isDirty === true;
      const choice = await requestStudioDialog({ kind: 'confirmation', title: 'New project',
        message: dirty ? 'Start a new project? Unsaved changes will be discarded.' : 'Start a new project?',
        confirmLabel: 'Start New', cancelLabel: 'Cancel' });
      if (!choice?.confirmed) break;
      const result = await call('project.new');
      if (result.project) setState({ project: result.project, selectedElementId: null, selectedStripId: null });
      await refreshState();
      break;
    }
    case 'project-open': await call('project.open', {}, { label: 'Opening project' }); await refreshState(); break;
    case 'project-save': {
      const result = await call('project.save', {}, { label: 'Saving project' });
      if (result.project) setState({ project: result.project });
      else await refreshState();
      break;
    }
    case 'project-save-as': await call('project.save_as', {}, { label: 'Saving project' }); await refreshState(); break;
    case 'image-up': case 'image-move-up': await reorderFromButtons(button.dataset.imageId || button.closest('[data-image-id]')?.dataset.imageId, -1); break;
    case 'image-down': case 'image-move-down': await reorderFromButtons(button.dataset.imageId || button.closest('[data-image-id]')?.dataset.imageId, 1); break;
    case 'image-thumbnail-retry': {
      const imageId = button.dataset.imageId || button.closest('[data-image-id]')?.dataset.imageId;
      if (!imageId) break;
      try {
        await thumbnailPipeline.load(imageId, { retry: true });
        renderDesign();
      } catch (error) {
        status(error.message || 'Thumbnail failed.');
      }
      break;
    }
    case 'image-remove': await call('images.remove', { imageIds: [button.dataset.imageId || button.closest('[data-image-id]')?.dataset.imageId] }); await refreshState(); break;
    case 'generate-strips': {
      const result = await call('strips.generate', { slotsPerStrip: Number(bind('slots-per-strip')?.value || 4), incomplete: bind('incomplete-handling')?.value || 'empty' });
      if (result.project) setState({ project: result.project }); else await refreshState();
      break;
    }
    case 'select-strip': selectStrip(button.dataset.stripId); break;
    case 'template-load': await call('templates.load', { templateId: bind('template-list')?.value }); await refreshState(); break;
    case 'template-save-as': {
      const name = await requestTemplateName({ title: 'Save as template', message: 'Save the current strip design as a reusable template.', confirmLabel: 'Save Template' });
      if (name) { await call('templates.save_as', { name, stripId: getState().selectedStripId }); await loadTemplates(); }
      break;
    }
    case 'template-duplicate': {
      const selected = getState().templates.find(item => (item.templateId || item.id) === bind('template-list')?.value);
      const name = await requestTemplateName({ title: 'Duplicate template', message: 'Create a custom copy of the selected template.',
        value: selected ? `${selected.name} Copy` : '', confirmLabel: 'Duplicate' });
      if (name) { await call('templates.duplicate', { templateId: bind('template-list')?.value, name }); await loadTemplates(); }
      break;
    }
    case 'template-rename': {
      const selected = getState().templates.find(item => (item.templateId || item.id) === bind('template-list')?.value);
      if (!selected) break;
      const name = await requestTemplateName({ title: 'Rename template', message: 'Choose a new name for this template.',
        value: selected.name, confirmLabel: 'Rename', selectOnFocus: true });
      if (name) { await call('templates.rename', { templateId: selected.templateId || selected.id, name }); await loadTemplates(); }
      break;
    }
    case 'template-delete': {
      const templateId = bind('template-list')?.value;
      const choice = templateId ? await requestStudioDialog({ kind: 'destructive', destructive: true, title: 'Delete template',
        message: 'Delete this custom template? This cannot be undone. Built-in templates cannot be deleted.',
        confirmLabel: 'Delete', cancelLabel: 'Cancel' }) : null;
      if (templateId && choice?.confirmed) { await call('templates.delete', { templateId }); await refreshState(); await loadTemplates(); }
      break;
    }
    case 'template-reset': {
      const choice = await requestStudioDialog({ kind: 'destructive', destructive: true, title: 'Reset design',
        message: 'Reset the design to the selected template? Styling changes on this strip will be replaced.',
        confirmLabel: 'Reset Design', cancelLabel: 'Cancel' });
      if (choice?.confirmed) { await call('templates.reset', {}); await refreshState(); }
      break;
    }
    case 'design-undo': await call('design.undo', {}); await refreshState(); button.dispatchEvent(new Event('undo')); break;
    case 'design-redo': await call('design.redo', {}); await refreshState(); button.dispatchEvent(new Event('undo')); break;
    case 'design-snap': await call('design.set_snap', { enabled: button.checked, stripId: getState().selectedStripId }); break;
    case 'element-toggle': { const element = selectedElement(); if (element) await applyDesignChanges({ elements: [{ id: element.id, visible: element.visible === false }] }, 'Toggle visibility'); break; }
    case 'element-lock': { const element = selectedElement(); if (element) await applyDesignChanges({ elements: [{ id: element.id, locked: !element.locked }] }, 'Toggle lock'); break; }
    case 'element-duplicate': {
      const element = selectedElement();
      if (element) {
        const result = await applyDesignChanges({ duplicateElementId: element.id }, 'Duplicate element');
        if (result.createdElementId) setState({ selectedElementId: result.createdElementId });
      }
      break;
    }
    case 'element-delete': {
      const element = selectedElement();
      if (element) {
        const elements = currentDesign().elements || [];
        const index = elements.findIndex(item => item.id === element.id);
        const remaining = elements.filter(item => item.id !== element.id);
        await applyDesignChanges({ deleteElementId: element.id }, 'Delete element');
        setState({ selectedElementId: remaining[Math.min(index, remaining.length - 1)]?.id || null });
      }
      break;
    }
    case 'element-reset': {
      const element = selectedElement();
      if (element) await applyDesignChanges({ resetElementId: element.id }, 'Reset element');
      break;
    }
    case 'element-add': case 'element-add-decoration': case 'element-add-pattern': case 'element-add-text': {
      const type = action === 'element-add-pattern' ? 'pattern'
        : action === 'element-add-decoration' ? 'decoration'
          : action === 'element-add-text' ? 'text'
          : (button.dataset.elementType || 'shape');
      const label = `Add ${type.replaceAll('_', ' ')}`;
      const result = await enqueueProjectMutation(() => call('design.add_element', { type, elementType: type, stripId: getState().selectedStripId, label }));
      if (result.project) setState({ project: result.project, selectedElementId: result.element?.id });
      renderDesign();
      break;
    }
    case 'design-apply-all': {
      const element = selectedElement();
      if (!element || !getState().selectedStripId) break;
      const stripCount = project()?.strips?.length || 0;
      if (stripCount > 6) {
        const choice = await requestStudioDialog({ kind: 'confirmation', title: 'Apply to all strips',
          message: `Apply this element's design to all ${stripCount} strips?`, confirmLabel: 'Apply to All', cancelLabel: 'Cancel' });
        if (!choice?.confirmed) break;
      }
      setText('apply-all-status', 'Applying…');
      try {
        const result = await call('design.apply_to_all', { stripId: getState().selectedStripId, elementId: element.id });
        if (result.project) setState({ project: result.project, ...(result.history ? { history: result.history } : {}) });
        const message = `${result.affectedStrips} strips updated (${result.created} created, ${result.updated} updated${result.skippedLocked ? `, ${result.skippedLocked} locked skipped` : ''}).`;
        setText('apply-all-status', message);
        dispatch('studio:design-applied-all', result);
        dispatch('studio:applyall-result', { ...result, ok: true, message });
      } catch (error) {
        dispatch('studio:applyall-result', { ok: false, message: error.message || 'Could not apply to all strips' });
        throw error;
      }
      break;
    }
    case 'element-apply-scale': {
      const element = selectedElement(), scale = Number(bind('element-scale')?.value);
      if (element && Number.isFinite(scale) && scale > 0) {
        await applyDesignChanges({ elements: [{ id: element.id, width: element.width * scale, height: element.height * scale }] }, 'Scale element');
      }
      break;
    }
    case 'element-align': { const element = selectedElement(); if (element) { await call('design.align', { elementIds: [element.id], alignment: button.dataset.alignment, stripId: getState().selectedStripId, snap: Boolean($('[data-action="design-snap"]')?.checked) }); await refreshState(); } break; }
    case 'element-edit-text': {
      const element = selectedElement();
      await editTextElement(element);
      break;
    }
    case 'frame-unlock': {
      const element = selectedElement();
      if (element?.type === 'image_frame') await applyDesignChanges({ elements: [{ id: element.id, locked: !element.locked }] }, element.locked ? 'Unlock frame layout' : 'Lock frame layout');
      break;
    }
    case 'crop-fill':
    case 'crop-fit': {
      const element = selectedElement();
      const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
      const slotIndex = selectedSlotIndex();
      if (element?.type === 'image_frame' && strip?.slots?.[slotIndex]?.imageId) {
        const mode = action === 'crop-fit' ? 'contain' : 'cover';
        await applyCropChange({ stripId: strip.id, slotIndex, elementId: element.id,
          crop: { ...(strip.slots[slotIndex].crop || {}), mode, zoom: 1, offsetX: 0, offsetY: 0 } });
      }
      break;
    }
    case 'crop-center': {
      const element = selectedElement();
      const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
      const slotIndex = selectedSlotIndex();
      if (element?.type === 'image_frame' && strip?.slots?.[slotIndex]?.imageId) {
        await applyCropChange({ stripId: strip.id, slotIndex, elementId: element.id,
          crop: { ...(strip.slots[slotIndex].crop || {}), offsetX: 0, offsetY: 0 } });
      }
      break;
    }
    case 'crop-reset': {
      const element = selectedElement();
      const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
      const slotIndex = selectedSlotIndex();
      if (element?.type === 'image_frame' && strip?.slots?.[slotIndex]?.imageId)
        await applyCropChange({ stripId: strip.id, slotIndex, elementId: element.id,
          crop: { mode: 'contain', ratio: 'original', zoom: 1, offsetX: 0, offsetY: 0 } });
      break;
    }
    case 'print-update': await persistPrintSettings(); await refreshState(); await showValidation(); await showPreview(0); break;
    case 'print-validate': await persistPrintSettings(); await showValidation(); break;
    case 'use-max-copies': {
      const maximum = window.__studio_print_layout?.fit?.maximumCount;
      if (Number.isInteger(maximum) && maximum > 0) {
        setValue('copies-mode', 'manual'); setValue('strips-per-page', maximum);
        syncPrintFormVisibility();
        await persistPrintSettings(); await showValidation(); await showPreview(0);
      }
      break;
    }
    case 'preview-refresh': await persistPrintSettings(); await showPreview(0); break;
    case 'preview-previous': await persistPrintSettings(); await showPreview(Math.max(0, (getState().previewPage || 0) - 1)); break;
    case 'preview-next': await persistPrintSettings(); await showPreview((getState().previewPage || 0) + 1); break;
    case 'export-pdf': {
      await persistPrintSettings();
      const result = await call('print.export', {}, { wait: true, label: 'Exporting PDF' });
      setText('export-status', result.cancelled ? 'Export cancelled.' : result.path ? `Saved: ${result.path}` : 'PDF export complete'); break;
    }
    case 'preset-save': {
      const name = bind('preset-name')?.value?.trim();
      if (name) { await call('print.presets.save', { name, settings: currentPrintForm() }); await loadPresets(); setText('export-status', `Saved print preset: ${name}`); }
      break;
    }
    case 'preset-load': {
      const preset = getState().printPresets?.find(item => item.id === bind('print-presets')?.value);
      if (preset) {
        const result = await call('print.update_settings', { settings: { ...preset.settings, ...(preset.stripSpec ? { stripSpec: preset.stripSpec } : {}) } });
        if (result.project) setState({ project: result.project });
        await showValidation(); await showPreview(0);
      }
      break;
    }
  }
}

function selectedSlotIndex() {
  const element = selectedElement();
  if (element?.type !== 'image_frame') return 0;
  const index = Number(element.slotIndex ?? (Number((element.id || '').match(/(\d+)$/)?.[1] || 1) - 1));
  return Math.max(0, index);
}

document.addEventListener('click', event => {
  const button = event.target.closest('[data-action]'); if (!button) return;
  if (['design-snap'].includes(button.dataset.action)) return;
  void handleAction(button).catch(error => console.error(error));
});

document.addEventListener('change', event => {
  const node = event.target;
  if (node.matches('[data-action="design-snap"]')) void call('design.set_snap', { enabled: node.checked, stripId: getState().selectedStripId }).then(refreshState).catch(console.error);
  if (node.matches('[data-action="sort-images"]')) void call('images.sort', { mode: node.value }).then(refreshState).catch(console.error);
  if (node.matches('[data-action="theme-set"]')) void call('theme.set', { mode: node.value }).then(data => {
    const effective = data.mode === 'system' ? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : data.effective;
    document.documentElement.dataset.theme = effective || node.value;
    if (project()) setState({ project: { ...project(), theme: data, themeMode: data.mode } });
    dispatch('studio:theme-change', { ...data, effective });
  }).catch(console.error);
  if (node.matches('[data-bind="design-element-list"]')) {
    setState({ selectedElementId: node.value || null });
    dispatch('studio:element-selected', { id: node.value || null });
    renderDesign();
  }
  if (node.matches('[data-bind="template-list"]')) setState({ selectedTemplateId: node.value });
  if (node.matches('[data-action="design-aspect-lock"]')) aspectLockEnabled = node.checked;
  if (node.matches('[data-bind="slots-per-strip"], [data-bind="incomplete-handling"]')) {
    const changes = { slotsPerStrip: Number(bind('slots-per-strip')?.value || 4), incompleteHandling: bind('incomplete-handling')?.value || 'empty' };
    void call('strips.generate', { slotsPerStrip: changes.slotsPerStrip, incomplete: changes.incompleteHandling }).then(refreshState).catch(console.error);
  }
  if (node.matches('[data-bind="crop-mode"]')) {
    const element = selectedElement();
    const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
    const slotIndex = selectedSlotIndex();
    if (element?.type === 'image_frame' && strip?.slots?.[slotIndex]?.imageId)
      void applyCropChange({ stripId: strip.id, slotIndex, elementId: element.id,
        crop: { ...(strip.slots[slotIndex].crop || {}), mode: node.value, zoom: 1, offsetX: 0, offsetY: 0 } }).catch(console.error);
    return;
  }
  if (node.matches('[data-bind="crop-zoom"]')) flushPendingCrop();
  if (node.matches('[data-bind="element-x"], [data-bind="element-y"], [data-bind="element-width"], [data-bind="element-height"], [data-bind="element-fill"], [data-bind="element-border-color"], [data-bind="element-border-width"], [data-bind="element-border-radius"], [data-bind="element-padding"], [data-bind="element-opacity"], [data-bind="image-ratio"]')) {
    const element = selectedElement(); if (!element || element.locked) return;
    if (node.matches('[data-bind="image-ratio"]') && element.type !== 'image_frame') return;
    const numberValue = name => {
      const control = bind(name);
      if (!control) return undefined;
      const value = Number(control.value);
      return Number.isFinite(value) ? value : undefined;
    };
    const fieldMap = {
      'element-x': 'x', 'element-y': 'y', 'element-width': 'width', 'element-height': 'height',
      'element-fill': 'fill', 'element-border-color': 'borderColor', 'element-border-width': 'borderWidth',
      'element-border-radius': 'borderRadius', 'element-padding': 'padding', 'element-opacity': 'opacity',
      'image-ratio': 'aspectRatio',
    };
    const field = fieldMap[node.dataset.bind];
    const numericFields = new Set(['x', 'y', 'width', 'height', 'borderWidth', 'borderRadius', 'padding', 'opacity']);
    const value = numericFields.has(field) ? numberValue(node.dataset.bind) : node.value;
    if (value === undefined) return;
    const elementChanges = { id: element.id, [field]: value };
    const source = element.type === 'image_frame' ? sourceForFrame(element) : null;
    const lockedRatio = element.type === 'image_frame'
      ? (frameAspectRatio(element, source) || (Number(element.width) / Number(element.height)))
      : (Number(element.width) / Number(element.height));
    if (aspectLockEnabled && field === 'width' && element.width && Number.isFinite(lockedRatio) && lockedRatio > 0) {
      elementChanges.height = Number((value / lockedRatio).toFixed(2));
      setValue('element-height', elementChanges.height);
    }
    if (aspectLockEnabled && field === 'height' && element.height && Number.isFinite(lockedRatio) && lockedRatio > 0) {
      elementChanges.width = Number((value * lockedRatio).toFixed(2));
      setValue('element-width', elementChanges.width);
    }
    const changes = { elements: [elementChanges] };
    if (field === 'aspectRatio') {
      const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
      const slotIndex = selectedSlotIndex();
      const slot = strip?.slots?.[slotIndex];
      if (slot?.imageId) {
        const currentCrop = slot.crop || {};
        changes.crop = {
          stripId: strip.id, slotIndex,
          crop: {
            ...currentCrop, ratio: value,
            mode: value === 'original' ? 'contain' : (currentCrop.mode || 'cover'),
            zoom: 1, offsetX: 0, offsetY: 0,
          },
        };
      }
    }
    void applyDesignChanges(changes).catch(console.error);
  }
  if (node.matches('[data-bind="text-content"], [data-bind="text-font-family"], [data-bind="text-font-size"], [data-bind="text-font-weight"], [data-bind="text-font-style"], [data-bind="text-color"], [data-bind="text-align"], [data-bind="text-vertical-align"], [data-bind="text-letter-spacing"], [data-bind="text-line-height"]')) {
    const element = selectedElement();
    if (!element || element.type !== 'text' || element.locked) return;
    const fields = {
      'text-content': 'text', 'text-font-family': 'fontFamily', 'text-font-size': 'fontSize',
      'text-font-weight': 'fontWeight', 'text-font-style': 'fontStyle', 'text-color': 'fill',
      'text-align': 'textAlign', 'text-vertical-align': 'verticalAlign',
      'text-letter-spacing': 'letterSpacing', 'text-line-height': 'lineHeight',
    };
    const field = fields[node.dataset.bind];
    const numeric = new Set(['fontSize', 'letterSpacing', 'lineHeight']);
    const value = numeric.has(field) ? Number(node.value) : node.value;
    if (numeric.has(field) && !Number.isFinite(value)) return;
    void applyDesignChanges({ elements: [{ id: element.id, [field]: value }] }, `Edit text ${field}`).catch(console.error);
  }
  if (node.matches('[data-bind="pattern-type"], [data-bind="pattern-scale"], [data-bind="pattern-spacing"], [data-bind="pattern-opacity"], [data-bind="pattern-color"], [data-bind="pattern-offset-x"], [data-bind="pattern-offset-y"]')) {
    const selected = selectedElement();
    if (selected?.locked) return;
    const patternFields = {
      'pattern-type': selected?.type === 'pattern' ? 'patternType' : 'type',
      'pattern-scale': 'scale', 'pattern-spacing': 'spacing', 'pattern-opacity': 'opacity',
      'pattern-color': 'color', 'pattern-offset-x': 'offsetX', 'pattern-offset-y': 'offsetY',
    };
    const field = patternFields[node.dataset.bind];
    const value = node.dataset.bind === 'pattern-type' || node.dataset.bind === 'pattern-color' ? node.value : Number(node.value);
    if (!Number.isFinite(value) && typeof value !== 'string') return;
    if (selected?.type === 'pattern') {
      void applyDesignChanges({ elements: [{ id: selected.id, [field]: value }] }, 'Edit pattern element').catch(console.error);
    } else {
      void applyDesignChanges({ pattern: { [field]: value } }, 'Edit pattern').catch(console.error);
    }
  }
  if (node.matches('[data-bind="strip-preset"], [data-bind="paper-orientation"], [data-bind="copies-mode"], [data-bind="strips-per-page"], [data-bind="strip-width-mm"], [data-bind="strip-height-mm"], [data-bind="page-margin-mm"], [data-bind="strip-gap-mm"], [data-bind="cut-guides"]')) {
    syncPrintFormVisibility();
    void persistPrintSettings({ validate: true }).catch(console.error);
  }
});

document.addEventListener('input', event => {
  const node = event.target;
  if (!node.matches('[data-bind="crop-zoom"]')) return;
  if (selectedElement()?.type !== 'image_frame') return;
  const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
  const slot = strip?.slots?.[selectedSlotIndex()];
  if (!slot?.imageId) return;
  queueCropChange({ stripId: getState().selectedStripId, slotIndex: selectedSlotIndex(), elementId: selectedElement().id,
    crop: { ...(slot.crop || {}), zoom: Number(node.value) } });
});

const canvas = bind('designer-canvas');
const canvasInteractionRoot = canvas?.parentElement;
let drag = null;
function canvasMetrics(rect, design) {
  const artboard = design.artboard || { width: 1200, height: 1600 };
  const scale = Math.min(canvas.width / artboard.width, canvas.height / artboard.height);
  return { artboard, scale, padX: (canvas.width - artboard.width * scale) / 2, padY: (canvas.height - artboard.height * scale) / 2 };
}

function queueCropChange(params, delay = 180) {
  previewCrop(params.stripId, params.slotIndex, params.crop);
  if (pendingCropTimer) clearTimeout(pendingCropTimer);
  pendingCropParams = params;
  pendingCropTimer = setTimeout(() => {
    const pending = pendingCropParams;
    pendingCropTimer = null; pendingCropParams = null;
    if (pending) void applyCropChange(pending).catch(console.error);
  }, delay);
}

function flushPendingCrop() {
  if (!pendingCropParams) return;
  const params = pendingCropParams;
  void applyCropChange(params).catch(console.error);
}

function screenBox(element, rect, design) {
  const { artboard, scale, padX, padY } = canvasMetrics(rect, design);
  const px = canvas.width / rect.width, py = canvas.height / rect.height;
  return {
    left: rect.left + (padX + element.x * scale) / px,
    top: rect.top + (padY + element.y * scale) / py,
    width: element.width * scale / px,
    height: element.height * scale / py,
    artboard,
  };
}

function resizeDirectionAtPointer(event, element, rect, design) {
  const nodes = $$('#designer-overlay .overlay-handle[data-dir]');
  for (const node of nodes) {
    const handle = node.getBoundingClientRect();
    if (!handle.width || !handle.height) continue;
    const padding = 4;
    if (event.clientX >= handle.left - padding && event.clientX <= handle.right + padding
      && event.clientY >= handle.top - padding && event.clientY <= handle.bottom + padding) return node.dataset.dir;
  }
  return resizeHandleAtPoint({ x: event.clientX, y: event.clientY }, screenBox(element, rect, design));
}

function replacePreviewElement(element) {
  transientFrameLayout = true;
  const p = project(), design = currentDesign();
  const elements = (design.elements || []).map(item => item.id === element.id ? element : item);
  const previewLayout = element.type === 'image_frame'
    ? { ...(design.imageLayout || {}), frameWidth: Number(element.width) }
    : design.imageLayout;
  const previewDesign = { ...design, ...(previewLayout ? { imageLayout: previewLayout } : {}), elements };
  const stripId = getState().selectedStripId;
  if (stripId) {
    const strips = (p.strips || []).map(strip => strip.id === stripId ? { ...strip, designOverride: previewDesign } : strip);
    setState({ project: { ...p, strips } });
  } else setState({ project: { ...p, design: previewDesign } });
}

function cropForDrag(current, dx, dy) {
  const frame = photoFrameRect(current.element, current.image);
  const image = current.image;
  if (!image?.width || !image?.height) return { ...current.crop };
  return panPhotoCrop(current.crop, dx, dy, image.width, image.height,
    frame.width, frame.height, current.element.cropMode || (current.element.aspectRatio === 'original' ? 'contain' : 'cover'));
}

let cropPreviewFrame = 0;
function previewCrop(stripId, slotIndex, crop) {
  const strip = project()?.strips?.find(item => item.id === stripId);
  const slot = strip?.slots?.[slotIndex];
  if (!slot) return;
  slot.crop = crop;
  if (!cropPreviewFrame) cropPreviewFrame = requestAnimationFrame(() => {
    cropPreviewFrame = 0;
    renderDesign();
  });
}

function startPointerCapture(event) {
  try { canvas.setPointerCapture(event.pointerId); } catch {}
}

function releasePointerCapture(event) {
  try { if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId); } catch {}
}

canvasInteractionRoot?.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  const rect = canvas.getBoundingClientRect(), design = currentDesign();
  const selected = selectedElement();
  if (!canResize(selected)) return;
  const selectedBox = effectiveFrameElement(selected);
  const direction = resizeDirectionAtPointer(event, selectedBox, rect, design);
  if (!direction) return;
  event.preventDefault(); event.stopImmediatePropagation();
  const metrics = canvasMetrics(rect, design);
  drag = { type: 'resize', direction, element: { ...selectedBox }, design, rect, canvasSize: { width: canvas.width, height: canvas.height }, artboard: metrics.artboard,
    startClientX: event.clientX, startClientY: event.clientY, lockAspect: aspectLockEnabled,
    aspectRatio: frameAspectRatio(selected, sourceForFrame(selected)), preview: { ...selectedBox }, pointerId: event.pointerId };
  startPointerCapture(event);
}, true);

canvasInteractionRoot?.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  const rect = canvas.getBoundingClientRect(), design = currentDesign();
  const withinCanvas = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
  if (!withinCanvas) return;
  flushPendingCrop();
  const metrics = canvasMetrics(rect, design);
  const point = clientToArtboard(event.clientX, event.clientY, rect, { width: canvas.width, height: canvas.height }, metrics.artboard);
  const logicalX = point.x, logicalY = point.y;
  const activeStrip = project()?.strips?.find(value => value.id === getState().selectedStripId);
  const visible = [...(design.elements || [])].filter(item => item.visible !== false && item.type !== 'background').reverse();
  const containsPoint = item => {
    const box = effectiveFrameElement(item);
    return logicalX >= box.x && logicalX <= box.x + box.width
      && logicalY >= box.y && logicalY <= box.y + box.height;
  };
  // Preserve the active layer for drags through overlapping artwork (for
  // example, a pattern placed over a shape). Layers remain the way to change
  // selection when the pointer lands inside more than one element.
  const activeSelectionId = bind('design-element-list')?.value || getState().selectedElementId;
  const activeElement = visible.find(item => item.id === activeSelectionId && containsPoint(item));
  const hit = activeElement || visible.find(containsPoint);
  if (hit) {
    if (getState().selectedElementId !== hit.id) {
      setState({ selectedElementId: hit.id });
      dispatch('studio:element-selected', { id: hit.id });
    }
  } else {
    setState({ selectedElementId: null });
    dispatch('studio:element-selected', { id: null });
    dispatch('studio:snap-guides', { x: null, y: null });
  }
  if (hit?.type === 'image_frame' && activeStrip && !event.altKey) {
    const slotIndex = Number(hit.slotIndex ?? (Number((hit.id || '').match(/(\d+)$/)?.[1] || 1) - 1));
    const slot = activeStrip.slots?.[slotIndex];
    if (slot?.imageId) {
      const crop = { ...(slot.crop || { mode: hit.cropMode || (hit.aspectRatio === 'original' ? 'contain' : 'cover'), zoom: 1, offsetX: 0, offsetY: 0 }) };
      const image = project()?.images?.find(value => value.id === slot.imageId);
      const hitBox = effectiveFrameElement(hit);
      drag = { type: 'crop', slotIndex, stripId: activeStrip.id, startClientX: event.clientX, startClientY: event.clientY,
        offsetX: crop.offsetX || 0, offsetY: crop.offsetY || 0, zoom: crop.zoom || 1, crop,
        mode: hit.cropMode || crop.mode || (hit.aspectRatio === 'original' ? 'contain' : 'cover'),
        element: { ...hitBox }, image, rect, canvasSize: { width: canvas.width, height: canvas.height }, artboard: metrics.artboard,
        frameWidth: hitBox.width * metrics.scale, frameHeight: hitBox.height * metrics.scale, pointerId: event.pointerId };
      startPointerCapture(event); return;
    }
  }
  if (!hit || hit.locked) return;
  drag = { type: 'move', element: { ...hit }, design, rect, canvasSize: { width: canvas.width, height: canvas.height }, artboard: metrics.artboard,
    startClientX: event.clientX, startClientY: event.clientY, pointerId: event.pointerId };
  startPointerCapture(event);
});

canvas?.addEventListener('pointermove', event => {
  if (drag?.type === 'crop' && drag.pointerId === event.pointerId) {
    const delta = clientDeltaToArtboard(event.clientX - drag.startClientX, event.clientY - drag.startClientY,
      drag.rect, drag.canvasSize, drag.artboard);
    if (Math.abs(delta.x) + Math.abs(delta.y) > 0.5) previewCrop(drag.stripId, drag.slotIndex, cropForDrag(drag, delta.x, delta.y));
    return;
  }
  if (!drag || drag.pointerId !== event.pointerId || drag.type !== 'resize') return;
  const delta = clientDeltaToArtboard(event.clientX - drag.startClientX, event.clientY - drag.startClientY, drag.rect,
    drag.canvasSize, drag.artboard);
  let next = resizeElement(drag.element, drag.direction, delta.x, delta.y, {
    lockAspect: drag.lockAspect, aspectRatio: drag.aspectRatio,
  });
  let guides = { x: null, y: null };
  if (drag.design.snap && !drag.lockAspect) {
    const snapped = snapResize(drag.element, next, drag.direction, drag.design);
    next = snapped.element; guides = snapped.guides;
  }
  drag.preview = next;
  replacePreviewElement(next);
  dispatch('studio:snap-guides', guides);
});

function finishPointer(event, cancelled = false) {
  if (!drag || drag.pointerId !== event.pointerId) return;
  const current = drag; drag = null;
  releasePointerCapture(event);
  if (current.type === 'resize') {
    dispatch('studio:snap-guides', { x: null, y: null });
    if (cancelled) { replacePreviewElement(current.element); transientFrameLayout = false; renderDesign(); return; }
    const next = current.preview;
    if (['x', 'y', 'width', 'height'].some(key => Math.abs(Number(next[key]) - Number(current.element[key])) > 0.01)) {
      void applyDesignChanges({ elements: [{ id: current.element.id, x: next.x, y: next.y, width: next.width, height: next.height }] }, 'Resize element').catch(error => {
        replacePreviewElement(current.element); transientFrameLayout = false; renderDesign(); console.error(error);
      });
    }
    return;
  }
  if (cancelled) {
    if (current.type === 'crop') previewCrop(current.stripId, current.slotIndex, current.crop);
    return;
  }
  const delta = clientDeltaToArtboard(event.clientX - current.startClientX, event.clientY - current.startClientY,
    current.rect || canvas.getBoundingClientRect(), current.canvasSize || { width: canvas.width, height: canvas.height }, current.artboard || (current.design?.artboard || { width: 1200, height: 1600 }));
  if (current.type === 'crop') {
    if (Math.abs(delta.x) + Math.abs(delta.y) <= 0.5) return;
    const crop = cropForDrag(current, delta.x, delta.y);
    previewCrop(current.stripId, current.slotIndex, crop);
    void applyCropChange({ stripId: current.stripId, slotIndex: current.slotIndex, elementId: current.element.id, crop }).catch(error => {
      previewCrop(current.stripId, current.slotIndex, current.crop); console.error(error);
    });
    return;
  }
  if (Math.abs(delta.x) + Math.abs(delta.y) <= 0.5) return;
  const element = current.element, design = current.design;
  let x = element.x + delta.x, y = element.y + delta.y, guides = { x: null, y: null };
  if (design.snap) {
    const board = design.artboard || { width: 1200, height: 1600 };
    const others = (design.elements || []).filter(item => item.id !== element.id && item.visible !== false);
    const xCandidates = [{ value: 0, guide: 0 }, { value: board.width - element.width, guide: board.width }, { value: (board.width - element.width) / 2, guide: board.width / 2 }];
    const yCandidates = [{ value: 0, guide: 0 }, { value: board.height - element.height, guide: board.height }, { value: (board.height - element.height) / 2, guide: board.height / 2 }];
    for (const other of others) {
      xCandidates.push({ value: other.x, guide: other.x }, { value: other.x + other.width - element.width, guide: other.x + other.width }, { value: other.x + other.width, guide: other.x + other.width }, { value: other.x - element.width, guide: other.x });
      yCandidates.push({ value: other.y, guide: other.y }, { value: other.y + other.height - element.height, guide: other.y + other.height }, { value: other.y + other.height, guide: other.y + other.height }, { value: other.y - element.height, guide: other.y });
    }
    const nearest = (position, values) => values.reduce((best, candidate) => {
      const distance = Math.abs(candidate.value - position);
      return distance < best.distance ? { ...candidate, distance } : best;
    }, { value: position, guide: null, distance: Infinity });
    const sx = nearest(x, xCandidates), sy = nearest(y, yCandidates), threshold = 12;
    if (sx.guide !== null && Math.abs(sx.value - x) <= threshold) { x = sx.value; guides.x = sx.guide; }
    if (sy.guide !== null && Math.abs(sy.value - y) <= threshold) { y = sy.value; guides.y = sy.guide; }
    const row = others.filter(item => Math.abs(item.y - y) <= threshold && Math.abs(item.height - element.height) <= threshold).sort((a, b) => a.x - b.x);
    for (let index = 0; index + 1 < row.length; index++) {
      const gap = row[index + 1].x - (row[index].x + row[index].width);
      const candidate = row[index + 1].x + row[index + 1].width + gap;
      if (Math.abs(candidate - x) <= threshold && Math.abs(element.width - row[index].width) <= threshold) { x = candidate; guides.x = candidate; break; }
    }
  }
  dispatch('studio:snap-guides', guides);
  void applyDesignChanges({ elements: [{ id: element.id, x, y }] }, 'Move element').catch(console.error);
}

canvas?.addEventListener('pointerup', event => finishPointer(event));
canvas?.addEventListener('pointercancel', event => finishPointer(event, true));
canvas?.addEventListener('wheel', event => {
  const element = selectedElement();
  if (element?.type !== 'image_frame' || element.visible === false) return;
  const elementBox = effectiveFrameElement(element);
  const rect = canvas.getBoundingClientRect();
  const point = clientToArtboard(event.clientX, event.clientY, rect,
    { width: canvas.width, height: canvas.height }, currentDesign().artboard || { width: 1200, height: 1600 });
  if (point.x < elementBox.x || point.x > elementBox.x + elementBox.width
    || point.y < elementBox.y || point.y > elementBox.y + elementBox.height) return;
  const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
  const slotIndex = selectedSlotIndex();
  const slot = strip?.slots?.[slotIndex];
  if (!slot?.imageId) return;
  event.preventDefault();
  const crop = { ...(slot.crop || {}) };
  const oldZoom = Number(crop.zoom) || 1;
  const zoom = Math.min(4, Math.max(1, oldZoom * Math.exp(-event.deltaY * 0.0015)));
  if (Math.abs(zoom - oldZoom) < 0.001) return;
  queueCropChange({ stripId: strip.id, slotIndex, elementId: element.id,
    crop: { ...crop, zoom: Math.round(zoom * 100) / 100 } }, 220);
}, { passive: false });
canvas?.addEventListener('dblclick', event => {
  const rect = canvas.getBoundingClientRect(), design = currentDesign();
  const point = clientToArtboard(event.clientX, event.clientY, rect, { width: canvas.width, height: canvas.height }, design.artboard || { width: 1200, height: 1600 });
  const hit = [...(design.elements || [])].reverse().find(item => item.visible !== false && point.x >= item.x && point.x <= item.x + item.width && point.y >= item.y && point.y <= item.y + item.height);
  if (hit?.type !== 'text' || hit.locked) return;
  event.preventDefault();
  if (getState().selectedElementId !== hit.id) {
    setState({ selectedElementId: hit.id });
    dispatch('studio:element-selected', { id: hit.id });
  }
  void editTextElement(hit).catch(error => { status(error.message || 'Text editing could not be opened.'); console.error(error); });
});

document.addEventListener('studio:files-dropped', event => {
  const paths = event.detail?.paths;
  if (!Array.isArray(paths) || !paths.length) return;
  void call('images.import_paths', { paths }, { wait: true, label: 'Importing photos' }).then(async result => {
    if (result?.errors?.length) setText('image-errors', result.errors.map(item => item.error || item.message).join(' · '));
    else setText('image-errors', '');
    await refreshState();
  }).catch(console.error);
});

async function start() {
  await new Promise(resolve => {
    if (window.pywebview?.api) return resolve();
    window.addEventListener('pywebviewready', resolve, { once: true });
    setTimeout(resolve, 10000);
  });
  try {
    const recovery = await invoke('session.get_recovery', {});
    if (recovery.available) {
      const choice = await requestStudioDialog({
        kind: 'recovery', title: 'Restore previous session?',
        message: 'We found an unfinished project from your previous session.',
        detail: `${recovery.projectName || 'Untitled Project'} · ${recovery.imageCount || 0} photos`,
        confirmLabel: 'Restore Session', secondaryLabel: 'Start New', decisionRequired: true,
      });
      if (choice?.action === 'primary' && choice.confirmed) await invoke('session.restore', {});
      else if (choice?.action === 'secondary' && !choice.confirmed) await invoke('session.start_new', {});
      else throw new Error('Choose Restore Session or Start New to continue.');
    }
    await refreshState(); await loadTemplates(); await loadPresets();
    const mode = project()?.theme?.mode || 'system';
    const effective = mode === 'system' ? (window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ? 'dark' : 'light') : mode;
    document.documentElement.dataset.theme = effective;
    document.body.dataset.themeMode = mode;
    dispatch('studio:theme-change', { mode, effective });
    await showValidation().catch(() => {});
    await showPreview(0).catch(() => {});
    dispatch('studio:ready', { state: getState() });
  } catch (error) { status(error.message || 'Could not initialize the application'); }
}

document.addEventListener('studio:state-change', renderAll);
document.addEventListener('studio:text-edit', event => {
  const { id, changes, label } = event.detail || {};
  if (!id || !changes || typeof changes !== 'object') return;
  const element = currentDesign().elements?.find(item => item.id === id);
  if (!element || element.type !== 'text' || element.locked) return;
  void applyDesignChanges({ elements: [{ id, ...changes }] }, label || 'Edit text').catch(console.error);
});
window.addEventListener('error', event => status(event.message || 'Unexpected frontend error'));
const systemTheme = window.matchMedia?.('(prefers-color-scheme: dark)');
systemTheme?.addEventListener?.('change', event => {
  if (project()?.theme?.mode === 'system') {
    const effective = event.matches ? 'dark' : 'light';
    document.documentElement.dataset.theme = effective;
    dispatch('studio:theme-change', { mode: 'system', effective });
  }
});
void start();
