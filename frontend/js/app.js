import { invoke, invokeAndWait } from './api.js';
import { getState, setState, project } from './state.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const bind = name => $(`[data-bind="${name}"]`);
const setText = (name, value) => { const node = bind(name); if (node) node.textContent = value ?? ''; };
const setValue = (name, value) => { const node = bind(name); if (node && value !== undefined && value !== null) node.value = value; };
const setChecked = (name, value) => { const node = bind(name); if (node) node.checked = Boolean(value); };
const thumbnailCache = new Map();
const thumbnailLoading = new Set();
let aspectLockEnabled = true;
const thumbnailObserver = typeof IntersectionObserver !== 'undefined' ? new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    thumbnailObserver.unobserve(entry.target);
    void fetchThumbnail(entry.target, entry.target.dataset.imageId);
  }
}) : null;

async function fetchThumbnail(node, imageId) {
  if (!imageId || thumbnailLoading.has(imageId)) return;
  const cached = thumbnailCache.get(imageId);
  if (cached) { node.src = cached.src; return; }
  thumbnailLoading.add(imageId);
  try {
    const value = await invoke('images.get_thumbnail', { imageId });
    if (value.dataUrl) { node.src = value.dataUrl; thumbnailCache.set(imageId, node); }
  } finally { thumbnailLoading.delete(imageId); }
}

function status(message) { setText('app-status', message); }
function dispatch(name, detail) { document.dispatchEvent(new CustomEvent(name, { detail })); }
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c])); }

async function call(method, params = {}, { wait = false, label = method } = {}) {
  status(`${label}…`);
  try {
    const data = wait ? await invokeAndWait(method, params, job => dispatch('studio:job-progress', job)) : await invoke(method, params);
    status('Ready');
    return data;
  } catch (error) {
    status(error.message || 'Request failed');
    throw error;
  }
}

async function refreshState() {
  const data = await invoke('app.get_state', {});
  const p = data.project || data;
  setState({ project: p });
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
}

function renderImages() {
  const p = project(); const list = bind('image-list');
  if (!list || !p) return;
  list.querySelectorAll('img[data-image-id]').forEach(node => thumbnailObserver?.unobserve(node));
  list.replaceChildren();
  for (const image of p.images || []) {
    const li = document.createElement('li');
    li.dataset.imageId = image.id;
    const thumbnail = document.createElement('img'); thumbnail.width = 56; thumbnail.height = 56;
    thumbnail.alt = ''; thumbnail.loading = 'lazy'; thumbnail.dataset.imageId = image.id;
    if (thumbnailObserver) thumbnailObserver.observe(thumbnail); else void fetchThumbnail(thumbnail, image.id);
    const name = document.createElement('span'); name.textContent = `${image.name} (${image.width || '?'}×${image.height || '?'})`;
    const up = document.createElement('button'); up.type = 'button'; up.dataset.action = 'image-up'; up.dataset.imageId = image.id; up.textContent = 'Up';
    const down = document.createElement('button'); down.type = 'button'; down.dataset.action = 'image-down'; down.dataset.imageId = image.id; down.textContent = 'Down';
    const remove = document.createElement('button'); remove.type = 'button'; remove.dataset.action = 'image-remove'; remove.dataset.imageId = image.id; remove.textContent = 'Remove';
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
    setState({ selectedStripId: strips[0]?.id || null });
  }
}

function currentDesign() { return project()?.design || {}; }
function selectedElement() { return currentDesign().elements?.find(item => item.id === getState().selectedElementId) || currentDesign().elements?.[0] || null; }

