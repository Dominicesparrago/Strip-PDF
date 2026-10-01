const state = { project: null, templates: [], selectedElementId: null, selectedStripId: null, previewPage: 0, preview: null, history: { canUndo: false, canRedo: false }, canUndo: false, canRedo: false };

export function getState() { return state; }

export function setState(patch, eventName = 'studio:state-change') {
  if (patch.history && typeof patch.history === 'object') {
    patch = { ...patch, canUndo: Boolean(patch.history.canUndo), canRedo: Boolean(patch.history.canRedo) };
  }
  Object.assign(state, patch);
  document.dispatchEvent(new CustomEvent(eventName, { detail: {
    ...patch,
    selectedElementId: state.selectedElementId,
    selectedStripId: state.selectedStripId,
    history: state.history,
    canUndo: state.canUndo,
    canRedo: state.canRedo,
  } }));
  return state;
}

export function project() { return state.project; }
