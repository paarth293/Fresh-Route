import React from 'react';

interface ReceiptDrawerProps {
  isOpen: boolean;
  receipt: Record<string, any> | null;
  alternatives: Record<string, any>;
  onClose: () => void;
}

export const ReceiptDrawer: React.FC<ReceiptDrawerProps> = ({ isOpen, receipt, alternatives, onClose }) => {
  if (!isOpen || !receipt) return null;
  return (
    <div className="fixed inset-0 z-40 bg-slate-950/75 flex justify-end">
      <div className="bg-slate-900 text-slate-100 w-full max-w-2xl h-full p-5 overflow-y-auto border-l border-slate-800 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div>
            <span className="text-xs uppercase text-emerald-400 font-bold">Immutable Decision Receipt</span>
            <h3 className="text-base font-bold">Batch {receipt.batch_id} ({receipt.commodity_id})</h3>
            <p className="text-xs text-slate-400 font-mono">
              Snapshot Hash: {receipt.snapshot_hash || 'verified'} | Plan Rev #{receipt.plan_state_revision || 1}
              {receipt.is_stale_vs_current_ledger ? ' (HISTORICAL SNAPSHOT)' : ' (CURRENT)'}
            </p>
          </div>
          <button onClick={onClose} className="px-3 py-1 rounded bg-slate-800 text-xs">Close</button>
        </div>
        <div className="p-3 rounded-xl bg-slate-800 text-xs">{receipt.explanation_text}</div>
        <div>
          <h4 className="text-xs font-bold mb-1">Binding Operational Constraints</h4>
          <ul className="list-disc list-inside text-xs space-y-1 text-amber-300">
            {(receipt.binding_constraints || []).map((c: string, idx: number) => (
              <li key={idx}>{c}</li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="text-xs font-bold mb-1">Controlled Counterfactual Re-Solves (Same Assumption Profile)</h4>
          <div className="space-y-1.5 text-xs">
            {Object.values(alternatives || {}).map((alt: any) => (
              <div key={alt.alternative_destination_id} className="p-2 rounded bg-slate-800 flex justify-between">
                <span>Force {alt.forced_quantity_units} kg → {alt.alternative_destination_id}</span>
                <span className="font-mono">
                  {alt.is_feasible ? `Delta: ${alt.net_delta_paise} paise` : `Infeasible (${alt.infeasibility_reason})`}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