function renderDesign() {
  const design = currentDesign(); const canvas = bind('designer-canvas');
  const elements = design.elements || [];
  // Presentation-only layer mirror; functional editing continues to use the
  // parked select and the structured design in application state.
  window.__studio_design_snapshot = design;
  const elementList = bind('design-element-list');
  if (elementList) {
    const current = getState().selectedElementId || elements[0]?.id;
    elementList.innerHTML = elements.map(item => `<option value="${esc(item.id)}">${esc(item.id)} · ${esc(item.type)}</option>`).join('');
    if (current) elementList.value = current;
    if (current !== getState().selectedElementId) setState({ selectedElementId: current });
  }
  const element = selectedElement();
  if (element) {
    for (const key of ['x','y','width','height']) setValue(`element-${key}`, element[key]);
    const fill = bind('element-fill'); if (fill && /^#[0-9a-f]{6}$/i.test(element.fill || '')) fill.value = element.fill;
    const border = bind('element-border-color'); if (border && /^#[0-9a-f]{6}$/i.test(element.borderColor || '')) border.value = element.borderColor;
    setValue('element-border-width', element.borderWidth ?? 0); setValue('element-opacity', element.opacity ?? 1);
    setValue('element-border-radius', element.borderRadius ?? 0); setValue('element-padding', element.padding ?? 0);
    const strip = project()?.strips?.find(value => value.id === getState().selectedStripId);
    const crop = element.type === 'image_frame' ? strip?.slots?.[selectedSlotIndex()]?.crop : null;
    setValue('image-ratio', element.aspectRatio || crop?.ratio || 'original');
    setValue('crop-mode', element.cropMode || crop?.mode || 'cover');
    setValue('crop-zoom', crop?.zoom ?? 1);
  }
  const activeElement = selectedElement();
  const snapToggle = $('[data-action="design-snap"]');
  if (snapToggle) snapToggle.checked = design.snap !== false;
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
  for (const item of elements) {
    if (item.visible === false) continue;
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
        if (cached) drawEditorPhoto(ctx, cached, item, strip.slots[slot]?.crop || {});
        else if (!thumbnailLoading.has(imageId)) {
          thumbnailLoading.add(imageId);
          void invoke('images.get_thumbnail', { imageId }).then(value => {
            if (!value.dataUrl) return;
            const photo = new Image(); photo.onload = () => { thumbnailCache.set(imageId, photo); renderDesign(); };
            photo.src = value.dataUrl;
          }).finally(() => thumbnailLoading.delete(imageId));
        }
      }
    }
    if (item.borderWidth > 0) { ctx.strokeStyle = item.borderColor || '#000'; ctx.lineWidth = item.borderWidth; ctx.stroke(); }
  }
  ctx.globalAlpha = 1; ctx.restore();
}

