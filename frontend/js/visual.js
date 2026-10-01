/* =============================================================================
   Strip Print Studio — Presentation layer (visual.js)
   -----------------------------------------------------------------------------
   Presentation-only enhancements. Owns NO application state and never calls
   the pywebview bridge directly. It observes the functional events dispatched
   by app.js / state.js and mirrors them into visual-only hooks.

   Functional hooks remain untouched: app.js continues to bind [data-action],
   [data-bind], and dispatch studio:* events. When a presentation control needs
   a functional value change, it forwards it to the matching parked functional
   control (see the .parked block in index.html) so app.js keeps owning logic.

   Designer editor overlay: a dedicated interface-only layer (hover bounds,
   selection bounds, resize handles, snap guides) above the canvas. It is
   presentation-only — nothing here is exported, printed, or persisted, and it
   never mutates design state; edits always flow through app.js actions.
   ============================================================================= */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* ------------------------------------------------------------- View router */
const views = {
  images: $('[data-view="images"]'),
  designer: $('[data-view="designer"]'),
  print: $('[data-view="print"]'),
  preview: $('[data-view="preview"]'),
  templates: $('[data-view="templates"]'),
  settings: $('[data-view="settings"]'),
};

const titles = {
  images: 'Photos',
  designer: 'Strip Designer',
  print: 'Print Layout',
  preview: 'PDF Preview',
  templates: 'Templates',
  settings: 'Settings',
};

function activateView(name) {
  if (!views[name] || document.body.dataset.activeView === name) return;
  document.body.dataset.activeView = name;
  for (const [key, node] of Object.entries(views)) {
    node?.classList.toggle('is-active', key === name);
  }
  $$('.rail-item[data-nav]').forEach(item => item.classList.toggle('is-active', item.dataset.nav === name));
  const title = $('[data-topbar-title]');
  if (title) title.textContent = titles[name] ?? name;
  if (name === 'designer') requestAnimationFrame(updateOverlay);
  else resetOverlay();
}

for (const item of $$('.rail-item[data-nav]')) {
  item.addEventListener('click', () => activateView(item.dataset.nav));
}

/* Internal view shortcuts (Next-step cards etc.) */
$$('[data-vaction^="nav:"]').forEach(node => {
  node.addEventListener('click', () => activateView(node.dataset.vaction.split(':')[1]));
});

/* --------------------------------------------------------- Parked forwards */
function forwardSelect(select, value) {
  if (!select || value === undefined || value === null) return;
  if (select.value === value) return;
  select.value = value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

/* Sort segmented control → parked [data-action="sort-images"] */
$$('[data-segmented="sort"] button').forEach(button => {
  button.addEventListener('click', () => {
    const mode = button.dataset.vaction.split(':')[1];
    $$('[data-segmented="sort"] button').forEach(b => b.classList.toggle('is-active', b === button));
    forwardSelect($('[data-action="sort-images"]'), mode);
  });
});

/* Theme segmented + rail cycle button → parked [data-action="theme-set"] */
const themeSelect = $('[data-action="theme-set"]');

function setThemeSeg(mode) {
  $$('[data-segmented="theme"] button').forEach(b => b.classList.toggle('is-active', b.dataset.vaction === `theme:${mode}`));
  const icon = $('[data-theme-icon]');
  if (icon) icon.setAttribute('href', mode === 'light' ? '#i-sun' : '#i-moon');
}

function applyEffectiveTheme(mode) {
  const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
  const effective = mode === 'system' ? (mq?.matches ? 'dark' : 'light') : mode;
  document.documentElement.dataset.theme = effective;
  setThemeSeg(mode);
  updateOverlay();
}

$$('[data-segmented="theme"] button').forEach(button => {
  button.addEventListener('click', () => {
    const mode = button.dataset.vaction.split(':')[1];
    forwardSelect(themeSelect, mode);
    applyEffectiveTheme(mode);
    document.body.dataset.themeMode = mode;
  });
});

const themeCycle = $('[data-vaction="theme-cycle"]');
themeCycle?.addEventListener('click', () => {
  const current = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  const next = current === 'dark' ? 'light' : 'dark';
  const mode = next;
  forwardSelect(themeSelect, mode);
  applyEffectiveTheme(mode);
  document.body.dataset.themeMode = mode;
});

document.documentElement.classList.add('theme-transition');
setTimeout(() => document.documentElement.classList.remove('theme-transition'), 400);

/* Follow the functional theme state so system mode stays in sync */
document.addEventListener('studio:theme-change', event => {
  document.body.dataset.themeMode = event.detail?.mode ?? 'system';
  applyEffectiveTheme(event.detail?.mode ?? 'system');
});

const systemDark = window.matchMedia?.('(prefers-color-scheme: dark)');
systemDark?.addEventListener?.('change', () => {
  if (document.body.dataset.themeMode === 'system') applyEffectiveTheme('system');
});

/* ------------------------------------------------------------ Rail collapse */
const appRoot = $('#app-root');

function setRailCompact(compact) {
  appRoot?.classList.toggle('app--rail-compact', compact);
  localStorage.setItem('studio.rail-compact', compact ? '1' : '0');
  const toggle = $('[data-vaction="rail-toggle"]');
  if (toggle) toggle.dataset.tooltip = compact ? 'Expand sidebar' : 'Collapse sidebar';
}

const railToggle = $('[data-vaction="rail-toggle"]');
railToggle?.addEventListener('click', () => setRailCompact(!appRoot?.classList.contains('app--rail-compact')));

const railSetting = $('[data-vsetting="rail-compact"]');
railSetting?.addEventListener('change', () => setRailCompact(railSetting.checked));
if (railSetting) railSetting.checked = localStorage.getItem('studio.rail-compact') === '1';

/* --------------------------------------------------------- Designer zoom
   The canvas keeps a CSS fit width; zoom widens the width cap so the stage
   scrolls instead of transform-scaling (which clipped content and blurred
   the render). Overlay geometry compensates for this scale in screen space. */
const canvas = $('[data-bind="designer-canvas"]');
let zoom = 1;

function applyZoom(value) {
  zoom = Math.min(4, Math.max(0.25, Math.round(value * 20) / 20));
  canvas?.style.setProperty('--zoom', String(zoom));
  const readout = $('[data-bind="zoom-level"]');
  if (readout) readout.textContent = `${Math.round(zoom * 100)}%`;
  requestAnimationFrame(updateOverlay);
}

function fitCanvasZoom() {
  /* Fit resets the presentation zoom; the canvas is CSS-fitted to the stage. */
  applyZoom(1);
  const stage = canvas?.closest('.stage');
  stage?.scrollTo({ left: 0, top: 0 });
}

$('[data-vaction="zoom-in"]')?.addEventListener('click', () => applyZoom(zoom + 0.1));
$('[data-vaction="zoom-out"]')?.addEventListener('click', () => applyZoom(zoom - 0.1));
$('[data-vaction="zoom-fit"]')?.addEventListener('click', fitCanvasZoom);
$('[data-vaction="zoom-100"]')?.addEventListener('click', () => applyZoom(1));

let resizeTimer = 0;
window.addEventListener('resize', () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(updateOverlay, 60);
});

/* ---------------------------------------------------------- Preview zoom */
const previewImage = $('[data-bind="pdf-preview"]');
let previewScale = 'fit';
let previewZoom = 1;

function applyPreviewMode(mode) {
  const requestedZoom = mode === '100' ? 1 : Number(mode);
  previewScale = ['fit', 'width', 'actual'].includes(mode) ? mode
    : String(Math.min(4, Math.max(0.25, requestedZoom || 1)));
  previewZoom = ['fit', 'width', 'actual'].includes(previewScale) ? 1 : Number(previewScale);
  if (!previewImage) return;
  previewImage.classList.toggle('is-fit', previewScale === 'fit');
  previewImage.classList.toggle('is-100', previewScale !== 'fit');
  /* Presentation-only scale: size the intrinsic raster by width so the stage
     scroll area matches the zoomed page (a transform would not be scrollable
     and would clip against the stage box). */
  if (previewScale === 'width') {
    const stage = previewImage.closest('.stage--preview');
    previewImage.style.width = `${Math.max(100, (stage?.clientWidth || 900) - 40)}px`;
  } else if (previewScale === 'actual') {
    const pageWidthMm = window.__studio_print_layout?.pageWidthMm || 297;
    previewImage.style.width = `${Math.round(pageWidthMm * 96 / 25.4)}px`;
  } else if (previewScale !== 'fit') {
    const base = previewImage.naturalWidth || 0;
    if (base) previewImage.style.width = `${Math.round(base * previewZoom)}px`;
  } else {
    previewImage.style.width = '';
  }
  const activeAction = previewScale === 'fit' ? 'pz-fit'
    : previewScale === 'width' ? 'pz-width'
    : previewScale === 'actual' ? 'pz-actual'
    : previewZoom === 1 ? 'pz-100' : '';
  $$('[data-vaction^="pz-"]').forEach(b => b.classList.toggle('is-active', b.dataset.vaction === activeAction));
}

$('[data-vaction="pz-fit"]')?.addEventListener('click', () => applyPreviewMode('fit'));
$('[data-vaction="pz-width"]')?.addEventListener('click', () => applyPreviewMode('width'));
$('[data-vaction="pz-actual"]')?.addEventListener('click', () => applyPreviewMode('actual'));
$('[data-vaction="pz-100"]')?.addEventListener('click', () => applyPreviewMode('100'));
$('[data-vaction="pz-in"]')?.addEventListener('click', () => applyPreviewMode((previewScale === 'fit' ? 1 : previewZoom) + 0.1));
$('[data-vaction="pz-out"]')?.addEventListener('click', () => applyPreviewMode((previewScale === 'fit' ? 1 : previewZoom) - 0.1));

