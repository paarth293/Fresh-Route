export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function requestJson(path: string, body?: unknown, asyncJob = false): Promise<any> {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(asyncJob ? { Prefer: 'respond-async' } : {}) }, body: JSON.stringify(body),
  });
  let data;
  try { data = await response.json(); }
  catch { throw new ApiError(response.status, 'Invalid response. Check that the backend is running.'); }
  if (!response.ok) {
    const details = data.error?.details?.validation_errors?.map((e: any) => `${e.field_path}: ${e.message}`).join('; ');
    throw new ApiError(response.status, details || data.error?.message || data.detail || `Request failed (${response.status}).`);
  }
  return data;
}
export const checkBackendHealth = () => requestJson('/health');
export const importScenarioApi = (data: unknown) => requestJson('/api/v1/scenarios', data);
export const fetchScenarioState = (id: string) => requestJson(`/api/v1/scenarios/${encodeURIComponent(id)}/state`);
export async function runJob(kind: string, payload: unknown) {
  let job = await requestJson(`/api/v1/${kind}-jobs`, payload, true);
  const notify = () => { if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('freshroute:job', { detail: job })); };
  notify();
  const deadline = Date.now() + 300_000;
  while (job.status === 'queued' || job.status === 'running') {
    if (Date.now() >= deadline) throw new Error(`Job ${job.job_id} is still running. Check status before submitting again.`);
    await new Promise(resolve => setTimeout(resolve, 500));
    job = await requestJson(`/api/v1/jobs/${encodeURIComponent(job.job_id)}`);
    notify();
  }
  if (job.status !== 'succeeded' || !job.result_id) throw new Error(job.error_message || `Job ended with status: ${job.status}`);
  return requestJson(`/api/v1/${kind === 'planning' ? 'plans' : 'reports'}/${encodeURIComponent(job.result_id)}`);
}
export const cancelJob = (id: string) => requestJson(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`, {});
export const submitPlanningJob = (payload: unknown) => runJob('planning', payload);
export const fetchBatchReceiptApi = (plan: string, batch: string) => requestJson(`/api/v1/plans/${encodeURIComponent(plan)}/receipts/${encodeURIComponent(batch)}`);
export const commitPlanRequest = (plan: string, revision: number, key: string) => requestJson(`/api/v1/plans/${encodeURIComponent(plan)}/commit`, { expected_revision: revision, idempotency_key: key, commit_state: 'reserved' });
export const postOperationalEventApi = (id: string, payload: unknown) => requestJson(`/api/v1/scenarios/${encodeURIComponent(id)}/events`, payload);
export const fetchRecoveryOptionsApi = (id: string, plan?: string) => requestJson(`/api/v1/scenarios/${encodeURIComponent(id)}/recovery-options`, { prior_plan_id: plan, planning_seed: 1701 });
export const fetchPlanFragilityApi = (id: string) => requestJson(`/api/v1/plans/${encodeURIComponent(id)}/fragility`);
export const executeInspectionApi = (id: string, batch: string, key: string) => requestJson(`/api/v1/scenarios/${encodeURIComponent(id)}/inspections`, { batch_id: batch, idempotency_key: key, world_seed: 1701 });
