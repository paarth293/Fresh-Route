
export interface AppWorkspaceState {
  activeTab: 'overview' | 'inventory' | 'dispatch' | 'disruption' | 'inspection' | 'evaluation' | 'studio';
  selectedScenarioId: string;
  selectedPolicy: string;
  stateRevision: number;
  planRevision: number;
  scenarioVersionId: string;
  decisionAtMin: number;
  isPlanStale: boolean;
  connectionMode: 'live_api' | 'offline_deterministic';
  selectedBatchForReceipt: string | null;
}

const STORAGE_KEY = 'freshroute_lastwindow_persisted_state_v2';

export const initialWorkspaceState: AppWorkspaceState = {
  activeTab: 'overview',
  selectedScenarioId: 'demo-normal-001',
  selectedPolicy: 'scenario_selected',
  stateRevision: 1,
  planRevision: 1,
  scenarioVersionId: 'sv_demo-normal-001_v1',
  decisionAtMin: 0,
  isPlanStale: false,
  connectionMode: 'offline_deterministic',
  selectedBatchForReceipt: 'B-TOM-01',
};

export function savePersistedWorkspaceState(state: Record<string, unknown>): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    }
  } catch {
    // Ignore storage quota errors in restricted sandboxes
  }
}

export function loadPersistedWorkspaceState(): Record<string, unknown> | null {
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    }
  } catch {
    return null;
  }
  return null;
}

export function clearPersistedWorkspaceState(): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Ignore
  }
}