/* Zoomed sizing needs the intrinsic raster size; re-apply once it loads. */
previewImage?.addEventListener('load', () => {
  if (previewScale !== 'fit') applyPreviewMode(previewScale);
});
window.addEventListener('resize', () => { if (previewScale === 'width') applyPreviewMode('width'); });

/* --------------------------------------------------------------- Layers
   Built from the functional [data-bind="design-element-list"] options, which
   app.js re-renders on every design change as "id · type". Visibility, lock
   state, and fill swatches come from window.__studio_design_snapshot. */
const layersRoot = $('[data-layers]');
const elementSelect = $('[data-bind="design-element-list"]');

const LAYER_ICONS = {
  background: '#i-layers',
  pattern: '#i-templates',
  image_frame: '#i-image',
  border: '#i-strip',
  decoration: '#i-snap',
  shape: '#i-designer',
  accent: '#i-plus',
};

const LAYER_LABELS = {
  background: 'Background',
  pattern: 'Pattern',
  image_frame: 'Image Frame',
  border: 'Border',
  decoration: 'Decoration',
  shape: 'Shape',
  accent: 'Accent',
};

let lastLayersStamp = '';

function parseOptionLabel(text) {
  const separator = text.lastIndexOf(' · ');
  if (separator === -1) return { id: text, type: '' };
  return { id: text.slice(0, separator), type: text.slice(separator + 3) };
}

function niceName(id, type) {
  if (!id) return LAYER_LABELS[type] ?? (type || 'Element');
  if (id === 'background') return 'Background';
  if (id === 'pattern') return 'Pattern';
  const match = id.match(/(\d+)\s*$/);
  if (match) return `${LAYER_LABELS[type] || type || 'Element'} ${match[1]}`;
  return id;
}

function iconButton(href, tooltip, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-icon-sm';
  button.dataset.tooltip = tooltip;
  button.innerHTML = `<svg class="icon"><use href="${href}"/></svg>`;
  button.addEventListener('click', onClick);
  return button;
}

function runFunctionalAction(action, root) {
  (root ?? document).querySelector(`[data-action="${action}"]`)?.click();
}

/* Structural elements are part of every template; the presentation layer must
   not offer Delete for them. Rationale: name derivation + structural types.
   (App-level validation stays Luna's responsibility.) */
function isStructuralElement(id, type) {
  if (type === 'background' || type === 'border') return true;
  const suffix = Number((String(id ?? '')).match(/(\d+)\s*$/)?.[1]);
  return (type === 'image_frame' && Number.isInteger(suffix) && suffix >= 1 && suffix <= 12)
    || (type === 'pattern' && id === 'pattern');
}

function elementFlag(flagClass, href, title) {
  const flag = document.createElement('span');
  flag.className = `layer-flag ${flagClass}`;
  flag.dataset.tooltip = title;
  flag.innerHTML = `<svg class="icon"><use href="${href}"/></svg>`;
  return flag;
}

function renderLayers() {
  if (!layersRoot || !elementSelect) return;
  const options = [...elementSelect.options];
  const design = window.__studio_design_snapshot;
  const byId = new Map((design?.elements ?? []).map(element => [element.id, element]));
  const stamp = `${options.map(o => {
    const element = byId.get(o.value);
    return `${o.value}:${element?.visible !== false}:${Boolean(element?.locked)}:${element?.fill ?? ''}`;
  }).join('|')}#${elementSelect.value}`;
  if (stamp === lastLayersStamp) return;
  lastLayersStamp = stamp;

  layersRoot.replaceChildren();
  for (const option of [...options].reverse()) {
    const { id, type } = parseOptionLabel(option.textContent);
    const element = byId.get(option.value);
    const row = document.createElement('li');
    row.className = 'layer-row';
    row.dataset.layerId = option.value;
    if (element?.visible === false) row.classList.add('is-hidden');
    if (element?.locked) row.classList.add('is-locked');
    row.dataset.tooltip = niceName(id, type);

    if (element && typeof element.fill === 'string' && /^#[0-9a-f]{6}$/i.test(element.fill)) {
      const swatch = document.createElement('span');
      swatch.className = 'layer-swatch';
      swatch.style.background = element.fill;
      row.append(swatch);
    }

    const icon = document.createElement('span');
    icon.className = 'layer-type-icon';
    icon.innerHTML = `<svg class="icon"><use href="${LAYER_ICONS[type] ?? '#i-layers'}"/></svg>`;

    const name = document.createElement('span');
    name.className = 'layer-name';
    name.textContent = niceName(id, type);

    /* Persistent state flags: the user must see lock/hidden without hovering. */
    if (element?.locked) row.append(elementFlag('is-locked', '#i-lock', 'Locked'));
    if (element?.visible === false) row.append(elementFlag('is-muted', '#i-eye-off', 'Hidden'));

    const actions = document.createElement('span');
    actions.className = 'layer-actions';
    actions.append(
      iconButton('#i-eye', 'Show / Hide', () => { selectElement(option.value); runFunctionalAction('element-toggle'); }),
      iconButton('#i-unlock', 'Lock / Unlock', () => { selectElement(option.value); runFunctionalAction('element-lock'); }),
    );

    row.append(icon, name, actions);
    row.addEventListener('click', event => {
      if (event.target.closest('button')) return;
      selectElement(option.value);
    });
    layersRoot.append(row);
  }
}

function selectElement(id) {
  if (elementSelect) {
    elementSelect.value = id;
    elementSelect.dispatchEvent(new Event('change', { bubbles: true }));
  }
  document.dispatchEvent(new CustomEvent('studio:element-selected', { detail: { id } }));
}

function syncLayersFromSelect() {
  renderLayers();
  $$('.layer-row', layersRoot).forEach(row => row.classList.toggle('is-selected', row.dataset.layerId === elementSelect?.value));
  updateSelectionChip();
  updateOverlay();
}

function selectedLayerInfo() {
  const selectedId = elementSelect?.value;
  if (!selectedId) return null;
  const option = [...(elementSelect?.options ?? [])].find(item => item.value === selectedId);
  if (!option) return null;
  const { id, type } = parseOptionLabel(option.textContent);
  const element = (window.__studio_design_snapshot?.elements ?? []).find(item => item.id === selectedId);
  return { id, type, name: niceName(id, type), element };
}

function updateSelectionChip() {
  const chip = $('[data-selection-chip]');
  if (!chip) return;
  const info = selectedLayerInfo();
  const label = $('[data-selection-label]', chip);
  const icon = $('[data-selection-icon]', chip);
  if (!info) {
    chip.dataset.status = 'none';
    if (label) label.textContent = 'Nothing selected';
    if (icon) icon.setAttribute('href', '#i-layers');
  } else {
    const locked = Boolean(info.element?.locked);
    chip.dataset.status = locked ? 'locked' : 'selected';
    if (label) label.textContent = locked ? `${info.name} · Locked` : info.name;
    if (icon) icon.setAttribute('href', locked ? '#i-lock' : '#i-layers');
  }
  /* Inspector action buttons always exist; deleting is restricted per element. */
  const deletable = Boolean(info && !isStructuralElement(info.id, info.type));
  const deleteButton = $('[data-action="element-delete"]');
  if (deleteButton) {
    deleteButton.disabled = !deletable;
    deleteButton.dataset.tooltip = deletable ? 'Delete element' : 'Template-structural elements cannot be deleted';
  }
  const resetButton = $('[data-action="element-reset"]');
  if (resetButton && info) resetButton.disabled = !deletable;
  const inspectorTarget = $('[data-inspector-target]');
  if (inspectorTarget && info) inspectorTarget.textContent = info.name;
}

document.addEventListener('studio:state-change', () => {
  requestAnimationFrame(() => {
    syncLayersFromSelect();
    runPresentationPass();
  });
  setTimeout(() => {
    syncLayersFromSelect();
    runPresentationPass();
  }, 50);
});

/* =============================================================== Tooltip
   Body-level host so tooltips are never clipped by ancestor overflow. */
const tooltipHost = document.createElement('div');
tooltipHost.id = 'tooltip-host';
tooltipHost.setAttribute('aria-hidden', 'true');
const tooltipNode = document.createElement('div');
tooltipNode.className = 'tooltip';
tooltipHost.append(tooltipNode);
document.body.append(tooltipHost);

let tooltipTarget = null;
let tooltipTimer = 0;

function showTooltip(target) {
  const text = target?.dataset?.tooltip;
  if (!text) return;
  tooltipNode.textContent = text;
  tooltipNode.classList.add('is-visible');
  tooltipNode.classList.remove('is-placed-left');

  const rect = target.getBoundingClientRect();
  const margin = 8;
  const { width, height } = tooltipNode.getBoundingClientRect();
  let top = rect.top - height - 7;
  let left = rect.left + rect.width / 2 - width / 2;
  if (top < margin) top = rect.bottom + 7;
  if (left < margin) left = margin;
  if (left + width > window.innerWidth - margin) {
    left = Math.max(margin, rect.left - width - 8);
    tooltipNode.classList.add('is-placed-left');
  }
  tooltipNode.style.top = `${Math.round(top)}px`;
  tooltipNode.style.left = `${Math.round(left)}px`;
}

document.addEventListener('mouseover', event => {
  const target = event.target.closest?.('[data-tooltip]');
  if (target === tooltipTarget) return;
  window.clearTimeout(tooltipTimer);
  tooltipTarget = target;
  if (!target) { tooltipNode.classList.remove('is-visible'); return; }
  tooltipTimer = window.setTimeout(() => showTooltip(target), 350);
});

