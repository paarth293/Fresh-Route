import { afterEach, describe, expect, it, vi } from 'vitest';
import { money, stalePlan } from '../src/format';
import { ApiError, importScenarioApi, runJob, commitPlanRequest } from '../src/api/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { Table } from '../src/App';

afterEach(() => vi.unstubAllGlobals());
const response = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });
describe('actual application helpers', () => {
  it('formats integer paise without changing the sign', () => {
    expect(money(204000)).toContain('2,040.00');
    expect(money(-178205)).toContain('-');
    expect(money(undefined)).toBe('Not calculated');
  });
  it('rejects a matching revision on a different scenario version', () => {
    expect(stalePlan({state_revision:1,scenario_version_id:'v1'}, {state_revision:1,scenario_version_id:'v2'})).toBe(true);
    expect(stalePlan({state_revision:1,scenario_version_id:'v1'}, {state_revision:1,scenario_version_id:'v1'})).toBe(false);
  });
  it('renders actual table data and escapes imported content', () => {
    const html = renderToStaticMarkup(createElement(Table, {headings:['Batch'],rows:[['<script>bad</script>']]}));
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>');
  });
});
describe('backend API contracts', () => {
  it('uses the canonical import route', async () => {
    const fetch = vi.fn().mockResolvedValue(response({scenario_id:'sample'})); vi.stubGlobal('fetch',fetch);
    await importScenarioApi({scenario_id:'sample'});
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/scenarios');
  });
  it('surfaces validation details', async () => {
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({error:{details:{validation_errors:[{field_path:'batches[0]',message:'Invalid quantity'}]}}},422)));
    await expect(importScenarioApi({})).rejects.toThrow('batches[0]: Invalid quantity');
  });
  it('fetches the completed plan rather than returning a job as a plan', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({status:'succeeded',result_id:'p1'})).mockResolvedValueOnce(response({plan_id:'p1'}));
    vi.stubGlobal('fetch',fetch); expect(await runJob('planning',{})).toEqual({plan_id:'p1'});
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/plans/p1');
  });
  it('does not render failed jobs as successful empty plans', async () => {
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({status:'failed',error_message:'Solver unavailable'})));
    await expect(runJob('planning',{})).rejects.toThrow('Solver unavailable');
  });
  it('fetches nonplanning results from reports', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({status:'succeeded',result_id:'r1'})).mockResolvedValueOnce(response([]));
    vi.stubGlobal('fetch',fetch); await runJob('inspection',{}); expect(fetch.mock.calls[1][0]).toBe('/api/v1/reports/r1');
  });
  it('passes the current revision to commit and preserves errors', async () => {
    const fetch = vi.fn().mockResolvedValue(response({error:{message:'State changed'}},409)); vi.stubGlobal('fetch',fetch);
    await expect(commitPlanRequest('p1',4,'retry-key')).rejects.toBeInstanceOf(ApiError);
    expect(JSON.parse(fetch.mock.calls[0][1].body).expected_revision).toBe(4);
  });
});
