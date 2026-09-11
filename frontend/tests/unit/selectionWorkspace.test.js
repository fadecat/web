import { describe, it, expect, vi } from 'vitest';
import { useSelectionWorkspace } from '../../src/composables/useSelectionWorkspace.js';
const tmpl = (id) => ({id, name:id, conditions:[], strategy_factors:[], target_count:10, hold_tolerance:0, migration_issues:[]});
const deferred = () => { let resolve; const promise = new Promise(r => { resolve=r; }); return {promise, resolve}; };
const setup = async (overrides={}) => {
 const api={getFactors:vi.fn().mockResolvedValue({version:3,revision:'r1',active_id:'a',templates:[tmpl('a'),tmpl('b')]}),saveFactors:vi.fn(async c => ({data:{...c,revision:'r2'}})),screenBonds:vi.fn().mockResolvedValue({rows:[],meta:{}}),...overrides};
 const ws=useSelectionWorkspace(api); await ws.load(); return {ws,api};
};
describe('V3 workspace',()=>{
 it('does not mark results stale when saving only reorders condition keys',async()=>{
  const {ws}=await setup({saveFactors:async c=>({data:{...c,templates:c.templates.map(t=>({...t,conditions:t.conditions.map(rule=>Object.fromEntries(Object.entries(rule).reverse()))}))}})});
  ws.patch({conditions:[{id:'x',enabled:true,field:'price',op:'gte',value:100,missing:'exclude',negative:'compare'}]});
  await ws.run();await ws.save();expect(ws.stale.value).toBe(false);
  ws.patch({conditions:[{...ws.current.value.conditions[0],value:101}]});expect(ws.stale.value).toBe(true);
 });
 it('saves only current draft and preserves another template draft',async()=>{
  const {ws,api}=await setup(); ws.patch({name:'edited A'}); ws.editingId.value='b'; ws.patch({name:'edited B'}); await ws.save();
  expect(api.saveFactors.mock.calls[0][0].templates.map(t=>t.name)).toEqual(['a','edited B']);
  ws.editingId.value='a'; expect(ws.current.value.name).toBe('edited A'); expect(ws.dirty.value).toBe(true);
 });
 it('retains edits made while saving',async()=>{
  const d=deferred(); const {ws}=await setup({saveFactors:()=>d.promise}); ws.patch({name:'first'}); const p=ws.save(); ws.patch({name:'second'});
  d.resolve({data:{version:3,revision:'r2',active_id:'a',templates:[{...tmpl('a'),name:'first'},tmpl('b')]}});await p;
  expect(ws.current.value.name).toBe('second'); expect(ws.dirty.value).toBe(true);
 });
 it('does not display A result under B and marks changed conditions stale',async()=>{
  const d=deferred();const {ws}=await setup({screenBonds:()=>d.promise});const p=ws.run(); ws.editingId.value='b';d.resolve({rows:[],meta:{}});await p;
  expect(ws.result.value).toBeNull();ws.editingId.value='a';expect(ws.result.value).not.toBeNull();
  ws.patch({name:'renamed'});expect(ws.stale.value).toBe(false);ws.patch({conditions:[{id:'x',field:'price',op:'gte',value:100}]});expect(ws.stale.value).toBe(true);
 });
 it('preserves draft on revision conflict',async()=>{
  const {ws}=await setup({saveFactors:vi.fn().mockRejectedValue(new Error('conflict'))});ws.patch({name:'keep'});await expect(ws.save()).rejects.toThrow();expect(ws.current.value.name).toBe('keep');expect(ws.dirty.value).toBe(true);
 });
 it('creates independent new templates with business status and ST conditions',async()=>{
  const {ws}=await setup();ws.create('new');expect(ws.current.value.conditions.map(c=>c.field)).toEqual(['redeem_status_code','stock_is_st']);expect(ws.current.value.strategy_factors).toEqual([]);
 });
});