document.addEventListener('focusin', event => {
  const target = event.target.closest?.('[data-tooltip]');
  window.clearTimeout(tooltipTimer);
  tooltipTarget = target;
  if (target) showTooltip(target);
  else tooltipNode.classList.remove('is-visible');
});

document.addEventListener('mousedown', () => {
  window.clearTimeout(tooltipTimer);
  tooltipNode.classList.remove('is-visible');
});

/* ==================================================== Designer add menu */
const addTrigger = $('#design-add-trigger');
const addMenu = $('#design-add-menu');

function setAddMenu(open) {
  addMenu?.classList.toggle('is-open', open);
  addTrigger?.setAttribute('aria-expanded', String(open));
}

addTrigger?.addEventListener('click', event => {
  event.stopPropagation();
  setAddMenu(!addMenu?.classList.contains('is-open'));
});

addMenu?.addEventListener('click', event => {
  if (event.target.closest('[data-action="element-add-decoration"], [data-action="element-add-pattern"]')) {
    window.setTimeout(() => setAddMenu(false), 60);
  }
});

document.addEventListener('click', event => {
  if (addMenu?.classList.contains('is-open') && !event.target.closest('.menu-anchor')) setAddMenu(false);
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && addMenu?.classList.contains('is-open')) {
    setAddMenu(false);
    addTrigger?.focus();
  }
});

/* The menu sits inside a scrollable toolbar document; keep it above panels
   and re-anchor under the trigger whenever geometry changes. */
function repositionAddMenu() {
  if (!addTrigger || !addMenu || !addMenu.classList.contains('is-open')) return;
  const anchorRect = addTrigger.getBoundingClientRect();
  const width = addMenu.offsetWidth;
  const center = anchorRect.left + anchorRect.width / 2;
  const desiredViewportLeft = Math.min(Math.max(4, center - width / 2), window.innerWidth - width - 4);
  addMenu.style.left = `${Math.round(desiredViewportLeft - anchorRect.left)}px`;
}

new ResizeObserver(repositionAddMenu).observe(document.documentElement);

/* ================================================= Designer editor overlay
   Interface-only feedback layer above the canvas. Geometry is derived from
   the design snapshot (artboard coordinates) mapped into stage pixel space,
   so it follows the canvas even when zoom or layout changes. */
const overlay = $('#designer-overlay');
const overlayRoot = overlay?.parentElement ?? null;   /* .canvas-content */
let hoverBox = null;
let selectionBox = null;
let snapGuides = { x: null, y: null };

const HANDLE_DIRECTIONS = ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'];
const HANDLE_OFFSETS = {
  nw: [0, 0], n: [0.5, 0], ne: [1, 0],
  w: [0, 0.5], e: [1, 0.5],
  sw: [0, 1], s: [0.5, 1], se: [1, 1],
};

function stageGeometry() {
  if (!canvas) return null;
  const canvasRect = canvas.getBoundingClientRect();
  if (!canvasRect.width || !canvasRect.height) return null;
  const design = window.__studio_design_snapshot;
  const artboard = design?.artboard || { width: 1200, height: 1600 };
  const logicalRatio = artboard.width / artboard.height;
  const pixelRatio = canvasRect.width / canvasRect.height;
  /* Letterbox mapping: whichever dimension is proportionally larger gets the
     empty bars; the other fills. The canvas aspect matches the artboard
     (600×800 vs 1200×1600), so today both scales are exactly 1. */
  const scaleX = pixelRatio > logicalRatio ? pixelRatio / logicalRatio : 1;
  const scaleY = pixelRatio < logicalRatio ? logicalRatio / pixelRatio : 1;
  return {
    canvasRect,
    contentLeft: canvasRect.left + (canvasRect.width * (scaleX - 1)) / 2,
    contentTop: canvasRect.top + (canvasRect.height * (scaleY - 1)) / 2,
    contentWidth: canvasRect.width / scaleX,
    contentHeight: canvasRect.height / scaleY,
  };
}

function artboardToStage(x, y, geometry) {
  const design = window.__studio_design_snapshot;
  const artboard = design?.artboard || { width: 1200, height: 1600 };
  return {
    left: geometry.contentLeft + (x / artboard.width) * geometry.contentWidth,
    top: geometry.contentTop + (y / artboard.height) * geometry.contentHeight,
  };
}

function stageBoxForElement(element, geometry) {
  const start = artboardToStage(element.x ?? 0, element.y ?? 0, geometry);
  const end = artboardToStage((element.x ?? 0) + (element.width ?? 0), (element.y ?? 0) + (element.height ?? 0), geometry);
  return {
    left: start.left,
    top: start.top,
    width: Math.max(2, end.left - start.left),
    height: Math.max(2, end.top - start.top),
  };
}

function placeBox(box, boxNode, boxClass) {
  const rootRect = overlayRoot.getBoundingClientRect();
  boxNode.className = `overlay-box ${boxClass}`;
  boxNode.style.left = `${Math.round(box.left - rootRect.left)}px`;
  boxNode.style.top = `${Math.round(box.top - rootRect.top)}px`;
  boxNode.style.width = `${Math.round(box.width)}px`;
  boxNode.style.height = `${Math.round(box.height)}px`;
}

function renderHandles(boxNode, box) {
  /* Children position against the padding box, but the box rect (and the
     visual outline) is the border box — compensate so handle centers sit
     exactly on the outline corners/edges at any zoom. */
  const boxStyle = getComputedStyle(boxNode);
  const borderX = parseFloat(boxStyle.borderLeftWidth) || 0;
  const borderY = parseFloat(boxStyle.borderTopWidth) || 0;
  for (const direction of HANDLE_DIRECTIONS) {
    const handle = document.createElement('div');
    handle.className = 'overlay-handle';
    handle.dataset.dir = direction;
    const [fx, fy] = HANDLE_OFFSETS[direction];
    /* Handles are children of the already-positioned selection box; use local
       offsets here so they stay attached to its actual NW/N/NE/... anchors. */
    handle.style.left = `${Math.round(box.width * fx - 4.5 - borderX)}px`;
    handle.style.top = `${Math.round(box.height * fy - 4.5 - borderY)}px`;
    boxNode.append(handle);
  }
}

function disposeBox(node) {
  node?.remove();
}

function buildSelectionBox(box, element) {
  const locked = Boolean(element?.locked);
  const boxNode = document.createElement('div');
  placeBox(box, boxNode, locked ? 'is-selected is-locked' : 'is-selected');
  if (element?.visible === false) {
    const badge = document.createElement('span');
    badge.className = 'overlay-badge is-ghost';
    badge.textContent = 'Hidden';
    boxNode.append(badge);
  }
  if (locked) {
    const badge = document.createElement('span');
    badge.className = 'overlay-badge';
    badge.innerHTML = '<svg class="icon"><use href="#i-lock"/></svg>Locked';
    boxNode.append(badge);
  }
  /* Handles are rendered by updateOverlay AFTER the box is attached, so the
     computed border width is available for exact corner anchoring. */
  return boxNode;
}

function updateOverlay() {
  if (!overlay || !canvas) return;
  const geometry = stageGeometry();
  if (!geometry) return;
  const snapshot = window.__studio_design_snapshot;
  if (!snapshot?.elements?.length) {
    hoverBox = disposeBox(hoverBox);
    selectionBox = disposeBox(selectionBox);
    return;
  }

  /* Selection follows the functional state exactly — never a competing one. */
  const selectedId = elementSelect?.value;
  const selected = snapshot.elements.find(item => item.id === selectedId)
    ?? snapshot.elements.find(item => item.id === elementSelect?.value);
  const hover = hoverElementId && hoverElementId !== selectedId
    ? snapshot.elements.find(item => item.id === hoverElementId)
    : null;

  hoverBox = disposeBox(hoverBox);
  selectionBox = disposeBox(selectionBox);

  if (hover && hover.visible !== false) {
    hoverBox = document.createElement('div');
    placeBox(stageBoxForElement(hover, geometry), hoverBox, 'is-hover');
    overlay.append(hoverBox);
  }

  if (selected) {
    const box = stageBoxForElement(selected, geometry);
    selectionBox = buildSelectionBox(box, selected);
    overlay.append(selectionBox);
    /* Match the functional resize rule: handles only on visible, unlocked
       elements. Rendered after attachment so border compensation is exact. */
    if (selected.visible !== false && !selected.locked) renderHandles(selectionBox, box);
  }
}

/* Hover detection: map pointer position over the canvas to artboard space. */
let hoverElementId = null;

function elementAtPoint(event) {
  const design = window.__studio_design_snapshot;
  if (!design?.elements?.length || !canvas) return null;
  const geometry = stageGeometry();
  if (!geometry) return null;
  const rootRect = overlayRoot.getBoundingClientRect();
  const pointX = event.clientX, pointY = event.clientY;
  if (pointX < geometry.contentLeft || pointX > geometry.contentLeft + geometry.contentWidth) return null;
  if (pointY < geometry.contentTop || pointY > geometry.contentTop + geometry.contentHeight) return null;

  const artboard = design.artboard || { width: 1200, height: 1600 };
  const logicalX = ((pointX - geometry.contentLeft) / geometry.contentWidth) * artboard.width;
  const logicalY = ((pointY - geometry.contentTop) / geometry.contentHeight) * artboard.height;
  const visible = [...design.elements].filter(item => item.visible !== false).reverse();
  return visible.find(item => logicalX >= item.x && logicalX <= item.x + item.width
    && logicalY >= item.y && logicalY <= item.y + item.height) ?? null;
}

canvas?.addEventListener('pointermove', event => {
  if (event.pointerType === 'touch') return;
  const hit = elementAtPoint(event);
  const id = hit?.id ?? null;
  if (id !== hoverElementId) {
    hoverElementId = id;
    updateOverlay();
  }
});

