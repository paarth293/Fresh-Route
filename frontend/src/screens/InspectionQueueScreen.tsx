import React from 'react';

export interface InspectionQueueScreenProps {
  scenario: Record<string, any>;
  plan: Record<string, any>;
  replay?: Record<string, any>;
  fragility?: Record<string, any>;
  recoveryComparison?: Record<string, any>;
  onAction?: (actionType: string, payload?: any) => void;
}

export const InspectionQueueScreen: React.FC<InspectionQueueScreenProps> = ({ scenario, plan, fragility, recoveryComparison, onAction }) => {
  return (
    <section className="p-4 rounded-2xl bg-slate-900 border border-slate-800 space-y-3 text-xs text-slate-200">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-white">Bayesian Value-of-Information (VoI) Inspection Prioritization</h2>
        <span className="font-mono text-emerald-400">
          Scenario: {scenario?.scenario_id} | Plan: {plan?.plan_id} (Rev #{plan?.state_revision})
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
        <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
          <div className="text-slate-400">1. Stock At Risk</div>
          <div className="text-sm font-bold font-mono text-amber-300">
            {Object.values(plan?.unallocated_by_batch || {}).reduce((a: number, b: any) => a + Number(b), 0)} kg unallocated
          </div>
        </div>
        <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
          <div className="text-slate-400">2. When Plan Stops Working</div>
          <div className="text-sm font-bold font-mono text-emerald-300">
            Breakpoint: +{fragility?.min_breakpoint_delay_min ?? 0}m ({fragility?.most_fragile_trip_id || 'none'})
          </div>
        </div>
        <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
          <div className="text-slate-400">3. Recommended Intervention</div>
          <div className="text-sm font-bold font-mono text-indigo-300">
            {recoveryComparison?.recommended_option_id || 'retain_current_plan'}
          </div>
        </div>
        <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
          <div className="text-slate-400">4. Forecast Net Outcome</div>
          <div className="text-sm font-bold font-mono text-white">
            {plan?.forecast_economics?.net_outcome_paise ?? 0} paise
          </div>
        </div>
      </div>
      {onAction && (
        <div className="flex gap-2 pt-2">
          <button onClick={() => onAction('replan')} className="px-3 py-1.5 rounded bg-emerald-600 text-white font-semibold">
            Solve / Replan via Engine
          </button>
          <button onClick={() => onAction('commit')} className="px-3 py-1.5 rounded bg-indigo-600 text-white font-semibold">
            Commit Active Plan
          </button>
        </div>
      )}
    </section>
  );
};