function drawEditorPhoto(ctx, photo, element, crop) {
  let x = element.x, y = element.y, width = element.width, height = element.height;
  const ratio = element.aspectRatio || crop.ratio || 'original';
  const desiredRatio = ratio === '1:1' ? 1 : ratio === '4:3' ? 4 / 3 : ratio === '16:9' ? 16 / 9 : photo.naturalWidth / photo.naturalHeight;
  if (width / height > desiredRatio) { const next = height * desiredRatio; x += (width - next) / 2; width = next; }
  else { const next = width / desiredRatio; y += (height - next) / 2; height = next; }
  ctx.save(); ctx.beginPath();
  if (element.borderRadius > 0 && ctx.roundRect) ctx.roundRect(x, y, width, height, element.borderRadius);
  else ctx.rect(x, y, width, height);
  ctx.clip();
  const zoom = Math.min(4, Math.max(1, Number(crop.zoom) || 1));
  const mode = element.cropMode || crop.mode || 'cover';
  if (mode === 'contain') {
    const scale = Math.min(width / photo.naturalWidth, height / photo.naturalHeight) * zoom;
    const drawWidth = photo.naturalWidth * scale, drawHeight = photo.naturalHeight * scale;
    ctx.drawImage(photo, x + (width - drawWidth) / 2, y + (height - drawHeight) / 2, drawWidth, drawHeight);
  } else {
    const targetRatio = width / height, sourceRatio = photo.naturalWidth / photo.naturalHeight;
    let sourceWidth = photo.naturalWidth, sourceHeight = photo.naturalHeight;
    if (sourceRatio > targetRatio) sourceWidth = sourceHeight * targetRatio;
    else sourceHeight = sourceWidth / targetRatio;
    sourceWidth /= zoom; sourceHeight /= zoom;
    const centerX = photo.naturalWidth / 2 + (Number(crop.offsetX) || 0) * Math.max(0, photo.naturalWidth - sourceWidth) / 2;
    const centerY = photo.naturalHeight / 2 + (Number(crop.offsetY) || 0) * Math.max(0, photo.naturalHeight - sourceHeight) / 2;
    const sourceX = Math.min(Math.max(0, centerX - sourceWidth / 2), photo.naturalWidth - sourceWidth);
    const sourceY = Math.min(Math.max(0, centerY - sourceHeight / 2), photo.naturalHeight - sourceHeight);
    ctx.drawImage(photo, sourceX, sourceY, sourceWidth, sourceHeight, x, y, width, height);
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
  const settings = project()?.printSettings || {};
  setValue('strips-per-page', settings.stripsPerPage ?? 5); setValue('strip-width-mm', settings.stripWidthMm ?? 54);
  setValue('strip-height-mm', settings.stripHeightMm ?? 72); setValue('page-margin-mm', settings.marginMm ?? 5);
  setValue('strip-gap-mm', settings.gapMm ?? 2); setValue('resolution-dpi', settings.resolutionDpi ?? 300); setChecked('cut-guides', settings.cutGuides ?? false);
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
  setText('fit-message', fit ? (fit.valid ? `${fit.requestedCount} strips fit. Maximum: ${fit.maximumCount}.` : `${fit.requestedCount} strips cannot fit while preserving configured strip size. Maximum: ${fit.maximumCount}; available ${fit.availableWidthMm} mm; required ${fit.requiredWidthMm} mm; overflow ${fit.overflowMm} mm.`) : '');
  dispatch('studio:validation-updated', validation);
}

async function showPreview(page = getState().previewPage || 0) {
  const scale = bind('preview-scale')?.value || 'fit';
  const data = await call('print.preview', { page, scale }, { wait: true, label: 'Rendering preview' });
  const preview = data.preview || data;
  setState({ preview, previewPage: preview.page ?? page }, 'studio:preview-updated');
  const image = bind('pdf-preview');
  if (image) {
    image.src = preview.dataUrl || preview.uri || '';
    image.hidden = !image.src;
  }
  setText('preview-page', (preview.page ?? page) + 1); setText('page-count', preview.pageCount || 1);
  dispatch('studio:preview-scale-change', { scale, page: preview.page ?? page });
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
    const result = await call('design.update', { changes, label });
    if (result.design) setState({ project: { ...project(), design: result.design } });
    else await refreshState();
    if (revision === designUpdateRevision) renderDesign();
  });
}

let cropUpdateRevision = 0;
function applyCropChange(params) {
  const revision = ++cropUpdateRevision;
  return enqueueProjectMutation(async () => {
    const result = await call('strips.set_crop', params);
    if (result.project) setState({ project: result.project });
    else await refreshState();
    if (revision === cropUpdateRevision) renderDesign();
  });
}