canvas?.addEventListener('pointerleave', () => {
  if (hoverElementId !== null) {
    hoverElementId = null;
    updateOverlay();
  }
});

/* Snap guides from the functional adapter's studio:snap-guides event. */
function updateGuides(guides) {
  snapGuides = { x: guides?.x ?? null, y: guides?.y ?? null };
  renderGuides();
}

function renderGuides() {
  if (!overlay) return;
  const design = window.__studio_design_snapshot;
  const geometry = stageGeometry();
  const guideX = $('[data-guide="x"]', overlay);
  const guideY = $('[data-guide="y"]', overlay);
  if (!geometry || !design) {
    if (guideX) { guideX.hidden = true; guideX.classList.remove('is-visible'); }
    if (guideY) { guideY.hidden = true; guideY.classList.remove('is-visible'); }
    return;
  }
  const rootRect = overlayRoot.getBoundingClientRect();

  if (snapGuides.x !== null && snapGuides.x !== undefined && guideX) {
    const position = artboardToStage(snapGuides.x, 0, geometry);
    guideX.hidden = false;
    guideX.classList.add('is-visible');
    guideX.style.left = `${Math.round(position.left - rootRect.left)}px`;
  } else if (guideX) {
    guideX.hidden = true;
    guideX.classList.remove('is-visible');
  }

  if (snapGuides.y !== null && snapGuides.y !== undefined && guideY) {
    const position = artboardToStage(0, snapGuides.y, geometry);
    guideY.hidden = false;
    guideY.classList.add('is-visible');
    guideY.style.top = `${Math.round(position.top - rootRect.top)}px`;
  } else if (guideY) {
    guideY.hidden = true;
    guideY.classList.remove('is-visible');
  }
}

document.addEventListener('studio:snap-guides', event => updateGuides(event.detail));

/* Recompute overlay when the design model or selection changes, on stage
   scroll/zoom, when the stage geometry itself changes, and after undo/redo
   (the functional adapter dispatches 'undo' on the undo/redo buttons). */
document.addEventListener('studio:state-change', () => {
  requestAnimationFrame(updateOverlay);
});

/* Explicit selection changes (canvas/layer clicks, list changes) mirror
   synchronously: handles, chips, and inspector must reflect the new selection
   — including handle removal for locked elements — in the same tick, while
   bulk geometry changes stay frame-batched above. */
document.addEventListener('studio:element-selected', () => {
  syncLayersFromSelect();
});

$$('[data-action="design-undo"], [data-action="design-redo"]').forEach(node => {
  node.addEventListener('undo', () => window.setTimeout(updateOverlay, 60));
});

const designerStage = canvas?.closest('.stage');
designerStage?.addEventListener('scroll', () => requestAnimationFrame(updateOverlay), { passive: true });

if (designerStage && typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(() => requestAnimationFrame(updateOverlay)).observe(designerStage);
}

if (canvas && typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(() => requestAnimationFrame(updateOverlay)).observe(canvas);
}

/* Clear transient hover/selection feedback when leaving the Designer view. */
function resetOverlay() {
  hoverElementId = null;
  hoverBox = disposeBox(hoverBox);
  selectionBox = disposeBox(selectionBox);
  renderGuides();
}

/* ------------------------------------------------- Mirror rendering helpers */
function mirrorList(sourceSel, targetSel, build) {
  const source = $(sourceSel);
  const target = $(targetSel);
  if (!source || !target) return;
  const html = source.innerHTML;
  if (target.dataset.stamp === html) return;
  target.dataset.stamp = html;
  target.replaceChildren();
  build(source, target);
}

/* Mirror the Designer strip list into the Photos side panel. The functional
   list is left untouched; chips re-query and click the attached originals. */
function mirrorStrips() {
  mirrorList('[data-bind="strip-list"]', '[data-mirror-list="strip-list"]', (source, target) => {
    [...source.children].forEach((li, index) => {
      const button = li.querySelector('button[data-action="select-strip"]');
      if (!button) return;
      const stripId = button.dataset.stripId;
      const ratio = (button.textContent.match(/\d+\s*\/\s*\d+/) || [''])[0];

      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'strip-chip';
      chip.dataset.stripId = stripId;

      const num = document.createElement('span');
      num.className = 'strip-chip-num';
      num.textContent = String(index + 1);

      const name = document.createElement('span');
      name.className = 'strip-chip-name';
      name.textContent = `Strip ${index + 1}`;

      const meta = document.createElement('span');
      meta.className = 'strip-chip-meta';
      meta.textContent = ratio ? `${ratio} photos` : '';

      chip.append(num, name, meta);
      chip.addEventListener('click', () => {
        document.querySelector(`[data-bind="strip-list"] button[data-strip-id="${CSS.escape(stripId)}"]`)?.click();
      });

      const item = document.createElement('li');
      item.append(chip);
      target.append(item);
    });
  });
}

function mirrorChecks() {
  mirrorList('[data-bind="validation-checks"]', '[data-mirror-list="validation-checks"]', (source, target) => {
    for (const li of source.children) {
      const status = li.dataset.status ?? 'PASS';
      const row = document.createElement('li');
      row.className = 'check-row';
      row.dataset.status = status;
      const label = document.createElement('span');
      label.className = 'check-status';
      label.textContent = status;
      const msg = document.createElement('span');
      msg.className = 'check-msg';
      msg.textContent = li.textContent.replace(/^(PASS|WARNING|ERROR)\s*/, '').trim();
      row.append(label, msg);
      target.append(row);
    }
  });
}

function mirrorTemplateOptions() {
  const source = $('[data-bind="template-list"]');
  if (!source) return;
  $$('[data-mirror-options="template-list"]').forEach(target => {
    const stamp = source.innerHTML + '#' + source.value;
    if (target.dataset.stamp === stamp) return;
    target.dataset.stamp = stamp;
    target.replaceChildren();
    for (const option of source.options) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'template-chip' + (option.value === source.value ? ' is-current' : '');
      chip.textContent = option.textContent;
      chip.addEventListener('click', () => {
        source.value = option.value;
        source.dispatchEvent(new Event('change', { bubbles: true }));
        mirrorTemplateOptions();
      });
      target.append(chip);
    }
  });
}

function mirrorCounts() {
  const map = {
    'image-count': 'mirror-image-count',
    'strip-count': 'mirror-strip-count',
    'page-count': 'mirror-page-count',
  };
  for (const [from, to] of Object.entries(map)) {
    const fromNode = $(`[data-bind="${from}"]`);
    const toNode = $(`[data-bind="${to}"]`);
    if (fromNode && toNode && toNode.textContent !== fromNode.textContent) toNode.textContent = fromNode.textContent;
  }
}

function mirrorValidationTopbar() {
  const status = $('[data-bind="validation-status"]');
  const chip = $('[data-bind="mirror-validation-status"]');
  if (!status || !chip) return;
  const text = status.textContent.trim();
  if (!text) { chip.hidden = true; return; }
  chip.hidden = false;
  chip.dataset.status = text;
  const label = $('[data-vchip-label]', chip);
  if (label) label.textContent = text === 'PASS' ? 'Print ready' : text === 'WARNING' ? 'Check warnings' : 'Needs attention';
  const icon = $('[data-vchip-icon]', chip);
  if (icon) icon.setAttribute('href', text === 'PASS' ? '#i-check' : text === 'WARNING' ? '#i-warn' : '#i-error');
}

function mirrorFit() {
  const fit = $('[data-fit]');
  if (!fit || !window.__studio_print_layout?.fit) return;
  const value = window.__studio_print_layout?.fit?.valid ? 'PASS' : 'ERROR';
  fit.dataset.status = value;
  const icon = $('[data-fit-icon]');
  if (icon) icon.setAttribute('href', value === 'ERROR' ? '#i-error' : '#i-check');
  const title = $('[data-fit-title]');
  if (title) title.textContent = value === 'ERROR' ? 'Does not fit' : 'Fits safely';
}

function mirrorPrintSize() {
  const width = $('[data-bind="strip-width-mm"]')?.value;
  const height = $('[data-bind="strip-height-mm"]')?.value;
  if (!width || !height) return;
  const preset = $('[data-bind="strip-preset"]')?.value;
  const label = preset === 'photobooth-2x6' ? '2 × 6 in · '
    : preset === 'photobooth-54x180' ? '54 × 180 mm · ' : 'Custom · ';
  $$('[data-print-size-value]').forEach(node => {
    node.textContent = `${label}${Number(width).toFixed(1)} × ${Number(height).toFixed(1)} mm`;
  });
}

/* Highlight the strip the user selected in either list. */
document.addEventListener('click', event => {
  const button = event.target.closest('[data-action="select-strip"], .strip-chip[data-strip-id]');
  const stripId = button?.dataset.stripId;
  if (!stripId) return;
  $$('.strip-chip-list button[data-strip-id]').forEach(node => {
    node.classList.toggle('is-active', node.dataset.stripId === stripId);
  });
});

/* ------------------------------------------------------------- Sheet schema */
const sheetStrips = $('[data-sheet-strips]');
let lastSheetKey = '';

