import React from 'react';

export interface TimelineTrip {
  trip_id: string;
  vehicle_id: string;
  destination_id: string;
  loading_start_min: number;
  departure_min: number;
  arrival_min: number;
  unloading_end_min: number;
  ready_for_sale_min: number;
  vehicle_release_min: number;
  total_loaded_units: number;
  state: string;
}

export const TimelineView: React.FC<{ trips: TimelineTrip[]; horizonMin?: number }> = ({ trips, horizonMin = 660 }) => {
  return (
    <div className="space-y-2">
      {trips.map((t) => {
        const leftPct = Math.min(95, Math.max(0, (t.loading_start_min / horizonMin) * 100));
        const widthPct = Math.min(100 - leftPct, Math.max(8, ((t.vehicle_release_min - t.loading_start_min) / horizonMin) * 100));
        return (
          <div key={t.trip_id} className="relative h-8 bg-slate-900 rounded-lg border border-slate-800 overflow-hidden">
            <div
              style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
              className="absolute top-1 bottom-1 rounded bg-emerald-600/80 text-white text-[11px] font-mono px-2 flex items-center justify-between"
            >
              <span>{t.vehicle_id} → {t.destination_id} ({t.total_loaded_units}kg)</span>
              <span>[{t.loading_start_min}m..{t.vehicle_release_min}m)</span>
            </div>
          </div>
        );
      })}
    </div>
  );
};