function currentPrintForm() {
  return {
    stripsPerPage: Number(bind('strips-per-page')?.value || 5),
    stripWidthMm: Number(bind('strip-width-mm')?.value || 54),
    stripHeightMm: Number(bind('strip-height-mm')?.value || 72),
    marginMm: Number(bind('page-margin-mm')?.value || 5),
    gapMm: Number(bind('strip-gap-mm')?.value || 2),
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
    case 'project-new': if (confirm('Start a new project?')) { await call('project.new'); await refreshState(); } break;
    case 'project-open': await call('project.open', {}, { label: 'Opening project' }); await refreshState(); break;
    case 'project-save': await call('project.save', {}, { label: 'Saving project' }); break;
    case 'project-save-as': await call('project.save_as', {}, { label: 'Saving project' }); await refreshState(); break;
    case 'image-up': await reorderFromButtons(button.dataset.imageId, -1); break;
    case 'image-down': await reorderFromButtons(button.dataset.imageId, 1); break;
    case 'image-remove': await call('images.remove', { imageIds: [button.dataset.imageId] }); await refreshState(); break;
    case 'generate-strips': {
      const result = await call('strips.generate', { slotsPerStrip: Number(bind('slots-per-strip')?.value || 4), incomplete: bind('incomplete-handling')?.value || 'empty' });
      if (result.project) setState({ project: result.project }); else await refreshState();
      break;
    }
    case 'select-strip': setState({ selectedStripId: button.dataset.stripId }); renderDesign(); break;
    case 'template-load': await call('templates.load', { templateId: bind('template-list')?.value }); await refreshState(); break;
    case 'template-save-as': { const name = prompt('Name this template'); if (name) { await call('templates.save_as', { name }); await loadTemplates(); } break; }
    case 'template-duplicate': { const name = prompt('Name the duplicate template'); if (name) { await call('templates.duplicate', { templateId: bind('template-list')?.value, name }); await loadTemplates(); } break; }
    case 'template-rename': {
      const selected = getState().templates.find(item => (item.templateId || item.id) === bind('template-list')?.value);
      if (!selected) break;
      const name = prompt('New template name', selected.name);
      if (name) { await call('templates.rename', { templateId: selected.templateId || selected.id, name }); await loadTemplates(); }
      break;
    }
    case 'template-delete': {
      const templateId = bind('template-list')?.value;
      if (templateId && confirm('Delete this custom template? Built-in templates cannot be deleted.')) { await call('templates.delete', { templateId }); await refreshState(); await loadTemplates(); }
      break;
    }
    case 'template-reset': if (confirm('Reset design to the selected template?')) { await call('templates.reset', {}); await refreshState(); } break;
    case 'design-undo': await call('design.undo', {}); await refreshState(); break;
    case 'design-redo': await call('design.redo', {}); await refreshState(); break;
    case 'design-snap': await call('design.set_snap', { enabled: button.checked }); break;
    case 'element-toggle': { const element = selectedElement(); if (element) await applyDesignChanges({ elements: [{ id: element.id, visible: element.visible === false }] }, 'Toggle visibility'); break; }
    case 'element-lock': { const element = selectedElement(); if (element) await applyDesignChanges({ elements: [{ id: element.id, locked: !element.locked }] }, 'Toggle lock'); break; }
    case 'element-duplicate': { const element = selectedElement(); if (element) await applyDesignChanges({ duplicateElementId: element.id }, 'Duplicate element'); break; }
    case 'element-add-decoration': case 'element-add-pattern': {
      const type = action === 'element-add-pattern' ? 'pattern' : 'decoration';
      const result = await call('design.add_element', { type, label: type === 'pattern' ? 'Add pattern' : 'Add decoration' });
      if (result.project) setState({ project: result.project, selectedElementId: result.element?.id });
      renderDesign();
      break;
    }
    case 'element-apply-scale': { const element = selectedElement(), scale = Number(bind('element-scale')?.value || 1); if (element && scale > 0) await applyDesignChanges({ elements: [{ id: element.id, width: element.width * scale, height: element.height * scale }] }, 'Scale element'); break; }
    case 'element-align': { const element = selectedElement(); if (element) { await call('design.align', { elementIds: [element.id], alignment: button.dataset.alignment, snap: Boolean($('[data-action="design-snap"]')?.checked) }); await refreshState(); } break; }
    case 'crop-reset': if (selectedElement()?.type === 'image_frame') await applyCropChange({ stripId: getState().selectedStripId, slotIndex: selectedSlotIndex(), crop: { zoom: 1, offsetX: 0, offsetY: 0 } }); break;
    case 'print-update': await persistPrintSettings(); await refreshState(); await showValidation(); await showPreview(0); break;
    case 'print-validate': await persistPrintSettings(); await showValidation(); break;
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
      if (preset) { await call('print.update_settings', { settings: preset.settings }); renderPrintSettings(); await showValidation(); await showPreview(0); }
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
  if (node.matches('[data-action="design-snap"]')) void call('design.set_snap', { enabled: node.checked }).then(refreshState).catch(console.error);
  if (node.matches('[data-action="sort-images"]')) void call('images.sort', { mode: node.value }).then(refreshState).catch(console.error);
  if (node.matches('[data-action="theme-set"]')) void call('theme.set', { mode: node.value }).then(data => {
    const effective = data.mode === 'system' ? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : data.effective;
    document.documentElement.dataset.theme = effective || node.value;
    if (project()) setState({ project: { ...project(), theme: data, themeMode: data.mode } });
    dispatch('studio:theme-change', { ...data, effective });
  }).catch(console.error);
  if (node.matches('[data-bind="design-element-list"]')) { setState({ selectedElementId: node.value }); renderDesign(); }
  if (node.matches('[data-bind="template-list"]')) setState({ selectedTemplateId: node.value });
  if (node.matches('[data-bind="preview-scale"]')) void showPreview(getState().previewPage || 0).catch(console.error);
  if (node.matches('[data-action="design-aspect-lock"]')) aspectLockEnabled = node.checked;
  if (node.matches('[data-bind="slots-per-strip"], [data-bind="incomplete-handling"]')) {
    const changes = { slotsPerStrip: Number(bind('slots-per-strip')?.value || 4), incompleteHandling: bind('incomplete-handling')?.value || 'empty' };
    void call('strips.generate', { slotsPerStrip: changes.slotsPerStrip, incomplete: changes.incompleteHandling }).then(refreshState).catch(console.error);
  }
  if (node.matches('[data-bind="element-x"], [data-bind="element-y"], [data-bind="element-width"], [data-bind="element-height"], [data-bind="element-fill"], [data-bind="element-border-color"], [data-bind="element-border-width"], [data-bind="element-border-radius"], [data-bind="element-padding"], [data-bind="element-opacity"], [data-bind="image-ratio"], [data-bind="crop-mode"]')) {
    const element = selectedElement(); if (!element) return;
    if (node.matches('[data-bind="image-ratio"], [data-bind="crop-mode"]') && element.type !== 'image_frame') return;
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
      'image-ratio': 'aspectRatio', 'crop-mode': 'cropMode',
    };
    const field = fieldMap[node.dataset.bind];
    const numericFields = new Set(['x', 'y', 'width', 'height', 'borderWidth', 'borderRadius', 'padding', 'opacity']);
    const value = numericFields.has(field) ? numberValue(node.dataset.bind) : node.value;
    if (value === undefined) return;
    const elementChanges = { id: element.id, [field]: value };
    if (aspectLockEnabled && field === 'width' && element.width) {
      elementChanges.height = Number((element.height * value / element.width).toFixed(2));
      setValue('element-height', elementChanges.height);
    }
    if (aspectLockEnabled && field === 'height' && element.height) {
      elementChanges.width = Number((element.width * value / element.height).toFixed(2));
      setValue('element-width', elementChanges.width);
    }
    const changes = { elements: [elementChanges] };
    void applyDesignChanges(changes).catch(console.error);
  }
  if (node.matches('[data-bind="pattern-type"], [data-bind="pattern-scale"], [data-bind="pattern-spacing"], [data-bind="pattern-opacity"], [data-bind="pattern-color"], [data-bind="pattern-offset-x"], [data-bind="pattern-offset-y"]')) {
    const selected = selectedElement();
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
  if (node.matches('[data-bind="strips-per-page"], [data-bind="strip-width-mm"], [data-bind="strip-height-mm"], [data-bind="page-margin-mm"], [data-bind="strip-gap-mm"], [data-bind="cut-guides"]')) {
    void persistPrintSettings({ validate: true }).catch(console.error);
  }
});

