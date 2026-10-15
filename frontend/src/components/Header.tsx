import React from 'react';
import { AppWorkspaceState } from '../state/store';

interface HeaderProps {
  state: AppWorkspaceState;
  onSelectScenario: (scenarioId: string) => void;
  onSelectPolicy: (policy: string) => void;
  onSelectTab: (tab: AppWorkspaceState['activeTab']) => void;
  onOpenImport: () => void;
  onOpenExport: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  state,
  onSelectScenario,
  onSelectPolicy,
  onSelectTab,
  onOpenImport,
  onOpenExport,
}) => {
  const tabs: Array<{ id: AppWorkspaceState['activeTab']; label: string }> = [
    { id: 'overview', label: '1. Recovery Overview' },
    { id: 'inventory', label: '2. Inventory & Receipts' },
    { id: 'dispatch', label: '3. Dispatch & Failure Boundary' },
    { id: 'disruption', label: '4. Disruption & Interventions' },
    { id: 'inspection', label: '5. Inspection Queue (VoI)' },
    { id: 'evaluation', label: '6. Evaluation & Benchmarks' },
    { id: 'studio', label: '★ 7. Judge Studio & Briefing' },
  ];

  return (
    <header className="bg-slate-900 text-white border-b border-slate-800 sticky top-0 z-30">
      <div className="max-w-7xl mx-auto px-4 py-2.5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500 flex items-center justify-center font-extrabold text-slate-950">
            FR
          </div>
          <div>
            <h1 className="font-extrabold text-base sm:text-lg">FreshRoute: LastWindow Recovery Workspace</h1>
            <p className="text-xs text-slate-400">
              Mode: {state.connectionMode === 'live_api' ? 'LIVE BACKEND API (/api/v1)' : 'OFFLINE DETERMINISTIC SOLVER'}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select
            value={state.selectedScenarioId}
            onChange={(e) => onSelectScenario(e.target.value)}
            className="bg-slate-800 text-white rounded px-2 py-1 border border-slate-700"
          >
            <option value="demo-normal-001">demo-normal-001 (Morning Balanced)</option>
            <option value="demo-shortage-002">demo-shortage-002 (Tight Supply)</option>
            <option value="demo-delay-003">demo-delay-003 (Monsoon Delay +25m)</option>
          </select>
          <select
            value={state.selectedPolicy}
            onChange={(e) => onSelectPolicy(e.target.value)}
            className="bg-slate-800 text-emerald-300 rounded px-2 py-1 border border-slate-700"
          >
            <option value="scenario_selected">scenario_selected (Uncertainty-Aware)</option>
            <option value="nominal_mip">nominal_mip (Two-Stage SCIP MIP)</option>
            <option value="fefo_greedy">fefo_greedy (FEFO Heuristic)</option>
            <option value="organizer_baseline">organizer_baseline (FIFO Baseline)</option>
          </select>
          <span className="px-2.5 py-1 rounded bg-slate-800 font-mono text-amber-300">
            t={state.decisionAtMin}m | Rev #{state.stateRevision} ({state.scenarioVersionId})
          </span>
          <button onClick={onOpenImport} className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700">
            Import / Validate JSON
          </button>
          <button onClick={onOpenExport} className="px-2.5 py-1 rounded bg-emerald-600 text-white font-semibold">
            Export Plan JSON
          </button>
        </div>
      </div>
      <nav className="bg-slate-950 border-t border-slate-800 px-4 py-1.5 flex gap-1.5 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => onSelectTab(t.id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap ${
              state.activeTab === t.id ? 'bg-emerald-600 text-white' : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
    </header>
  );
};