function renderSheet() {
  if (!sheetStrips) return;
  const layout = window.__studio_print_layout;
  if (!layout) return;
  const pageW = Number(layout.pageWidthMm);
  const pageH = Number(layout.pageHeightMm);
  const settings = layout.settings || {};
  const margin = Number(settings.marginMm || 0);
  const width = Number(settings.stripWidthMm || 0);
  const height = Number(settings.stripHeightMm || 0);
  const placements = layout.pages?.[0]?.placements || [];
  const key = JSON.stringify([pageW, pageH, margin, width, height, layout.fit, placements]);
  if (key === lastSheetKey) return;
  lastSheetKey = key;
  const sheet = $('[data-sheet]');
  if (sheet) {
    sheet.style.aspectRatio = `${pageW} / ${pageH}`;
    sheet.style.width = `min(calc(100cqh * ${pageW / pageH}), 100cqw, 1100px)`;
    sheet.setAttribute('aria-label', `A4 ${layout.orientation} page with vertical strips at ${width} by ${height} millimeters`);
  }
  sheetStrips.replaceChildren();
  const marginNode = $('[data-sheet-margin]');
  if (marginNode) marginNode.style.inset = `${(margin / pageH) * 100}% ${(margin / pageW) * 100}%`;
  for (const placement of placements) {
    const strip = document.createElement('div');
    strip.className = 'sheet-strip';
    strip.style.left = `${(Number(placement.xMm) / pageW) * 100}%`;
    strip.style.top = `${(Number(placement.yMm) / pageH) * 100}%`;
    strip.style.width = `${(Number(placement.widthMm) / pageW) * 100}%`;
    strip.style.height = `${(Number(placement.heightMm) / pageH) * 100}%`;
    for (let slot = 0; slot < (window.__studio_strip_spec?.frameCount || 4); slot += 1) {
      strip.append(Object.assign(document.createElement('span'), { className: 'sheet-strip-inner' }));
    }
    sheetStrips.append(strip);
  }
  const meta = $('[data-sheet-meta]');
  if (meta) {
    meta.textContent = `${layout.fit?.effectiveCount || 0} per page · ${width.toFixed(1)} × ${height.toFixed(1)} mm`;
    meta.style.color = layout.fit?.valid ? '' : 'var(--warn-ink)';
  }
  sheet?.classList.toggle('is-overflow', !layout.fit?.valid);
}
document.addEventListener('studio:print-layout-updated', renderSheet);

/* ------------------------------------------------------- Empty-state sync */
function syncEmptyStates() {
  const imageCount = Number($('[data-bind="image-count"]')?.textContent || 0);
  const libraryEmpty = $('[data-empty="library"]');
  if (libraryEmpty) libraryEmpty.hidden = imageCount > 0;

  const stripCount = Number($('[data-bind="strip-count"]')?.textContent || 0);
  const stripsEmpty = $('[data-empty="strips"]');
  if (stripsEmpty) stripsEmpty.hidden = stripCount > 0;
  const stripCard = $('[data-mirror-card="strip-list"]');
  if (stripCard) stripCard.hidden = stripCount === 0;

  const checksList = $('[data-bind="validation-checks"]');
  const checksEmpty = $('[data-empty="checks"]');
  if (checksEmpty && checksList) checksEmpty.hidden = checksList.children.length > 0;

  const preview = $('[data-bind="pdf-preview"]');
  const previewEmpty = $('[data-empty="preview"]');
  if (previewEmpty && preview) previewEmpty.hidden = Boolean(preview.getAttribute('src'));
}

/* ------------------------------------------------------------- Drop hint */
const libraryStage = $('.library-stage');
const dropPanel = $('[data-panel="image-library"]');
dropPanel?.addEventListener('dragover', event => {
  if (!event.dataTransfer?.types?.includes('Files')) return;
  event.preventDefault();
  libraryStage?.classList.add('is-drop-target');
});
dropPanel?.addEventListener('dragleave', event => {
  if (event.target === dropPanel) libraryStage?.classList.remove('is-drop-target');
});
dropPanel?.addEventListener('drop', () => libraryStage?.classList.remove('is-drop-target'));

/* ----------------------------------------------------- Toasts & statusbar */
const toastsRoot = $('[data-panel="toasts"]');

function toast(kind, message) {
  if (!toastsRoot) return;
  const iconHref = kind === 'error' ? '#i-error' : kind === 'warning' ? '#i-warn' : kind === 'success' ? '#i-check' : '#i-info';
  const title = kind === 'error' ? 'Something went wrong' : kind === 'warning' ? 'Check this' : kind === 'success' ? 'Done' : 'Heads up';
  const node = document.createElement('div');
  node.className = 'toast';
  node.dataset.kind = kind;
  node.innerHTML = `
    <span class="toast-icon"><svg class="icon"><use href="${iconHref}"/></svg></span>
    <span class="toast-body"><span class="toast-title">${title}</span>
    <span class="toast-msg"></span></span>
    <button type="button" class="toast-close" aria-label="Dismiss"><svg class="icon"><use href="#i-close"/></svg></button>`;
  $('.toast-msg', node).textContent = message;
  const dismiss = () => {
    node.classList.add('is-leaving');
    setTimeout(() => node.remove(), 180);
  };
  $('.toast-close', node).addEventListener('click', dismiss);
  toastsRoot.append(node);
  setTimeout(dismiss, kind === 'error' ? 7000 : 4200);
}

/* Bridge failures surface as frontend errors; show them as toasts, quietly. */
window.addEventListener('error', event => {
  const message = String(event.message || '');
  if (/pywebview|bridge/i.test(message)) toast('error', message);
});

const statusbar = $('#app-status-bar');
const jobProgress = $('[data-job-progress]');
const jobBar = $('[data-job-bar]');

document.addEventListener('studio:job-progress', event => {
  const job = event.detail ?? {};
  if (jobProgress) {
    jobProgress.hidden = false;
    const label = $('[data-job-label]');
    if (label) label.textContent = job.message || job.label || 'Working…';
  }
  const progress = Number(job.progress);
  if (jobBar) {
    if (Number.isFinite(progress) && progress > 0) {
      jobProgress?.classList.remove('is-indeterminate');
      jobBar.style.width = `${Math.min(100, Math.max(4, progress <= 1 ? progress * 100 : progress))}%`;
    } else {
      jobProgress?.classList.add('is-indeterminate');
    }
  }
});

document.addEventListener('studio:validation-updated', () => {
  requestAnimationFrame(() => {
    mirrorChecks();
    mirrorFit();
    mirrorValidationTopbar();
    syncEmptyStates();
  });
});

document.addEventListener('studio:preview-updated', () => {
  requestAnimationFrame(() => {
    syncEmptyStates();
    if (previewScale !== 'fit') applyPreviewMode(previewScale);
  });
});

/* ------------------------------------------------------- Statusbar reflect */
const statusNode = $('[data-bind="app-status"]');
const statusObserver = new MutationObserver(() => {
  if (!statusbar || !statusNode) return;
  const text = statusNode.textContent.trim().toLowerCase();
  statusbar.classList.toggle('is-busy', Boolean(text) && text !== 'ready');
  statusbar.classList.toggle('is-error', /fail|error|could not|not ready/.test(text));
  if (text === 'ready' && jobProgress) jobProgress.hidden = true;
});
if (statusNode) {
  statusObserver.observe(statusNode, { childList: true, characterData: true, subtree: true });
} else {
  statusObserver.observe(document.body, { childList: true, subtree: true });
}

/* ---------------------------------------------------- Global change mirror */
document.addEventListener('change', () => requestAnimationFrame(() => {
  renderSheet();
  mirrorPrintSize();
  syncEmptyStates();
  mirrorTemplateOptions();
  mirrorCounts();
  mirrorStrips();
  runPresentationPass();
  if (document.body.dataset.activeView === 'designer') updateOverlay();
}));

document.addEventListener('studio:state-change', () => requestAnimationFrame(() => {
  renderSheet();
  mirrorPrintSize();
  syncEmptyStates();
  mirrorTemplateOptions();
  mirrorCounts();
  mirrorStrips();
  mirrorChecks();
  mirrorFit();
  mirrorValidationTopbar();
}));

/* --------------------------------------------------- Fit details reveal */
$('[data-vaction="fit-details"]')?.addEventListener('click', event => {
  const button = event.currentTarget;
  const reveal = $('[data-reveal="fit-details"]');
  if (!reveal) return;
  const open = reveal.hidden;
  reveal.hidden = !open;
  button.setAttribute('aria-expanded', String(open));
});

/* ------------------------------------------------- Template featured load */
$('[data-vaction="tpl-load-builtin"]')?.addEventListener('click', () => {
  const source = $('[data-bind="template-list"]');
  const option = [...(source?.options ?? [])].find(item => item.value === 'dcapture-clean' || /dcapture/i.test(item.textContent));
  if (source && option) {
    source.value = option.value;
    source.dispatchEvent(new Event('change', { bubbles: true }));
  }
  runFunctionalAction('template-load');
});

/* ============================================ Image Library presentation
   app.js renders a functional list (img + name + Up/Down/Remove text buttons).
   This mirror re-presents each item as a card: large adaptive thumbnail,
   single-line filename with title tooltip, secondary dimension metadata, and
   square icon actions that REUSE the functional buttons' data-action hooks so
   app.js keeps owning all image logic. The functional nodes stay in the DOM
   (hidden) so app.js re-renders and observers keep working untouched. */
const imageListRoot = $('[data-bind="image-list"]');
let lastImageStamp = null;

function parseDimensions(text) {
  const match = String(text || '').match(/\((\d+)\s*×\s*(\d+|\?)\)\s*$/);
  if (!match) return null;
  return { width: match[1], height: match[2] };
}

function iconAction(action, imageId, href, label) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-icon-sm';
  button.dataset.action = action;
  button.dataset.imageId = imageId;
  button.dataset.tooltip = label;
  button.setAttribute('aria-label', label);
  button.innerHTML = `<svg class="icon"><use href="${href}"/></svg>`;
  return button;
}

