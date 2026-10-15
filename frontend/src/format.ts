export function money(paise: number | undefined | null): string {
  if (paise == null || !Number.isFinite(paise)) return 'Not calculated';
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(paise / 100);
}
export function stalePlan(plan: any, snapshot: any): boolean {
  return !plan || !snapshot || plan.state_revision !== snapshot.state_revision || plan.scenario_version_id !== snapshot.scenario_version_id;
}
