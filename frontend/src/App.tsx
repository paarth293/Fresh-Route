import React, { useEffect, useRef, useState } from 'react';
import * as api from './api/client';
import { money, stalePlan } from './format';
type Data = Record<string, any>;
const tabs = ['Overview', 'Inventory', 'Dispatch', 'Disruptions', 'Inspections', 'Evaluation'] as const;
const navIcons = ['grid', 'box', 'truck', 'pulse', 'scan', 'chart'];
const pageCopy = {
  Overview: ['A fresher outlook.', 'The right produce. The right destination. Before the window closes.'],
  Inventory: ['Know what’s in reach.', 'A clear view of your stock, shelf life, and the reasoning behind every allocation.'],
  Dispatch: ['Every route, considered.', 'Turn a validated plan into well-timed deliveries.'],
  Disruptions: ['Keep the day moving.', 'Respond to changing conditions while protecting the commitments already made.'],
  Inspections: ['Look closer. Decide better.', 'Find the observations that could make a meaningful difference.'],
  Evaluation: ['See beyond the forecast.', 'Replay decisions and compare policies under shared simulated conditions.'],
};

export default function App() {
  const [samples, setSamples] = useState<Data[]>([]);
  const [selected, setSelected] = useState('demo-normal-001');
  const [policy, setPolicy] = useState('nominal_mip');
  const [tab, setTab] = useState<typeof tabs[number]>('Overview');
  const [snapshot, setSnapshot] = useState<Data | null>(null);
  const [plan, setPlan] = useState<Data | null>(null);
  const [receipt, setReceipt] = useState<Data | null>(null);
  const [replay, setReplay] = useState<Data | null>(null);
  const [rankings, setRankings] = useState<Data[] | null>(null);
  const [fragility, setFragility] = useState<Data | null>(null);
  const [recovery, setRecovery] = useState<Data | null>(null);
  const [benchmark, setBenchmark] = useState<Data | null>(null);
  const [busy, setBusy] = useState('');
  const [activeJob, setActiveJob] = useState<Data | null>(null);
  const [auth, setAuth] = useState({ enabled: false, authenticated: true });
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [connected, setConnected] = useState(false);
  const [routeId, setRouteId] = useState('');
  const [delay, setDelay] = useState(20);
  const [inventoryQuery, setInventoryQuery] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const actionLock = useRef(false);
  const scenario = snapshot?.scenario;
  const stale = stalePlan(plan, snapshot);

  async function action(label: string, callback: () => Promise<void>) {
    if (actionLock.current) return;
    actionLock.current = true; setBusy(label); setError(''); setNotice('');
    try { await callback(); } catch (e) {
      if (e instanceof api.ApiError && e.status === 401) setAuth({ enabled: true, authenticated: false });
      setError(e instanceof Error ? e.message : String(e));
    }
    finally { actionLock.current = false; setBusy(''); setActiveJob(null); }
  }
  function clearResults() {
    setPlan(null); setReceipt(null); setReplay(null); setRankings(null);
    setFragility(null); setRecovery(null); setBenchmark(null);
  }
  async function refresh(id: string) {
    const next = await api.fetchScenarioState(id);
    setSnapshot(next); setSelected(id); setRouteId(next.scenario.routes[0]?.id || '');
    return next;
  }
  async function load(id: string, available = samples) {
    try { await refresh(id); } catch (e) {
      if (!(e instanceof api.ApiError) || e.status !== 404) throw e;
      const sample = available.find(s => s.scenario_id === id);
      if (!sample) throw new Error('Import this scenario to load it.');
      await api.importScenarioApi(sample); await refresh(id);
    }
    clearResults();
  }
  useEffect(() => {
    const onJob = (event: Event) => setActiveJob((event as CustomEvent).detail);
    window.addEventListener('freshroute:job', onJob);
    return () => window.removeEventListener('freshroute:job', onJob);
  }, []);
  useEffect(() => {
    void action('Connecting to the planning service', async () => {
      const session = await api.requestJson('/api/v1/auth/status'); setAuth(session);
      if (!session.authenticated) return;
      await api.checkBackendHealth(); setConnected(true);
      const data = await api.requestJson('/api/v1/demo-scenarios');
      setSamples(data); await load('demo-normal-001', data);
    });
  }, []);
  async function solve() {
    await action('Generating and validating the plan', async () => {
      const current = await refresh(selected);
      const result = await api.submitPlanningJob({ scenario_id: selected, state_revision: current.state_revision, policy, config_id: 'balanced_v1', planning_seed: 1701 });
      setPlan(result); setReplay(null); setReceipt(null); setFragility(null); setRecovery(null);
      setNotice(result.validation.is_valid ? 'Plan generated and independently validated.' : 'Plan failed validation. It cannot be committed.');
    });
  }
  async function importFile(file?: File) {
    if (!file) return;
    await action('Validating scenario import', async () => {
      if (file.size > 5_242_880) throw new Error('Scenario files must be smaller than 5 MB.');
      const data = JSON.parse(await file.text()); const meta = await api.importScenarioApi(data);
      setSamples(prev => [...prev.filter(s => s.scenario_id !== data.scenario_id), data]);
      await refresh(meta.scenario_id); clearResults(); setNotice('Scenario imported as a new version.');
    });
  }
  async function exportPlan() {
    if (!plan) return;
    await action('Exporting plan', async () => {
      const data = await api.requestJson(`/api/v1/plans/${encodeURIComponent(plan.plan_id)}/export`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = `${plan.plan_id}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  if (auth.enabled && !auth.authenticated) return <div className="login-shell"><div className="login-story"><Brand /><div><span className="eyebrow">GOOD PRODUCE. BETTER DECISIONS.</span><h1>Freshness has<br />a window.<br /><em>Make it count.</em></h1><p>Your workspace for thoughtful dispatch decisions, less uncertainty, and produce that reaches its next destination.</p></div><span className="login-foot">FRESHROUTE / LASTWINDOW</span></div><main className="login"><span className="eyebrow">WELCOME BACK</span><h2>Sign in to the workspace</h2><p className="muted">A better day of distribution starts here.</p><form onSubmit={e => { e.preventDefault(); void action('Signing in', async () => {
    await api.requestJson('/api/v1/auth/login', { password }); setPassword(''); window.location.reload();
  }); }}><label>Workspace password<input type="password" autoComplete="current-password" placeholder="Enter your workspace password" value={password} onChange={e => setPassword(e.target.value)} required /></label><button className="primary" disabled={!!busy}>Sign in <Icon name="arrow" /></button></form>{error && <p className="login-error" role="alert">{error}</p>}<p className="login-note"><Icon name="shield" /> Protected access to your shared operations workspace.</p></main></div>;
  return <div className="workspace">
    <aside className="sidebar"><Brand /><span className="nav-label">WORKSPACE</span><nav aria-label="Workspace">{tabs.map((t, i) => <button key={t} aria-label={t} aria-current={tab === t ? 'page' : undefined} onClick={() => setTab(t)}><Icon name={navIcons[i]} /><span>{t}</span>{t === 'Inventory' && scenario && <small>{scenario.batches.length}</small>}{t === 'Dispatch' && plan && <small>{plan.trips.length}</small>}</button>)}</nav><div className="sidebar-bottom"><div className="sidebar-note"><Icon name="leaf" /><p>Every batch.<br /><strong>A better destination.</strong></p><span>Decisions before deadlines.</span></div><div className="workspace-identity"><span className="avatar">FR</span><div><strong>Regional operations</strong><small>Distribution workspace</small></div><Icon name="chevron" /></div></div></aside>
    <div className="app-content"><header className="topbar"><div className="breadcrumb">Workspace <Icon name="chevron" /><strong>{tab}</strong></div><div className="topbar-meta"><span className={`connection ${connected ? 'ready' : ''}`}><i />{connected ? 'Planning service online' : 'Connecting to service'}</span><span className="demo-label">DEMO WORKSPACE</span>{auth.enabled && <button className="text-button" disabled={!!busy} onClick={() => void action('Signing out', async () => { await api.requestJson('/api/v1/auth/logout', {}); clearResults(); setSnapshot(null); setAuth({ enabled: true, authenticated: false }); })}>Sign out</button>}</div></header>
    <main>
    <div className="page-heading"><div><span className="eyebrow">{tab === 'Overview' ? 'YOUR OPERATIONS, IN FOCUS' : `LASTWINDOW / ${tab.toUpperCase()}`}</span><h1>{pageCopy[tab][0]}</h1><p>{pageCopy[tab][1]}</p></div><div className="cycle-stamp"><Icon name="sun" /><div><span>Planning cycle</span><strong>{scenario ? clock(scenario, snapshot!.decision_at_min) : '—'} <small>local time</small></strong></div></div></div>
    <div className="toolbar">
      <label>Scenario<select aria-label="Scenario" value={selected} disabled={!!busy} onChange={e => void action('Loading scenario', () => load(e.target.value))}>{samples.map(s => <option key={s.scenario_id} value={s.scenario_id}>{s.name}</option>)}</select></label>
      <label>Planning policy<select aria-label="Planning policy" value={policy} disabled={!!busy} onChange={e => setPolicy(e.target.value)}><option value="nominal_mip">Nominal optimizer</option><option value="scenario_selected">Uncertainty-aware selection</option><option value="fefo_greedy">FEFO baseline</option><option value="organizer_baseline">Illustrative FIFO baseline</option></select></label>
      <div className="file-actions"><button disabled={!!busy} onClick={() => input.current?.click()}><Icon name="upload" />Import JSON</button><button disabled={!!busy || !plan} onClick={() => void exportPlan()}><Icon name="download" />Export plan</button></div>
      <input ref={input} type="file" accept=".json,application/json" aria-label="Import scenario file" hidden onChange={e => { void importFile(e.target.files?.[0]); e.target.value = ''; }} />
    </div>
      {busy && <div className="banner" role="status"><span className="spinner" />{busy}… {activeJob && ['queued', 'running'].includes(activeJob.status) && <button onClick={() => void api.cancelJob(activeJob.job_id).catch(e => setError(String(e.message)))}>Cancel operation</button>}</div>}{error && <div className="banner error" role="alert"><Icon name="alert" />{error}</div>}{notice && <div className="banner success" role="status"><Icon name="check" />{notice}</div>}
      {!connected && !busy && <button onClick={() => window.location.reload()}>Retry connection</button>}
      {snapshot && <>
        <div className="plan-bar"><div className="context"><span className="context-icon"><Icon name="route" /></span><div><strong>{scenario.name}</strong><span>{plan ? stale ? 'State updated · Generate a fresh plan before committing' : 'Plan ready for review' : 'Scenario loaded · Ready when you are'}<b>·</b> {scenario.vehicles.length} vehicles <b>·</b> {scenario.destinations.length} destinations</span></div></div><div className="plan-actions"><button disabled={!!busy || stale || !plan?.validation?.is_valid || !plan?.trips?.length} onClick={() => void action('Committing dispatch reservations', async () => { await api.commitPlanRequest(plan!.plan_id, snapshot.state_revision, `commit_${plan!.plan_id}`); await refresh(selected); setNotice('Trips reserved. Generate a new plan for remaining stock.'); })}>Commit plan</button><button className="primary" disabled={!!busy} onClick={() => void solve()}>Generate plan <Icon name="arrow" /></button></div></div>
        {plan?.validation?.warnings?.map((w: any, i: number) => <div className="banner error" role="alert" key={i}>{w.message}</div>)}
        {plan && !plan.validation.is_valid && <JsonDetails title="Plan validation errors" data={plan.validation.violations} />}
        <div className={tab === 'Overview' ? 'overview-content' : 'work-panel'}>{tab === 'Overview' && <>
          <div className="cards"><Metric title="Available at origin" value={`${Object.values(snapshot.warehouse_stock_by_batch).reduce((sum: number, n: any) => sum + Number(n), 0)} kg`} icon="box" hint={`${scenario.batches.length} batches in this scenario`} /><Metric title="Forecast net outcome" value={plan ? money(plan.forecast_economics.net_outcome_paise) : '—'} icon="trend" hint="Under current planning assumptions" accent /><Metric title="Planned trips" value={plan ? String(plan.trips.length).padStart(2, '0') : '—'} icon="truck" hint={plan ? `${plan.allocations.reduce((s: number, a: any) => s + a.quantity_units, 0)} kg allocated for dispatch` : 'Generate a plan to allocate stock'} /><Metric title="Independent validation" value={plan ? (plan.validation.is_valid ? 'Passed' : 'Review needed') : 'Awaiting plan'} icon="shield" hint={plan ? 'Checked against operational constraints' : 'Every plan is checked before commitment'} /></div>
          <div className="overview-grid"><Network scenario={scenario} plan={plan} /><section className="panel freshness-panel"><div className="panel-heading"><div><span className="eyebrow">FRESHNESS WATCH</span><h2>The clock is ticking.</h2></div><span className="round-icon"><Icon name="clock" /></span></div><p className="panel-description">Your shortest remaining shelf lives, first.</p><div className="freshness-list">{[...scenario.batches].filter((b: any) => snapshot.warehouse_stock_by_batch[b.id] > 0).sort((a: any, b: any) => a.observed_at_min + a.remaining_life_nominal_min - b.observed_at_min - b.remaining_life_nominal_min).slice(0, 4).map((b: any, i: number) => {
            const remaining = Math.max(0, b.observed_at_min + b.remaining_life_nominal_min - snapshot.decision_at_min);
            return <div className="freshness-item" key={b.id}><span className={`produce-icon produce-${i % 3}`}><Icon name="leaf" /></span><div className="freshness-body"><div><strong>{scenario.commodities.find((c: any) => c.id === b.commodity_id)?.name || b.commodity_id}</strong><span className={remaining < 180 ? 'life-short' : ''}>{duration(remaining)}</span></div><small>{b.id} <b>·</b> {snapshot.warehouse_stock_by_batch[b.id]} kg available</small><div className="life-track"><i style={{ width: `${Math.min(100, remaining / Math.max(1, scenario.evaluation_end_min - snapshot.decision_at_min) * 100)}%` }} /></div></div></div>;
          })}{Object.values(snapshot.warehouse_stock_by_batch).every(n => Number(n) === 0) && <div className="empty-small"><Icon name="check" /><p>All available stock is committed.<br />Review your dispatch schedule.</p></div>}</div><button className="panel-link" onClick={() => setTab('Inventory')}>View inventory <Icon name="arrow" /></button></section></div>
          <div className="overview-bottom"><section className="panel decision-panel"><div className="panel-heading"><div><span className="eyebrow">YOUR NEXT MOVE</span><h2>{!plan ? 'From produce to possibility.' : stale ? 'Ready for the next decision.' : 'A plan with a purpose.'}</h2></div><span className="round-icon"><Icon name="route" /></span></div><p>{!plan ? 'Generate a plan to match available stock with destinations, prices, and delivery windows.' : stale ? 'Your operating state has changed. Generate a fresh plan using the remaining stock and capacity.' : 'Review the proposed routes, understand each allocation, then reserve the trips you want to make.'}</p><div className="decision-steps"><span className="step done"><i><Icon name="check" /></i>Scenario loaded</span><span className={`step ${plan ? 'done' : ''}`}><i>{plan ? <Icon name="check" /> : '2'}</i>Plan evaluated</span><span className={`step ${scenario.commitments.length ? 'done' : ''}`}><i>{scenario.commitments.length ? <Icon name="check" /> : '3'}</i>Trips reserved</span></div><button className="panel-link" onClick={() => setTab(plan ? 'Dispatch' : 'Inventory')}>{plan ? 'Review dispatch schedule' : 'Explore your inventory'} <Icon name="arrow" /></button></section><section className="panel economics-panel"><div className="panel-heading"><div><span className="eyebrow">THE ECONOMIC PICTURE</span><h2>Every decision adds up.</h2></div><Icon name="chart" /></div>{plan ? <Economics data={plan.forecast_economics} /> : <div className="economics-placeholder"><Icon name="chart" /><p>Your forecast takes shape here.</p><span>Revenue, costs, and net contribution appear once a plan is generated.</span></div>}</section></div>
          <div className="insight-strip"><span className="round-icon"><Icon name="pulse" /></span><div><strong>How much room does this plan have?</strong><p>Explore how delays could affect the proposed dispatch windows.</p></div><button disabled={!!busy || !plan} onClick={() => void action('Checking delay sensitivity', async () => setFragility(await api.fetchPlanFragilityApi(plan!.plan_id)))}>Analyze fragility <Icon name="arrow" /></button></div>{fragility && <JsonDetails title="Computed fragility evidence" data={fragility} />}
          <p className="assumption-note"><Icon name="info" />Forecasts use supplied life and demand assumptions. Unallocated stock is not automatically spoiled; replay shows simulated outcomes.</p>
        </>}
        {tab === 'Inventory' && <><div className="section-heading"><div><span className="eyebrow">BATCH REGISTER</span><h2>Inventory and decision receipts</h2></div><label className="search-field"><Icon name="search" /><input aria-label="Search batches" placeholder="Search batch or commodity…" value={inventoryQuery} onChange={e => setInventoryQuery(e.target.value)} /></label></div><Table headings={['Batch', 'Commodity', 'Available', 'Assumed life remaining', 'Status', 'Evidence']} rows={scenario.batches.filter((b: any) => `${b.id} ${b.commodity_id} ${scenario.commodities.find((c: any) => c.id === b.commodity_id)?.name}`.toLowerCase().includes(inventoryQuery.toLowerCase())).map((b: any) => [b.id, scenario.commodities.find((c: any) => c.id === b.commodity_id)?.name || b.commodity_id, `${snapshot.warehouse_stock_by_batch[b.id]} kg`, `${Math.max(0, b.observed_at_min + (b.remaining_life_low_min ?? b.remaining_life_nominal_min) - snapshot.decision_at_min)}–${Math.max(0, b.observed_at_min + (b.remaining_life_high_min ?? b.remaining_life_nominal_min) - snapshot.decision_at_min)} min`, b.status, <button disabled={!!busy || !plan} onClick={() => void action('Loading decision receipt', async () => setReceipt(await api.fetchBatchReceiptApi(plan!.plan_id, b.id)))}>View receipt <Icon name="arrow" /></button>])} /></>}
        {tab === 'Dispatch' && <><h2>Vehicle schedule and allocations</h2>{!plan ? <EmptyState icon="truck" title="Good deliveries start with a clear plan.">Generate a plan to match your available produce with vehicles, destinations, and dispatch windows.</EmptyState> : <>
          <Table headings={['Vehicle', 'Destination', 'Load starts', 'Departure', 'Ready', 'Vehicle release', 'Quantity']} rows={plan.trips.map((t: any) => [t.vehicle_id, t.destination_id, `${t.loading_start_min}m`, `${t.departure_min}m`, `${t.ready_for_sale_min}m`, `${t.vehicle_release_min}m`, `${t.total_loaded_units}/${t.capacity_units} kg`])} />
          {!plan.trips.length && <p>No new dispatch is recommended under these constraints.</p>}
          <h3>Batch allocations</h3><Table headings={['Batch', 'Destination', 'Quantity', 'Price / kg', 'Discount', 'Departure']} rows={plan.allocations.map((a: any) => [a.batch_id, a.destination_id, `${a.quantity_units} kg`, money(a.unit_price_paise), `${a.discount_pct}%`, `${a.departure_min}m`])} />
          <h3>Dispatch windows</h3><Table headings={['Batch', 'Destination', 'Latest scheduled departure', 'Slack', 'Reason']} rows={plan.deadlines.map((d: any) => [d.batch_id, d.destination_id, d.scheduled_latest_departure_min == null ? 'Unavailable' : `${d.scheduled_latest_departure_min}m`, d.dispatch_slack_min == null ? '—' : `${d.dispatch_slack_min}m`, d.rejection_reason || 'Eligible under plan assumptions'])} />
        </>}</>}
        {tab === 'Disruptions' && <><h2>Observe a disruption and replan</h2><p>Existing commitments keep their stock and vehicle capacity locked. New plans use the remaining availability.</p>
          <h3>Existing commitments</h3><p>Move a reservation to the next feasible departure, or release it back to available stock for a {money(scenario.cost_model.cancellation_fee_paise_per_trip)} cancellation fee. Loading and dispatched trips stay locked.</p>
          <Table headings={['Vehicle', 'Route', 'State', 'Loading', 'Departure', 'Vehicle return', 'Move', 'Release']} rows={scenario.commitments.map((c: any) => [c.vehicle_id, c.route_id, c.state, `${c.loading_start_min}m`, `${c.departure_min}m`, `${c.vehicle_release_min}m`, <button aria-label={`Move reservation ${c.vehicle_id} ${c.route_id}`} disabled={!!busy || c.state !== 'reserved' || c.loading_start_min <= snapshot.decision_at_min} onClick={() => void action('Finding a feasible reservation slot', async () => {
            const result = await api.requestJson(`/api/v1/scenarios/${encodeURIComponent(selected)}/commitments/${encodeURIComponent(c.id)}/reschedule`, { expected_revision: snapshot.state_revision, idempotency_key: crypto.randomUUID() }); await refresh(selected); clearResults(); setNotice(`Reservation moved to minute ${result.departure_min}. Generate a new plan to validate the updated schedule.`);
          })}>Move reservation</button>, <button aria-label={`Release reservation ${c.vehicle_id} ${c.route_id}`} disabled={!!busy || c.state !== 'reserved' || c.loading_start_min <= snapshot.decision_at_min} onClick={() => void action('Releasing the reservation', async () => {
            await api.requestJson(`/api/v1/scenarios/${encodeURIComponent(selected)}/commitments/${encodeURIComponent(c.id)}/release`, { expected_revision: snapshot.state_revision, idempotency_key: crypto.randomUUID() }); await refresh(selected); clearResults(); setNotice('Reservation released and cancellation fee recorded. Generate a new plan for the recovered stock.');
          })}>Release · {money(scenario.cost_model.cancellation_fee_paise_per_trip)}</button>])} />
          <div className="toolbar"><label>Route<select aria-label="Route" value={routeId} onChange={e => setRouteId(e.target.value)}>{scenario.routes.map((r: any) => <option key={r.id}>{r.id}</option>)}</select></label><label>Additional delay (minutes)<input aria-label="Delay minutes" type="number" min="0" max="720" value={delay} onChange={e => setDelay(Number(e.target.value))} /></label>
            <button disabled={!!busy || !routeId || !Number.isInteger(delay) || delay < 0} onClick={() => void action('Recording observed delay', async () => { await api.postOperationalEventApi(selected, { id: crypto.randomUUID(), event_type: 'ROUTE_DELAY_OBSERVED', entity_id: routeId, effective_at_min: snapshot.decision_at_min, expected_prior_revision: snapshot.state_revision, payload: { delay_minutes: delay } }); await refresh(selected); setRankings(null); setRecovery(null); setNotice('Delay recorded. The previous plan is historical; generate a revised plan.'); })}>Apply delay</button>
          </div><button disabled={!!busy} onClick={() => void action('Comparing recovery options', async () => setRecovery(await api.fetchRecoveryOptionsApi(selected, plan?.plan_id)))}>Compare recovery options</button>{recovery && <JsonDetails title="Computed recovery comparison" data={recovery} />}
        </>}
        {tab === 'Inspections' && <><h2>Which batch is worth inspecting?</h2><p>Inspection is simulated, consumes time, and incurs its configured cost. Estimates are not safety certifications.</p>
          <button disabled={!!busy} onClick={() => void action('Estimating inspection value', async () => setRankings(await api.runJob('inspection', { scenario_id: selected, planning_seed: 1701 })))}>Rank inspections</button>
          {!rankings && <EmptyState icon="scan" title="A closer look can change the plan.">Compare the potential value of inspecting each batch, with the cost of the inspection and the time it takes already considered.</EmptyState>}
          {rankings && <Table headings={['Batch', 'Estimated value', 'Cost', 'Duration', 'Recommendation', 'Action']} rows={rankings.map(r => [r.batch_id, money(r.estimated_inspection_value_paise), money(r.inspection_cost_paise), `${r.inspection_duration_min}m`, r.recommendation_reason, <button disabled={!!busy} onClick={() => void action('Simulating inspection', async () => { const result = await api.executeInspectionApi(selected, r.batch_id, `inspect_${snapshot.scenario_version_id}_${r.batch_id}`); await refresh(selected); setRankings(null); setNotice(`Observed ${result.observation.observed_band} life for ${r.batch_id}. Generate a new plan using the updated estimate.`); })}>Simulate inspection</button>])} />}{rankings?.length === 0 && <p>No eligible inspections remain.</p>}
        </>}
        {tab === 'Evaluation' && <><h2>Simulated outcomes and comparisons</h2><p>Replay uses the plan's stored snapshot. It does not replace the live operating ledger.</p>
          <button disabled={!!busy || !plan} onClick={() => void action('Replaying the stored plan', async () => setReplay(await api.runJob('replay', { plan_id: plan!.plan_id, world_seed: 2026 })))}>Run replay</button>
          {!replay && <EmptyState icon="chart" title="A forecast is only the beginning.">Replay a generated plan to explore simulated sales, spoilage, and contribution under a seeded set of conditions.</EmptyState>}
          {replay && <><div className="cards"><Metric title="Sold" value={`${replay.sold_total_units} kg`} /><Metric title="Expired unsold" value={`${replay.expired_unsold_total_units} kg`} /><Metric title="Unexpired carryover" value={`${replay.unexpired_carryover_total_units} kg`} /><Metric title="Realized simulated net" value={money(replay.realized_economics.net_outcome_paise)} /></div><Economics data={replay.realized_economics} /><p>Inventory reconciled: {replay.ledger_reconciled ? 'Yes' : 'No'} · Service failures: {replay.realized_service_failures}</p><JsonDetails title="Replay evidence" data={replay} /></>}
          <h3>Current-scenario policy comparison</h3><p>This is an exploratory synthetic comparison, not a held-out validation claim. The FIFO reference is illustrative, not organizer supplied.</p><button disabled={!!busy} onClick={() => void action('Comparing policies across shared simulation worlds', async () => setBenchmark(await api.runJob('benchmark', { scenario_ids: [selected], world_count: 2, base_seed: 2026 })))}>Compare policies</button>
          {benchmark && <><Table headings={['Policy', 'Mean simulated net', 'Mean sold', 'Mean spoilage']} rows={Object.entries(benchmark.policy_summaries).map(([name, row]: [string, any]) => [name, money(row.mean_net_paise), `${row.mean_sold_units.toFixed(1)} kg`, `${row.mean_spoilage_units.toFixed(1)} kg`])} /><JsonDetails title="Comparison manifest and evidence" data={benchmark} /></>}
        </>}
        </div>
      </>}
      {receipt && <div className="modal-backdrop" onClick={() => setReceipt(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="Decision receipt" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape') setReceipt(null); }}><button autoFocus onClick={() => setReceipt(null)}>Close receipt</button><h2>Decision receipt · {receipt.batch_id}</h2><p>{receipt.explanation_text}</p><ul>{receipt.binding_constraints.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul><JsonDetails title="Complete evidence" data={receipt} /></section></div>}
    </main><footer><span><span className="footer-dot" />FreshRoute LastWindow</span><span>Software simulation <b>·</b> Estimates stay visible. Decisions stay yours.</span></footer>
  </div></div>;
}
export function Table({ headings, rows }: { headings: string[]; rows: React.ReactNode[][] }) { return <div className="table-wrap"><table><thead><tr>{headings.map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>{rows.length ? rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{typeof cell === 'string' && ['available', 'reserved', 'loading', 'dispatched', 'quarantined', 'depleted'].includes(cell) ? <span className={`status-pill status-${cell}`}><i />{cell}</span> : cell}</td>)}</tr>) : <tr><td className="empty-table" colSpan={headings.length}><Icon name="box" /><strong>No items to display</strong><span>Adjust your search or generate a plan to see the relevant records.</span></td></tr>}</tbody></table></div>; }
function Metric({ title, value, icon = 'chart', hint, accent = false }: { title: string; value: string; icon?: string; hint?: string; accent?: boolean }) { return <article className={`metric ${accent ? 'metric-accent' : ''}`}><div className="metric-label"><p>{title}</p><Icon name={icon} /></div><strong>{value}</strong>{hint && <span className="metric-hint">{hint}</span>}</article>; }
function Economics({ data }: { data: Data }) { return <div className="economics">{Object.entries(data).filter(([k]) => k.endsWith('_paise') && k !== 'sunk_committed_cost_paise').map(([k, v]) => <div className={k === 'net_outcome_paise' ? 'net-row' : ''} key={k}><span>{k === 'net_outcome_paise' ? 'Net contribution' : k.replace(/_paise$/, '').replace(/_/g, ' ')}</span><strong>{money(v)}</strong></div>)}</div>; }
function JsonDetails({ title, data }: { title: string; data: unknown }) { return <details className="evidence"><summary><Icon name="file" />{title}<Icon name="chevron" /></summary><pre>{JSON.stringify(data, null, 2)}</pre></details>; }
function Brand() { return <div className="brand"><span className="brand-mark"><Icon name="leaf" /></span><div><span>FRESHROUTE</span><strong>LastWindow<span className="brand-period">.</span></strong></div></div>; }
function EmptyState({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) { return <div className="feature-empty"><span className="feature-icon"><Icon name={icon} /></span><h3>{title}</h3><p>{children}</p></div>; }
function duration(minutes: number) { return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`; }
function shortName(name: string) { const label = name.replace(/\s*\(.*\)/, ''); return label.length > 21 ? `${label.slice(0, 21).trimEnd()}…` : label; }
function clock(scenario: Data, minutes: number) { const start = /T(\d{2}):(\d{2})/.exec(scenario.start_at || ''); const total = (start ? Number(start[1]) * 60 + Number(start[2]) : 0) + minutes; return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`; }

function Network({ scenario, plan }: { scenario: Data; plan: Data | null }) {
  const [focus, setFocus] = useState('');
  const [zoom, setZoom] = useState(1);
  const points = [{ x: 462, y: 87 }, { x: 522, y: 213 }, { x: 445, y: 339 }, { x: 300, y: 365 }];
  const destinations = scenario.destinations.slice(0, 4);
  const selected = scenario.destinations.find((d: any) => d.id === focus);
  return <section className="panel network-panel"><div className="panel-heading"><div><span className="eyebrow">THE BIG PICTURE</span><h2>Your distribution network</h2></div><span className="subtle-pill"><i />{plan ? 'Plan generated' : 'Ready to plan'}</span></div><div className="network-map"><svg viewBox="0 0 760 420" role="group" aria-label="Schematic of available distribution corridors and planned quantities; not a geographic map"><defs><pattern id="map-grid" width="38" height="38" patternUnits="userSpaceOnUse"><path d="M 38 0 L 0 0 0 38" fill="none" stroke="#dce3da" strokeWidth=".7" /></pattern></defs><rect width="760" height="420" fill="#f0f3ec" /><rect width="760" height="420" fill="url(#map-grid)" /><path d="M-20 340C180 240 160 465 410 300S650 60 810 130" fill="none" stroke="#dce8df" strokeWidth="44" /><path d="M-20 340C180 240 160 465 410 300S650 60 810 130" fill="none" stroke="#e5ede5" strokeWidth="35" /><g className="map-streets" fill="none" stroke="#fff" strokeWidth="10"><path d="M-20 140L210 140 325 10M70 450L255 260 740 260M330 450L330 10M-20 75L800 390M600-10L600 450" /></g><g transform={`translate(${380 * (1 - zoom)} ${210 * (1 - zoom)}) scale(${zoom})`}>{destinations.map((d: any, i: number) => {
    const point = points[i]; const route = scenario.routes.find((r: any) => r.destination_id === d.id); const quantity = plan ? plan.allocations.filter((a: any) => a.destination_id === d.id).reduce((s: number, a: any) => s + a.quantity_units, 0) : null;
    const path = `M160 208 C${270 + i * 15} ${208 + (point.y - 208) * .05} ${point.x - 100} ${point.y} ${point.x} ${point.y}`;
    return <g key={d.id} className={`network-corridor ${quantity ? 'allocated' : ''} ${focus && focus !== d.id ? 'dimmed' : ''}`}><path d={path} fill="none" stroke="#d2dbcf" strokeWidth="7" /><path d={path} className="route-line" fill="none" stroke={quantity ? '#438969' : '#8d9f8a'} strokeWidth="2.5" strokeDasharray="7 7" /><g transform={`translate(${point.x - 130} ${point.y < 200 ? point.y + 32 : point.y - 28})`}><rect x="-37" y="-13" width="100" height="27" rx="13" fill="white" stroke="#e0e7dc" /><text x="12" y="5" textAnchor="middle" fill="#51624e" fontSize="12" fontWeight="600">{route ? `${route.outbound_travel_min} min` : 'No corridor'}{quantity ? ` · ${quantity} kg` : ''}</text></g><g className="map-node" role="button" tabIndex={0} aria-label={`Inspect corridor to ${d.name}`} onClick={() => setFocus(focus === d.id ? '' : d.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setFocus(focus === d.id ? '' : d.id); } }} transform={`translate(${point.x} ${point.y})`}><circle r="21" fill="white" stroke={focus === d.id ? '#164b36' : '#d8e1d3'} strokeWidth="2" /><path d="M-7 1L0-6 7 1M-5-1V8H5V-1M-1 3H2V8" fill="none" stroke="#3e654b" strokeWidth="1.7" strokeLinecap="round" /><text x="32" y="-3" fontSize="14" fontWeight="650" fill="#23392a">{shortName(d.name)}</text><text x="32" y="17" fontSize="11" fill="#758273">{quantity == null ? 'Awaiting allocation' : `${quantity} kg planned`} · {clock(scenario, d.receiving_close_min)} close</text></g></g>;
  })}<g transform="translate(160 208)"><circle r="43" fill="#1f593c" opacity=".08" /><rect x="-27" y="-27" width="54" height="54" rx="16" fill="#194d35" /><path d="M-13-2L0-12 13-2V13H-13ZM-5 13V3H5V13" fill="none" stroke="#c6ed99" strokeWidth="2" strokeLinejoin="round" /><text y="52" textAnchor="middle" fontSize="15" fontWeight="700" fill="#254030">Distribution centre</text><text y="69" textAnchor="middle" fontSize="11" fill="#758273">ORIGIN · {scenario.batches.length} BATCHES</text></g></g><text x="22" y="397" fontSize="10" letterSpacing="1.5" fill="#84907f">SCHEMATIC VIEW / NOT LIVE GPS</text></svg><div className="map-zoom"><button aria-label="Zoom in on network" disabled={zoom >= 1.3} onClick={() => setZoom(z => Math.min(1.3, z + .15))}>+</button><button aria-label="Zoom out on network" disabled={zoom <= 1} onClick={() => setZoom(z => Math.max(1, z - .15))}>−</button></div></div><div className="network-caption"><span><i className="legend-dot" />{selected ? selected.name : `Origin → ${scenario.destinations.length} destinations`}</span><button className="text-button" onClick={() => { setFocus(''); setZoom(1); }}>Reset view <Icon name="reset" /></button></div><div className="mobile-corridors">{destinations.map((d: any) => <button key={d.id} onClick={() => setFocus(d.id)}><strong>{d.name.replace(/\s*\(.*\)/, "")}</strong><span>{plan ? `${plan.allocations.filter((a: any) => a.destination_id === d.id).reduce((s: number, a: any) => s + a.quantity_units, 0)} kg planned` : "Not planned"}</span></button>)}</div>{scenario.destinations.length > 4 && <p className="map-overflow-note">Showing 4 of {scenario.destinations.length} destinations. See Dispatch for the complete schedule.</p>}</section>;
}

function Icon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    leaf: <><path d="M19 4C9 2 3 8 6 15c7 4 14-2 13-11Z" /><path d="M4 20 15 9M9 15v-4M9 15h5" /></>,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1.5" /><rect x="14" y="4" width="6" height="6" rx="1.5" /><rect x="4" y="14" width="6" height="6" rx="1.5" /><rect x="14" y="14" width="6" height="6" rx="1.5" /></>,
    box: <><path d="m12 3 9 5v9l-9 5-9-5V8ZM3 8l9 5 9-5M12 13v9M8 5l9 5" /></>,
    truck: <><path d="M3 5h11v12H3ZM14 9h4l3 4v4h-7" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></>,
    pulse: <path d="M2 12h5l3-8 4 16 3-8h5" />,
    scan: <><path d="M8 3H4v4M16 3h4v4M4 17v4h4M20 17v4h-4M3 12h18" /><rect x="8" y="7" width="8" height="10" rx="2" /></>,
    chart: <><path d="M4 4v16h17M9 16v-4M14 16V7M19 16v-6" /></>,
    arrow: <path d="M4 12h15m-5-5 5 5-5 5" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    trend: <><path d="m3 16 6-6 4 4 8-9M15 5h6v6" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" /><path d="m8 12 3 3 5-6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    route: <><circle cx="5" cy="6" r="2" /><circle cx="19" cy="18" r="2" /><path d="M7 6h9a4 4 0 0 1 0 8H8a2 2 0 0 0 0 4h9" /></>,
    upload: <><path d="M12 16V3m-4 4 4-4 4 4M4 15v5h16v-5" /></>,
    download: <><path d="M12 3v13m-4-4 4 4 4-4M4 17v4h16v-4" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1" /></>,
    alert: <><path d="m12 3 10 18H2Z" /><path d="M12 9v5M12 17h.01" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    file: <><path d="M5 3h9l5 5v13H5ZM14 3v6h5M9 13h6M9 17h5" /></>,
    reset: <><path d="M4 10a8 8 0 1 1 1 8M4 4v6h6" /></>,
  };
  return <svg className="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.grid}</svg>;
}
