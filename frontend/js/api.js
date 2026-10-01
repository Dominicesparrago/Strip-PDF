export async function invoke(method, params = {}) {
  const bridge = window.pywebview?.api;
  if (!bridge?.invoke) throw new Error('The desktop bridge is not ready. Launch the app with Python.');
  const response = await bridge.invoke(method, params);
  if (!response || typeof response !== 'object') throw new Error('The backend returned an invalid response.');
  if (!response.ok) throw new Error(response.error || 'The request failed.');
  const data = response.data;
  return response.history && data && typeof data === 'object' && !Array.isArray(data)
    ? { ...data, history: data.history || response.history }
    : data;
}

export async function pollJob(jobId, onProgress = () => {}) {
  for (;;) {
    const job = await invoke('jobs.get', { jobId });
    onProgress(job);
    if (job.status === 'completed') return job.result;
    if (job.status === 'failed' || job.status === 'cancelled') throw new Error(job.error || 'Background job failed.');
    await new Promise(resolve => setTimeout(resolve, 180));
  }
}

export async function invokeAndWait(method, params = {}, onProgress = () => {}) {
  const value = await invoke(method, params);
  return value?.jobId ? pollJob(value.jobId, onProgress) : value;
}