document.addEventListener('input', event => {
  const node = event.target;
  if (!node.matches('[data-bind="crop-zoom"]')) return;
  if (selectedElement()?.type !== 'image_frame') return;
  const strip = project()?.strips?.find(item => item.id === getState().selectedStripId);
  const slot = strip?.slots?.[selectedSlotIndex()];
  void applyCropChange({ stripId: getState().selectedStripId, slotIndex: selectedSlotIndex(), crop: { ...(slot?.crop || {}), zoom: Number(node.value) } }).catch(console.error);
});

const canvas = bind('designer-canvas');
let drag = null;
canvas?.addEventListener('pointerdown', event => {
  const rect = canvas.getBoundingClientRect(), design = currentDesign(), board = design.artboard || { width: 1200, height: 1600 };
  const scale = Math.min(canvas.width / board.width, canvas.height / board.height);
  const left = (canvas.width - board.width * scale) / 2, top = (canvas.height - board.height * scale) / 2;
  const x = (event.clientX - rect.left) * canvas.width / rect.width, y = (event.clientY - rect.top) * canvas.height / rect.height;
  const logicalX = (x - left) / scale, logicalY = (y - top) / scale;
  const activeStrip = project()?.strips?.find(value => value.id === getState().selectedStripId);
  const visible = [...(design.elements || [])].filter(item => item.visible !== false).reverse();
  const hit = visible.find(item => logicalX >= item.x && logicalX <= item.x + item.width && logicalY >= item.y && logicalY <= item.y + item.height);
  if (hit) setState({ selectedElementId: hit.id });
  if (hit?.type === 'image_frame' && activeStrip && !event.altKey) {
    const slotIndex = Number(hit.slotIndex ?? (Number((hit.id || '').match(/(\d+)$/)?.[1] || 1) - 1));
    const slot = activeStrip.slots?.[slotIndex];
    if (slot?.imageId) {
      setState({ selectedElementId: hit.id });
      const crop = slot.crop || { zoom: 1, offsetX: 0, offsetY: 0 };
      drag = { crop: true, slotIndex, stripId: activeStrip.id, startX: x, startY: y, offsetX: crop.offsetX || 0, offsetY: crop.offsetY || 0, zoom: crop.zoom || 1, frameWidth: hit.width * scale, frameHeight: hit.height * scale };
      canvas.setPointerCapture(event.pointerId); return;
    }
  }
  const element = hit || selectedElement(); if (!element || element.locked) return;
  drag = { element, startX: x, startY: y, x: element.x, y: element.y, scale }; canvas.setPointerCapture(event.pointerId);
});
canvas?.addEventListener('pointerup', event => {
  if (!drag) return;
  const rect = canvas.getBoundingClientRect(), x = (event.clientX - rect.left) * canvas.width / rect.width, y = (event.clientY - rect.top) * canvas.height / rect.height;
  if (drag.crop) {
    const dx = x - drag.startX, dy = y - drag.startY, current = drag; drag = null;
    if (Math.abs(dx) + Math.abs(dy) > 1) {
      const crop = { zoom: current.zoom, offsetX: current.offsetX - dx / Math.max(1, current.frameWidth), offsetY: current.offsetY - dy / Math.max(1, current.frameHeight) };
      void applyCropChange({ stripId: current.stripId, slotIndex: current.slotIndex, crop }).catch(console.error);
    }
    return;
  }
  const dx = (x - drag.startX) / drag.scale, dy = (y - drag.startY) / drag.scale;
  const element = drag.element, design = currentDesign(); drag = null;
  if (Math.abs(dx) + Math.abs(dy) > 1) {
    let x = element.x + dx, y = element.y + dy, guides = { x: null, y: null };
    if (design.snap) {
      const board = design.artboard || { width: 1200, height: 1600 };
      const others = (design.elements || []).filter(item => item.id !== element.id && item.visible !== false);
      const xCandidates = [{ value: 0, guide: 0 }, { value: board.width - element.width, guide: board.width }, { value: (board.width - element.width) / 2, guide: board.width / 2 }];
      const yCandidates = [{ value: 0, guide: 0 }, { value: board.height - element.height, guide: board.height }, { value: (board.height - element.height) / 2, guide: board.height / 2 }];
      for (const other of others) {
        xCandidates.push({ value: other.x, guide: other.x }, { value: other.x + other.width - element.width, guide: other.x + other.width }, { value: other.x + other.width, guide: other.x + other.width }, { value: other.x - element.width, guide: other.x });
        yCandidates.push({ value: other.y, guide: other.y }, { value: other.y + other.height - element.height, guide: other.y + other.height }, { value: other.y + other.height, guide: other.y + other.height }, { value: other.y - element.height, guide: other.y });
      }
      const nearest = (position, values) => values.reduce((best, current) => Math.abs(current.value - position) < Math.abs(best.value - position) ? current : best, { value: position, guide: null });
      const sx = nearest(x, xCandidates), sy = nearest(y, yCandidates), threshold = 12;
      if (sx.guide !== null && Math.abs(sx.value - x) <= threshold) { x = sx.value; guides.x = sx.guide; }
      if (sy.guide !== null && Math.abs(sy.value - y) <= threshold) { y = sy.value; guides.y = sy.guide; }
      // Match an existing horizontal/vertical gap for equal sized neighbors.
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
    if (recovery.available && confirm(`Previous session found (${recovery.projectName || 'Untitled Project'}). Restore it?`)) await invoke('session.restore', {});
    else if (recovery.available) await invoke('session.start_new', {});
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