function setCardThumbnailState(card, state) {
  if (!card) return;
  const normalized = ['loading', 'ready', 'failed'].includes(state) ? state : 'loading';
  const thumb = card.querySelector('.image-item-thumb');
  if (!thumb) return;
  card.dataset.thumbnailState = normalized;
  thumb.classList.toggle('is-loading', normalized === 'loading');
  thumb.classList.toggle('is-failed', normalized === 'failed');
  let failure = thumb.querySelector('.thumbnail-failure');
  if (normalized === 'failed' && !failure) {
    failure = document.createElement('div');
    failure.className = 'thumbnail-failure';
    const label = document.createElement('span');
    label.textContent = 'Thumbnail failed';
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'btn btn-sm';
    retry.dataset.action = 'image-thumbnail-retry';
    retry.dataset.imageId = card.dataset.imageId || '';
    retry.textContent = 'Retry';
    failure.append(label, retry);
    thumb.append(failure);
  } else if (normalized !== 'failed') failure?.remove();
}

document.addEventListener('studio:thumbnail-state', event => {
  const { imageId, state } = event.detail || {};
  for (const card of imageListRoot?.querySelectorAll('.image-item') || []) {
    if (card.dataset.imageId === imageId) setCardThumbnailState(card, state);
  }
});

function enhanceImageLibrary() {
  if (!imageListRoot) return;
  const bucket = imageListRoot.querySelector(':scope > [data-functional-image-list]');
  const cards = imageListRoot.querySelector(':scope > .image-grid');
  const strayItems = [...imageListRoot.children].filter(node => node !== bucket && node !== cards);

  /* Source order is authoritative even when a reorder keeps the same count. */
  const sourceItems = [...(bucket?.children || []), ...strayItems];
  const sourceStamp = sourceItems.map(node => `${node.dataset.imageId || ''}:${node.dataset.thumbnailState || 'loading'}`).join('|');
  const renderedCards = cards ? [...cards.querySelectorAll(':scope > .image-item')] : [];
  const cardStamp = renderedCards.map(card => `${card.dataset.imageId || ''}:${card.dataset.thumbnailState || 'loading'}`).join('|');
  if (sourceStamp === lastImageStamp && sourceStamp === cardStamp) return;
  lastImageStamp = sourceStamp;

  /* Harvest thumbnail nodes from the previous cards so async-loaded images
     survive a rebuild (the functional <li> no longer owns them). */
  const harvested = new Map();
  for (const img of imageListRoot.querySelectorAll('.image-item-thumb img[data-image-id]')) {
    harvested.set(img.dataset.imageId, img);
  }

  const functional = bucket ?? (() => {
    const node = document.createElement('div');
    node.hidden = true;
    node.setAttribute('aria-hidden', 'true');
    node.dataset.functionalImageList = '';
    return node;
  })();
  for (const node of strayItems) functional.append(node);

  const grid = cards ?? document.createElement('div');
  grid.className = 'image-grid';
  grid.replaceChildren();

  [...functional.children].forEach((li, index) => {
    const imageId = li.dataset.imageId ?? '';
    const label = li.querySelector('span');
    const displayName = label?.dataset.displayName ?? '';
    const rawName = label?.textContent ?? '';
    const dims = parseDimensions(rawName);
    const name = displayName || (dims
      ? rawName.slice(0, rawName.length - dims.width.length - dims.height.length - 4).trim()
      : rawName);

    const card = document.createElement('article');
    card.className = 'image-item';
    card.dataset.imageId = imageId;

    const thumb = document.createElement('div');
    thumb.className = 'image-item-thumb';
    const thumbnail = harvested.get(imageId) ?? li.querySelector('img');
    if (thumbnail) {
      thumbnail.removeAttribute('width');
      thumbnail.removeAttribute('height');
      thumbnail.setAttribute('aria-hidden', 'true');
      thumb.append(thumbnail);
    }

    const check = document.createElement('span');
    check.className = 'image-item-check';
    check.setAttribute('aria-hidden', 'true');
    check.innerHTML = '<svg class="icon"><use href="#i-check"/></svg>';

    const grip = document.createElement('span');
    grip.className = 'image-item-grip';
    grip.setAttribute('aria-hidden', 'true');
    grip.title = 'Drag to reorder';
    grip.innerHTML = '<svg class="icon"><use href="#i-handle"/></svg>';

    const num = document.createElement('span');
    num.className = 'image-item-num';
    num.textContent = String(index + 1);

    const body = document.createElement('div');
    body.className = 'image-item-body';
    const nameNode = document.createElement('span');
    nameNode.className = 'image-item-name';
    nameNode.textContent = name || 'Untitled image';
    nameNode.title = name || 'Untitled image';
    const dimsNode = document.createElement('span');
    dimsNode.className = 'image-item-dims';
    dimsNode.textContent = dims ? `${dims.width} × ${dims.height} px` : '';
    body.append(nameNode, dimsNode);

    const actions = document.createElement('div');
    actions.className = 'image-item-actions';
    const sourceUp = li.querySelector('button[data-action="image-move-up"], button[data-action="image-up"]');
    const sourceDown = li.querySelector('button[data-action="image-move-down"], button[data-action="image-down"]');
    const sourceRemove = li.querySelector('button[data-action="image-remove"]');
    /* Icon buttons reuse the functional buttons' data-action hooks and carry
       their aria-labels so app.js keeps owning reorder/remove behavior. */
    const up = iconAction(sourceUp?.dataset.action ?? 'image-move-up', imageId, '#i-chev-up', sourceUp?.getAttribute('aria-label') ?? 'Move earlier');
    const down = iconAction(sourceDown?.dataset.action ?? 'image-move-down', imageId, '#i-chev-down', sourceDown?.getAttribute('aria-label') ?? 'Move later');
    const remove = iconAction('image-remove', imageId, '#i-trash', sourceRemove?.getAttribute('aria-label') ?? 'Remove image');
    remove.classList.add('is-danger');
    if (sourceUp) up.disabled = sourceUp.disabled;
    if (sourceDown) down.disabled = sourceDown.disabled;
    if (sourceRemove) remove.disabled = sourceRemove.disabled;
    actions.append(up, down, remove);

    card.append(thumb, check, grip, num, body, actions);
    setCardThumbnailState(card, li.dataset.thumbnailState || thumbnail?.dataset.thumbnailState || (thumbnail?.getAttribute('src') ? 'ready' : 'loading'));
    grid.append(card);
  });

  /* Lead-photo emphasis: the first photo reads as the current one. */
  if (functional.children.length > 1) grid.firstElementChild?.classList.add('is-selected');

  imageListRoot.append(functional, grid);
  const stage = imageListRoot.closest('.library-stage');
  stage?.classList.toggle('has-images', functional.children.length > 0);
  stage?.classList.toggle('is-manual', imageListRoot.classList.contains('is-manual'));
}

if (imageListRoot) {
  let queued = false;
  new MutationObserver(records => {
    if (!records.some(record => record.type === 'childList' && record.target === imageListRoot) || queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; enhanceImageLibrary(); syncLibraryMode(); });
  }).observe(imageListRoot, { childList: true });
}

/* Grip cue only makes sense while Manual order is active with several photos. */
function syncLibraryMode() {
  const mode = $('[data-action="sort-images"]')?.value || 'smart';
  const manual = mode === 'manual' && Number($('[data-bind="image-count"]')?.textContent || 0) > 1;
  imageListRoot?.classList.toggle('is-manual', manual);
}

/* ------------------------------------------------- Text element inspector
   Luna's app.js reads the text binds (text-content, text-font-*, text-color,
   text-align, text-letter-spacing, text-line-height) directly. This layer
   only adds contextual presentation: the sections and alignment control
   activate when a type:text element is selected, and the parked text-align
   select stays in step with the visual buttons. */
const inspectorCard = $('fieldset[data-panel="design-properties"]');
const textContent = $('[data-bind="text-content"]');
const parkedTextAlign = $('[data-bind="text-align"]');

function currentSelectedElement() {
  const id = elementSelect?.value;
  if (!id) return null;
  return (window.__studio_design_snapshot?.elements ?? []).find(item => item.id === id) ?? null;
}

function updateInspectorMode() {
  if (!inspectorCard) return;
  const element = currentSelectedElement();
  const isText = element?.type === 'text';
  inspectorCard.classList.toggle('is-text-element', Boolean(isText));
  if (!isText) return;
  if (document.activeElement !== textContent && textContent) textContent.value = element.text ?? element.content ?? '';
  const align = element.textAlign || 'center';
  if (parkedTextAlign && document.activeElement !== parkedTextAlign) parkedTextAlign.value = align;
  $$('[data-text-align]').forEach(button => {
    const active = align === button.dataset.textAlign;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
    button.disabled = Boolean(element.locked);
  });
}

/* Alignment buttons mirror into the parked functional select; app.js owns the
   design.update call that follows the change event. */
$$('[data-text-align]').forEach(button => {
  button.addEventListener('click', () => {
    if (!currentSelectedElement() || currentSelectedElement()?.type !== 'text') return;
    forwardSelect(parkedTextAlign, button.dataset.textAlign);
  });
});

