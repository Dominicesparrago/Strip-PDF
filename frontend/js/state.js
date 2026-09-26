const state = { project: null, templates: [], selectedElementId: null, selectedStripId: null, previewPage: 0, preview: null };

export function getState() { return state; }

export function setState(patch, eventName = 'studio:state-change') {
  Object.assign(state, patch);
  document.dispatchEvent(new CustomEvent(eventName, { detail: patch }));
  return state;
}

export function project() { return state.project; }
