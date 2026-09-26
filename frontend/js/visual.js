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
  if (name === 'designer') requestAnimationFrame(fitCanvasZoom);
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

/* --------------------------------------------------------- Designer zoom */
const canvas = $('[data-bind="designer-canvas"]');
let zoom = 1;

function applyZoom(value) {
  zoom = Math.min(4, Math.max(0.25, value));
  if (!canvas) return;
  canvas.classList.toggle('is-zoomed', Math.abs(zoom - 1) > 0.001);
  canvas.style.setProperty('--zoom', String(zoom));
  const readout = $('[data-bind="zoom-level"]');
  if (readout) readout.textContent = `${Math.round(zoom * 100)}%`;
}

function fitCanvasZoom() {
  /* The canvas is CSS-fitted to the stage; Fit resets the presentation zoom. */
  applyZoom(1);
}

$('[data-vaction="zoom-in"]')?.addEventListener('click', () => applyZoom(zoom + 0.1));
$('[data-vaction="zoom-out"]')?.addEventListener('click', () => applyZoom(zoom - 0.1));
$('[data-vaction="zoom-fit"]')?.addEventListener('click', fitCanvasZoom);
$('[data-vaction="zoom-100"]')?.addEventListener('click', () => applyZoom(1));

window.addEventListener('resize', () => {
  if (document.body.dataset.activeView === 'designer') fitCanvasZoom();
});

/* ---------------------------------------------------------- Preview zoom */
const previewImage = $('[data-bind="pdf-preview"]');
let previewScale = 'fit';
let previewZoom = 1;

function applyPreviewMode(mode) {
  const requestedZoom = mode === '100' ? 1 : Number(mode);
  previewScale = mode === 'fit' ? 'fit' : String(Math.min(4, Math.max(0.25, requestedZoom || 1)));
  if (previewScale === 'fit') previewZoom = 1;
  else previewZoom = Number(previewScale);
  if (!previewImage) return;
  previewImage.classList.toggle('is-fit', previewScale === 'fit');
  previewImage.classList.toggle('is-100', previewScale !== 'fit');
  if (previewScale !== 'fit') previewImage.style.setProperty('--pz', String(previewZoom));
  const activeAction = previewScale === 'fit' ? 'pz-fit' : previewZoom === 1 ? 'pz-100' : '';
  $$('[data-vaction^="pz-"]').forEach(b => b.classList.toggle('is-active', b.dataset.vaction === activeAction));
}

$('[data-vaction="pz-fit"]')?.addEventListener('click', () => applyPreviewMode('fit'));
$('[data-vaction="pz-100"]')?.addEventListener('click', () => applyPreviewMode('100'));
$('[data-vaction="pz-in"]')?.addEventListener('click', () => applyPreviewMode((previewScale === 'fit' ? 1 : previewZoom) + 0.1));
$('[data-vaction="pz-out"]')?.addEventListener('click', () => applyPreviewMode((previewScale === 'fit' ? 1 : previewZoom) - 0.1));

/* --------------------------------------------------------------- Layers
   Built from the functional [data-bind="design-element-list"] options, which
   app.js re-renders on every design change as "id · type". If Luna later
   exposes window.__studio_design_snapshot ({ elements: [...] }), visibility,
   lock state, and fill swatches are enhanced automatically. */
const layersRoot = $('[data-layers]');
const elementSelect = $('[data-bind="design-element-list"]');

const LAYER_ICONS = {
  background: '#i-layers',
  pattern: '#i-templates',
  image_frame: '#i-image',
  border: '#i-strip',
  decoration: '#i-snap',
};