parkedTextAlign?.addEventListener('change', () => {
  const align = parkedTextAlign.value || 'center';
  $$('[data-text-align]').forEach(button => {
    const active = align === button.dataset.textAlign;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
});

/* ------------------------------------------------------ Apply to All UI
   app.js owns confirmation and propagation; this layer only reflects status. */
const applyAllBar = $('[data-applyall]');
const applyAllResult = $('[data-bind="apply-all-status"]');
const applyAllCount = $('[data-applyall-count]');

/* Luna's app.js writes the human-readable summary into
   [data-bind="apply-all-status"] and dispatches studio:design-applied-all
   with the structured response; success/error styling keys off that event. */
document.addEventListener('studio:design-applied-all', event => {
  if (!applyAllResult) return;
  applyAllResult.dataset.kind = event.detail?.affectedStrips ? 'success' : 'error';
});

function updateApplyAllScope() {
  if (!applyAllBar || applyAllBar.hidden || !applyAllCount) return;
  const buttons = $$('[data-bind="strip-list"] button[data-strip-id]');
  const activeId = $('[data-bind="strip-list"] button.is-active')?.dataset.stripId;
  const index = buttons.findIndex(button => button.dataset.stripId === activeId);
  const others = Math.max(0, buttons.length - 1);
  applyAllCount.textContent = others > 0
    ? `${others} other strip${others === 1 ? '' : 's'}`
    : 'no other strips';
}

/* --------------------------------------------- Strip indicator (N of M) */
const stripIndicator = $('[data-strip-indicator]');
const stripIndicatorLabel = $('[data-strip-indicator-label]');

function updateStripIndicator() {
  if (!stripIndicator || !stripIndicatorLabel) return;
  const buttons = $$('[data-bind="strip-list"] button[data-strip-id]');
  if (!buttons.length) {
    stripIndicator.hidden = true;
    return;
  }
  const activeId = $('[data-bind="strip-list"] button.is-active')?.dataset.stripId
    ?? window.__studio_state_mirror?.()?.selectedStripId
    ?? buttons[0].dataset.stripId;
  const index = Math.max(0, buttons.findIndex(button => button.dataset.stripId === activeId));
  stripIndicator.hidden = false;
  stripIndicatorLabel.textContent = `Strip ${index + 1} of ${buttons.length}`;
}

/* The functional strip-list buttons carry no active styling of their own;
   presentation marks the strip app.js reports as selected. */
function syncStripSelection() {
  const selectedStripId = window.__studio_state_mirror?.()?.selectedStripId;
  const buttons = $$('[data-bind="strip-list"] button[data-strip-id]');
  const fallback = buttons[0]?.dataset.stripId ?? null;
  const activeId = selectedStripId && buttons.some(button => button.dataset.stripId === selectedStripId)
    ? selectedStripId
    : fallback;
  buttons.forEach(button => {
    button.classList.toggle('is-active', Boolean(activeId) && button.dataset.stripId === activeId);
  });
}

/* Read-only state mirror: captures what app.js publishes on state changes.
   Only defined if app.js has not already exposed a richer one. */
window.__studio_state_mirror = window.__studio_state_mirror ?? (() => {
  const cached = { selectedStripId: null, isDirty: null };
  document.addEventListener('studio:state-change', event => {
    const detail = event.detail ?? {};
    if (detail.selectedStripId !== undefined) cached.selectedStripId = detail.selectedStripId;
    if (typeof detail.project?.isDirty === 'boolean') cached.isDirty = detail.project.isDirty;
  });
  return () => cached;
})();

/* ------------------------------------------------ Presentation batch run */
function applyAllVisibility() {
  if (!applyAllBar) return;
  const stripCount = Number($('[data-bind="strip-count"]')?.textContent || 0);
  applyAllBar.hidden = stripCount <= 1;
}

function runPresentationPass() {
  enhanceImageLibrary();
  syncLibraryMode();
  applyAllVisibility();
  updateInspectorMode();
  updateApplyAllScope();
  syncStripSelection();
  updateStripIndicator();
}

/* ====================================================== Dialog system
   Presentation-only Promise API. app.js supplies continuations; this layer
   serializes requests and returns structured outcomes without native dialogs. */
const dialogRoot = $('[data-dialog-root]');
const dialogNode = $('[data-dialog]');
const dialogTitleNode = $('[data-dialog-title]');
const dialogTextNode = $('[data-dialog-text]');
const dialogDetailNode = $('[data-dialog-detail]');
const dialogIconNode = $('[data-dialog-icon]');
const dialogInputWrap = $('[data-dialog-input-wrap]');
const dialogInputLabel = $('[data-dialog-input-label]');
const dialogInput = $('[data-dialog-input]');
const dialogError = $('[data-dialog-error]');
const dialogButtons = {
  primary: $('[data-dialog-action="primary"]'),
  secondary: $('[data-dialog-action="secondary"]'),
  cancel: $('[data-dialog-action="cancel"]'),
};

const DIALOG_ICONS = {
  confirmation: '#i-info',
  warning: '#i-warn',
  destructive: '#i-warn',
  informational: '#i-info',
  recovery: '#i-file',
};

let dialogState = null;
const dialogQueue = [];

function baseDialogButton(button, label, kind, visible) {
  if (!button) return;
  button.hidden = !visible;
  if (!visible) return;
  button.textContent = label;
  button.className = 'btn';
  if (kind === 'primary') button.classList.add('btn-primary');
  if (kind === 'danger') button.classList.add('btn-danger');
}

function startNextDialog() {
  if (dialogState || !dialogQueue.length) return;
  const request = dialogQueue.shift();
  const { spec, resolve, reject } = request;
  if (!dialogRoot || !dialogNode || !dialogTitleNode || !dialogTextNode || !dialogButtons.primary) {
    reject(new Error('The in-app dialog presentation is unavailable.'));
    queueMicrotask(startNextDialog);
    return;
  }
  try {
    const inputSpec = spec.input || null;
    dialogState = {
      ...spec,
      inputSpec,
      resolve,
      trigger: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    };
    dialogNode.dataset.kind = spec.destructive ? 'destructive' : (spec.kind || 'confirmation');
    /* Decisions that cannot be dismissed (recovery) use the stronger role. */
    const dialogRole = spec.decisionRequired || spec.destructive || spec.kind === 'warning'
      ? 'alertdialog'
      : 'dialog';
    dialogNode.setAttribute('role', dialogRole);
    dialogTitleNode.textContent = String(spec.title || '');
    dialogTextNode.textContent = String(spec.message || '');
    if (dialogIconNode) {
      const icon = DIALOG_ICONS[spec.kind] || '#i-info';
      dialogIconNode.innerHTML = `<svg class="icon"><use href="${icon}"/></svg>`;
    }
    if (dialogDetailNode) {
      const detailText = typeof spec.detail === 'string' ? spec.detail : '';
      /* Recovery specs arrive as "Name · 8 photos · 2 strips"; present the
         project name and meta as separate lines inside the detail block. */
      if (spec.kind === 'recovery' && detailText.includes('·')) {
        const [name, ...meta] = detailText.split('·').map(part => part.trim());
        const nameNode = document.createElement('div');
        nameNode.className = 'dialog-detail-name';
        nameNode.textContent = name || 'Untitled Project';
        const metaRow = document.createElement('div');
        metaRow.className = 'dialog-detail-row';
        const metaText = document.createElement('span');
        metaText.textContent = meta.join(' · ');
        metaRow.append(metaText);
        dialogDetailNode.replaceChildren(nameNode, metaRow);
        dialogDetailNode.hidden = false;
      } else {
        dialogDetailNode.replaceChildren();
        dialogDetailNode.hidden = detailText === '';
        dialogDetailNode.textContent = detailText;
      }
    }
    if (dialogInputWrap && dialogInput && dialogInputLabel) {
      dialogInputWrap.hidden = !inputSpec;
      dialogInput.value = inputSpec ? String(inputSpec.value ?? '') : '';
      dialogInput.type = inputSpec?.type === 'search' ? 'search' : 'text';
      dialogInput.placeholder = String(inputSpec?.placeholder || '');
      dialogInput.maxLength = Number.isInteger(inputSpec?.maxLength) && inputSpec.maxLength > 0 ? inputSpec.maxLength : 524288;
      dialogInput.required = Boolean(inputSpec?.required);
      dialogInputLabel.textContent = String(inputSpec?.label || '');
      dialogInput.setAttribute('aria-describedby', 'studio-dialog-error');
      clearInputInvalidState();
    }
    if (dialogError) { dialogError.hidden = true; dialogError.replaceChildren(); }
    baseDialogButton(dialogButtons.primary, spec.confirmLabel || 'OK', spec.destructive ? 'danger' : 'primary', true);
    baseDialogButton(dialogButtons.secondary, spec.secondaryLabel || '—', 'secondary', Boolean(spec.secondaryLabel));
    baseDialogButton(dialogButtons.cancel, spec.decisionRequired ? '—' : (spec.cancelLabel || 'Cancel'), 'secondary', !spec.decisionRequired && spec.cancelLabel !== '');
    dialogRoot.hidden = false;
    requestAnimationFrame(() => {
      if (inputSpec && dialogInput) {
        dialogInput.focus();
        if (inputSpec.selectOnFocus) dialogInput.select();
      } else dialogButtons.primary?.focus();
    });
  } catch (error) {
    dialogState = null;
    reject(error);
    queueMicrotask(startNextDialog);
  }
}

function settleDialog(action) {
  if (!dialogState) return;
  const current = dialogState;
  let value = null;
  if (action === 'primary' && current.inputSpec) {
    value = dialogInput?.value ?? '';
    if (current.inputSpec.required && !String(value).trim()) {
      if (dialogError) {
        dialogError.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-warn"/></svg>';
        const message = document.createElement('span');
        message.textContent = current.inputSpec.requiredMessage || 'Enter a value to continue.';
        dialogError.append(message);
        dialogError.hidden = false;
      }
      markInputInvalid();
      dialogInput?.focus();
      return;
    }
  }
  dialogState = null;
  dialogRoot.hidden = true;
  clearInputInvalidState();
  if (current.trigger && document.contains(current.trigger)) { try { current.trigger.focus(); } catch {} }
  current.resolve({ confirmed: action === 'primary', value: action === 'primary' && current.inputSpec ? value : null, action });
  queueMicrotask(startNextDialog);
}

function markInputInvalid() {
  if (!dialogInput) return;
  dialogInput.setAttribute('aria-invalid', 'true');
  dialogInput.dataset.invalid = 'true';
}

function clearInputInvalidState() {
  if (!dialogInput) return;
  dialogInput.removeAttribute('aria-invalid');
  delete dialogInput.dataset.invalid;
}

function requestDialog(spec) {
  if (!spec || typeof spec !== 'object') return Promise.reject(new TypeError('A dialog request must be an object.'));
  if (spec.input && (!dialogInputWrap || !dialogInput || !dialogInputLabel)) return Promise.reject(new Error('The in-app text input is unavailable.'));
  return new Promise((resolve, reject) => {
    dialogQueue.push({ spec, resolve, reject });
    startNextDialog();
  });
}

window.__studio_dialog = Object.freeze({ request: requestDialog });

dialogButtons.primary?.addEventListener('click', () => settleDialog('primary'));
dialogButtons.secondary?.addEventListener('click', () => settleDialog('secondary'));
dialogButtons.cancel?.addEventListener('click', () => settleDialog('cancel'));
dialogInput?.addEventListener('input', () => {
  if (dialogError) { dialogError.hidden = true; dialogError.replaceChildren(); }
  clearInputInvalidState();
});
dialogInput?.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.isComposing) {
    event.preventDefault();
    settleDialog('primary');
  }
});
dialogRoot?.addEventListener('click', event => {
  if (event.target === $('[data-dialog-backdrop]', dialogRoot) && dialogState?.decisionRequired !== true) settleDialog('cancel');
});

