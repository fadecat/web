import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import ElementPlus from 'element-plus';
import { createRouter, createMemoryHistory } from 'vue-router';
vi.mock('../../src/api/index.js',()=>({getFactorCatalog:vi.fn(),getFactors:vi.fn(),getRatingCatalog:vi.fn(),getIndustryCatalog:vi.fn(),screenBonds:vi.fn(),saveFactors:vi.fn(),getBlacklist:vi.fn(),addBlacklist:vi.fn(),removeBlacklist:vi.fn()}));
import * as api from '../../src/api/index.js';
import Factors from '../../src/pages/Factors.vue';
const t={id:'t1',name:'稳健',conditions:[],strategy_factors:[],target_count:10,hold_tolerance:0,migration_issues:[]};
beforeEach(()=>{
 vi.resetAllMocks();
 api.getFactorCatalog.mockResolvedValue([{field:'price',label:'当前价格',type:'number',operators:['gte','between'],filterable:true,scorable:true,allow_negative:false}]);
 api.getFactors.mockResolvedValue({version:3,revision:'r1',active_id:'t1',templates:[t]});
 api.getRatingCatalog.mockResolvedValue([{value:'NONE',label:'无评级'}]);api.getIndustryCatalog.mockResolvedValue([]);
 api.screenBonds.mockResolvedValue({total_all:1,total_filtered:1,total_excluded:0,selection_mode:'filter_only',source:'db',rows:[{rank:1,code:'110001',name:'测试转债',industry_name:'银行',price:100,redeem_price:110,simple_maturity_yield_pct:10}],excluded_rows:[],meta:{}});
});
async function page(){const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Factors}]});await router.push('/');await router.isReady();const w=mount(Factors,{global:{plugins:[ElementPlus,router]}});await flushPromises();return w;}
describe('转债选债完整页面',()=>{
 it('initially executes V3 once and renders actual industry and yield values',async()=>{
  const w=await page();expect(api.screenBonds).toHaveBeenCalledTimes(1);expect(api.screenBonds.mock.calls[0]).toEqual([{...t,schema_version:3},'db']);
  expect(w.text()).toContain('银行');expect(w.text()).toContain('110.00');expect(w.text()).toContain('10.00%');expect(w.text()).toContain('符合条件');
  expect(w.text()).toContain('市净率');expect(w.text()).toContain('到期赎回价');w.unmount();
 });
 it('pending migration prevents automatic execution and exposes resolution',async()=>{
  api.getFactors.mockResolvedValue({version:3,revision:'r1',active_id:'t1',templates:[{...t,migration_issues:[{id:'i',status:'pending',message:'旧强赎图标待确认',original:{value:['R']}}]}]});
  const w=await page();expect(api.screenBonds).not.toHaveBeenCalled();expect(w.text()).toContain('待确认');w.unmount();
 });
});