const LAYER_LABELS = {
  background: 'Background',
  pattern: 'Pattern',
  image_frame: 'Image Frame',
  border: 'Border',
  decoration: 'Decoration',
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

function runFunctionalAction(action) {
  document.querySelector(`[data-action="${action}"]`)?.click();
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
  const selectedId = elementSelect?.value;
  if (selectedId) {
    const option = [...(elementSelect?.options ?? [])].find(item => item.value === selectedId);
    if (option) {
      const { id, type } = parseOptionLabel(option.textContent);
      const target = $('[data-inspector-target]');
      if (target) target.textContent = niceName(id, type);
    }
  }
}

document.addEventListener('studio:state-change', () => {
  requestAnimationFrame(syncLayersFromSelect);
  setTimeout(syncLayersFromSelect, 50);
});

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
  const status = $('[data-bind="validation-status"]');
  const fit = $('[data-fit]');
  if (!fit) return;
  const value = status.textContent.trim();
  fit.dataset.status = value || 'PASS';
  const icon = $('[data-fit-icon]');
  if (icon) icon.setAttribute('href', value === 'ERROR' ? '#i-error' : value === 'WARNING' ? '#i-warn' : '#i-check');
  const title = $('[data-fit-title]');
  if (title) {
    title.textContent = value === 'ERROR'
      ? 'Does not fit'
      : value === 'WARNING'
        ? 'Fits with warnings'
        : 'Fits safely';
  }
}

function mirrorPrintSize() {
  const width = $('[data-bind="strip-width-mm"]')?.value;
  const height = $('[data-bind="strip-height-mm"]')?.value;
  if (!width || !height) return;
  $$('[data-print-size-value]').forEach(node => {
    node.textContent = `${Number(width).toFixed(1)} × ${Number(height).toFixed(1)} mm`;
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
  const stripsPerPage = Number($('[data-bind="strips-per-page"]')?.value || 0);
  const margin = Number($('[data-bind="page-margin-mm"]')?.value || 0);
  const gap = Number($('[data-bind="strip-gap-mm"]')?.value || 0);
  const width = Number($('[data-bind="strip-width-mm"]')?.value || 54);
  const height = Number($('[data-bind="strip-height-mm"]')?.value || 72);
  if (!stripsPerPage || stripsPerPage <= 0) return;
  const key = [stripsPerPage, margin, gap, width, height, $('[data-bind="cut-guides"]')?.checked].join('|');
  if (key === lastSheetKey) return;
  lastSheetKey = key;

  const pageW = 297;
  const pageH = 210;
  const innerW = pageW - 2 * margin;
  const innerH = pageH - 2 * margin;
  sheetStrips.replaceChildren();
  const marginNode = $('[data-sheet-margin]');
  if (marginNode) marginNode.style.inset = `${(margin / pageH) * 100}% ${(margin / pageW) * 100}%`;

  const fitting = Math.floor((innerW + gap) / (width + gap));
  const shown = Math.min(stripsPerPage, Math.max(fitting, 0));
  const totalW = shown * width + Math.max(0, shown - 1) * gap;
  const startX = (pageW - totalW) / 2;
  const startY = margin + (innerH - height) / 2;

  for (let index = 0; index < shown; index += 1) {
    const strip = document.createElement('div');
    strip.className = 'sheet-strip';
    strip.style.left = `${((startX + index * (width + gap)) / pageW) * 100}%`;
    strip.style.top = `${(startY / pageH) * 100}%`;
    strip.style.width = `${(width / pageW) * 100}%`;
    strip.style.height = `${(height / pageH) * 100}%`;
    for (let slot = 0; slot < 4; slot += 1) {
      strip.append(Object.assign(document.createElement('span'), { className: 'sheet-strip-inner' }));
    }
    sheetStrips.append(strip);
  }

  const meta = $('[data-sheet-meta]');
  if (meta) {
    meta.textContent = `${stripsPerPage} strips · ${width.toFixed(1)} × ${height.toFixed(1)} mm`;
    meta.style.color = shown < stripsPerPage ? 'var(--warn-ink)' : '';
  }
  $('[data-sheet]')?.classList.toggle('is-overflow', shown < stripsPerPage);
}

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

/* --------------------------------------------------------- Initial paint */
requestAnimationFrame(() => {
  renderSheet();
  mirrorPrintSize();
  syncEmptyStates();
  syncLayersFromSelect();
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