dialogNode?.addEventListener('keydown', event => {
  if (event.key === 'Escape' && dialogState && dialogState.decisionRequired !== true) {
    event.preventDefault();
    settleDialog('cancel');
  }
});

/* Simple focus trap while a dialog is open. */
document.addEventListener('keydown', event => {
  if (event.key !== 'Tab' || !dialogState) return;
  const focusables = [...dialogNode.querySelectorAll('button, input')].filter(node =>
    !node.hidden && !node.disabled && !node.closest('[hidden]'));
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialogNode.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialogNode.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
});

/* ==================================================== Workflow states
   Derived ONLY from published state: image/strip counts, validation status,
   preview availability. Stages: not-started (number), done (check), attention
   (warn), current (aria-current + rail styles). Never fakes completion. */
const liveRegion = $('[data-live-region]');
let lastAnnouncement = '';

function announce(message) {
  if (!liveRegion || !message || message === lastAnnouncement) return;
  lastAnnouncement = message;
  liveRegion.textContent = `${message}`;
}

const workflowRoots = {
  images: $('[data-rail-state="images"]')?.closest('[data-workflow-step]'),
  designer: $('[data-rail-state="designer"]')?.closest('[data-workflow-step]'),
  print: $('[data-rail-state="print"]')?.closest('[data-workflow-step]'),
  preview: $('[data-rail-state="preview"]')?.closest('[data-workflow-step]'),
};

function imageCount() { return Number($('[data-bind="image-count"]')?.textContent || 0); }
function stripCount() { return Number($('[data-bind="strip-count"]')?.textContent || 0); }
function validationStatus() { return $('[data-bind="validation-status"]')?.textContent.trim() || ''; }
function previewReady() { return Boolean($('[data-bind="pdf-preview"]')?.getAttribute('src')); }

function setRailState(step, state) {
  const item = workflowRoots[step];
  if (!item) return;
  if (state) item.dataset.flow = state;
  else delete item.dataset.flow;
  const icon = $('[data-rail-state="' + step + '"]', item);
  if (icon) {
    if (state === 'done') icon.innerHTML = '<svg class="icon"><use href="#i-check"/></svg>';
    else if (state === 'attention') icon.innerHTML = '<svg class="icon"><use href="#i-warn"/></svg>';
    else icon.innerHTML = '';
  }
}

function updateWorkflowStates() {
  const images = imageCount();
  const strips = stripCount();
  const validation = validationStatus();

  setRailState('images', images > 0 ? 'done' : null);
  setRailState('designer', strips > 0 ? 'done' : null);
  setRailState('print', validation === 'PASS' ? 'done' : validation === 'ERROR' ? 'attention' : null);
  setRailState('preview', previewReady() ? 'done' : null);

  /* Sidebar current page marker */
  const active = document.body.dataset.activeView;
  for (const [step, item] of Object.entries(workflowRoots)) {
    if (!item) continue;
    if (step === active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }
}

/* ------------------------------------------------------ Next-step content */
const nextStepCards = $$('[data-next-step]');

function setNextStep(view, message, buttonLabel, targetView) {
  const card = nextStepCards.find(node => node.dataset.nextStep === view);
  if (!card) return;
  const messageNode = $('[data-next-step-message]', card);
  const buttonNode = $('[data-next-step-button]', card);
  if (messageNode) messageNode.textContent = message;
  if (buttonNode) {
    buttonNode.dataset.targetView = targetView;
    const labelSpan = buttonNode.childNodes[0];
    if (labelSpan) labelSpan.textContent = `${buttonLabel}`;
  }
}

function updateNextStep() {
  const images = imageCount();
  const strips = stripCount();
  const validation = validationStatus();

  setNextStep('photos',
    strips > 0
      ? `${strips} strip${strips === 1 ? '' : 's'} are ready. Customize them in the Designer.`
      : images > 0
        ? `${images} photo${images === 1 ? '' : 's'} imported. Generate a strip to continue.`
        : 'Import photos to get started.',
    strips > 0 ? 'Open Designer' : 'Import Images',
    strips > 0 ? 'designer' : 'images');

  setNextStep('print',
    validation === 'ERROR'
      ? 'Review the fit limit and use a safe count or orientation.'
      : validation === 'WARNING'
        ? 'The layout fits with warnings. Review before export.'
        : validation === 'PASS'
          ? 'The layout fits safely. Review the PDF preview.'
          : 'Validate the layout, then review the PDF preview.',
    'Open PDF Preview', 'preview');
}

/* Click forwarding for contextual next-step buttons */
nextStepCards.forEach(card => {
  $('[data-next-step-button]', card)?.addEventListener('click', event => {
    const target = event.currentTarget.dataset.targetView;
    if (!target) return;
    if (target === 'designer') activateView('designer');
    else if (target === 'preview') activateView('preview');
    else if (target === 'images') {
      activateView('images');
      $('[data-action="import-images"]')?.focus();
    }
  });
});

/* ---------------------------------------------------- Project status chip */
const projectChip = $('[data-project-chip]');

/* The explicit dirty state is published with the project state; a name is not
   used as a proxy for whether the current edits have been saved. */
function updateProjectChip() {
  if (!projectChip) return;
  const saved = window.__studio_state_mirror?.()?.isDirty === false;
  projectChip.dataset.state = saved ? 'saved' : 'unsaved';
  const stateNode = $('[data-project-state]', projectChip);
  if (stateNode) {
    stateNode.textContent = saved ? 'Saved' : 'Unsaved changes';
    projectChip.dataset.tooltip = saved
      ? 'No unsaved project changes.'
      : 'Save the project to keep the latest changes.';
  }
}

/* ==================================================== Hit targets (a11y)
   Keep the compact 26px visual size but provide an effective hit area via a
   transparent ::after extension (no layout shift, no visual enlargement). */
function enlargeHitTargets() {
  $$('.image-item-actions .btn-icon-sm').forEach(button => {
    if (button.dataset.hitWired) return;
    button.dataset.hitWired = '1';
    button.classList.add('hit-expanded');
  });
}

/* ==================================================== Announcements
   Brief outcomes only; the status bar chip stays the quiet mirror. */
document.addEventListener('studio:validation-updated', () => {
  const status = validationStatus();
  if (status) announce(`Print validation ${status}`);
});

document.addEventListener('studio:design-applied-all', event => {
  const detail = event.detail ?? {};
  const affected = Number(detail.affectedStrips);
  if (Number.isFinite(affected)) announce(`Applied to ${affected} strip${affected === 1 ? '' : 's'}`);
});

document.addEventListener('studio:preview-updated', () => announce('PDF preview updated'));

/* ------------------------------------------------ Presentation batch run */
function runPolishPass() {
  updateWorkflowStates();
  updateNextStep();
  updateProjectChip();
  enlargeHitTargets();
}

/* Hook the polish pass into the same mirrors the rest of the shell uses */
document.addEventListener('studio:state-change', () => requestAnimationFrame(runPolishPass));
document.addEventListener('change', () => requestAnimationFrame(runPolishPass));
document.addEventListener('studio:validation-updated', () => requestAnimationFrame(runPolishPass));
document.addEventListener('studio:preview-updated', () => requestAnimationFrame(runPolishPass));

/* --------------------------------------------------------- Initial paint */
requestAnimationFrame(() => {
  renderSheet();
  mirrorPrintSize();
  syncEmptyStates();
  syncLayersFromSelect();
  applyZoom(1);
  applyPreviewMode('fit');
  runPresentationPass();
  runPolishPass();
});

/* ---------------------------------------------- Boot splash & failsafe --- */
function markBooted() {
  document.body.classList.add('is-booted');
}

if (document.readyState === 'complete') {
  markBooted();
} else {
  window.addEventListener('load', markBooted, { once: true });
}

/* Even if load never settles (bridge delay), reveal the UI after 1.5s. */
setTimeout(markBooted, 1500);

/* A 4s absolute failsafe hides the splash node itself if styles load late. */
setTimeout(() => $('#boot-splash')?.setAttribute('data-done', ''), 4000);

/* Listen for functional readiness to remove the splash early. */
document.addEventListener('studio:ready', markBooted);
document.addEventListener('studio:project-changed', markBooted);
