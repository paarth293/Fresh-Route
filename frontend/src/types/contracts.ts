export type BatchStatus = 'available' | 'quarantined' | 'committed' | 'depleted';
export type PolicyName = 'organizer_baseline' | 'fefo_greedy' | 'nominal_mip' | 'scenario_selected' | 'inspection_then_scenario_selected';
export type CommitmentState = 'proposed' | 'reserved' | 'loading' | 'dispatched' | 'delivered' | 'closed';

export interface Batch {
  id: string;
  commodity_id: string;
  quantity_units: number;
  observed_at_min: number;
  remaining_life_nominal_min: number;
  remaining_life_low_min?: number;
  remaining_life_high_min?: number;
  status: BatchStatus;
  inspection_cost_paise: number;
  inspection_duration_min: number;
  inspectable: boolean;
}

export interface EconomicBreakdown {
  is_forecast: boolean;
  gross_revenue_paise: number;
  fixed_trip_cost_paise: number;
  variable_transport_cost_paise: number;
  handling_cost_paise: number;
  spoilage_disposal_cost_paise: number;
  inspection_cost_paise: number;
  cancellation_cost_paise: number;
  sunk_committed_cost_paise: number;
  net_outcome_paise: number;
}
