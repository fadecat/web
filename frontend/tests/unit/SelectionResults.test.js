import { it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import ElementPlus from 'element-plus';
// 「相关讨论」依赖的两个 API 必须先 mock(挂载即触发当前页批量预热, 不能真发请求)
vi.mock('../../src/api/index.js',()=>({getCbDiscussion:vi.fn(),warmCbDiscussions:vi.fn(),getCbAdjustment:vi.fn()}));
import { getCbDiscussion, warmCbDiscussions, getCbAdjustment } from '../../src/api/index.js';
import Results from '../../src/components/selection/SelectionResults.vue';

// jsdom 里 EP popover 的悬浮事件挂载不生效, 用直通 stub: 常显内容 + mouseenter 即触发 @show,
// 只验证本组件的预热/惰性兜底/三态渲染逻辑(定位与延迟属 EP 职责, 走浏览器冒烟)。
const PopoverStub={
 name:'ElPopover',
 props:['placement','width','trigger','showAfter'],
 emits:['show'],
 template:'<span class="popover-stub" @mouseenter="$emit(\'show\')"><slot name="reference"/><slot/></span>',
};
const mountResults=(props)=>mount(Results,{props,global:{plugins:[ElementPlus],stubs:{ElPopover:PopoverStub}}});
const rowsPage={rows:[{code:'1',name:'甲'},{code:'2',name:'乙'}],excluded_rows:[],meta:{}};

beforeEach(()=>{
 localStorage.clear();
 warmCbDiscussions.mockReset().mockResolvedValue({items:{}});
 getCbDiscussion.mockReset();
 getCbAdjustment.mockReset();
});

it('uses compact default columns in the requested order and keeps operation fixed right',async()=>{
 const w=mountResults({result:{rows:[{code:'1',name:'很长的债券名称',stock_nm:'正股',stock_financial:{eps_growth_ttm:12.345}}],excluded_rows:[],meta:{}}});
 await flushPromises();
 const tableText=w.find('.el-table').text();
 expect(tableText.indexOf('代码')).toBeLessThan(tableText.indexOf('名称'));
 expect(tableText.indexOf('名称')).toBeLessThan(tableText.indexOf('排名'));
 expect(tableText.indexOf('排名')).toBeLessThan(tableText.indexOf('正股'));
 expect(tableText).toContain('净利润增长');
 expect(tableText).not.toContain('利润指标');
 expect(tableText.lastIndexOf('操作')).toBeGreaterThan(tableText.indexOf('强赎'));
 expect(w.find('.density-compact').exists()).toBe(true);
 expect(w.text()).toContain('12.35%');
 w.unmount();
});

it('persists density and optional columns while retaining defaults on reset',async()=>{
  localStorage.setItem('cb-selection-results-preferences',JSON.stringify({density:'comfortable',columns:['price','stock_financial_profit']}));
  const w=mountResults({result:{rows:[{code:'1',name:'甲',stock_financial:{profit_average:1}}],excluded_rows:[],meta:{}}});
  await flushPromises();
  expect(w.find('.density-comfortable').exists()).toBe(true);
  expect(w.text()).toContain('代码'); expect(w.text()).toContain('名称');
  expect(w.find('.el-table').text()).toContain('净利润增长');
 expect(localStorage.getItem('cb-selection-results-preferences')).toContain('comfortable');
 const densityButton=w.findAll('button').find((b)=>b.text().includes('紧凑密度'));
 await densityButton.trigger('click');
 expect(w.find('.density-compact').exists()).toBe(true);
 expect(localStorage.getItem('cb-selection-results-preferences')).toContain('compact');
 await w.findAll('button').find((b)=>b.text()==='列设置').trigger('click');
 w.vm.toggleColumn('total_score');
 expect(JSON.parse(localStorage.getItem('cb-selection-results-preferences')).columns).toContain('total_score');
 w.vm.resetColumns();
 expect(JSON.parse(localStorage.getItem('cb-selection-results-preferences')).columns).not.toContain('total_score');
 w.unmount();
});

it('maps 净利润增长 to eps_growth_ttm for positive and negative sorting',async()=>{
 const w=mountResults({result:{rows:[{code:'1',name:'正',stock_financial:{eps_growth_ttm:8}},{code:'2',name:'负',stock_financial:{eps_growth_ttm:-3}}],excluded_rows:[],meta:{}}});
 await flushPromises();
 expect(w.vm.sortValue({stock_financial:{eps_growth_ttm:8}},'stock_financial_eps_growth_ttm')).toBe(8);
 expect(w.vm.sortValue({stock_financial:{eps_growth_ttm:-3}},'stock_financial_eps_growth_ttm')).toBe(-3);
 w.vm.sortBy('stock_financial_eps_growth_ttm','ascending');
 await flushPromises();
 expect(w.vm.rows.map((row)=>row.code)).toEqual(['2','1']);
 w.unmount();
});

it('changes default and optional columns through checkbox change handlers in metadata order',async()=>{
 const w=mountResults({result:{rows:[{code:'1',name:'甲',stock_nm:'股',pb:1,convert_price:10}],excluded_rows:[],meta:{}}});
 await flushPromises();
 await w.findAll('button').find((b)=>b.text()==='列设置').trigger('click');
 const checkbox=(label)=>w.findAll('.el-checkbox').find((el)=>el.find('.el-checkbox__label').text()===label);
 w.vm.toggleColumn('industry_name');
 await flushPromises();
 expect(w.find('.el-table').text()).not.toContain('行业');
 w.vm.toggleColumn('industry_name');
 await flushPromises();
 expect(w.find('.el-table').text()).toContain('行业');
 w.vm.toggleColumn('convert_price');
 await flushPromises();
 const headers=w.findAll('.el-table th .cell').map((el)=>el.text()).filter(Boolean);
 expect(headers).toContain('转股价');
 expect(headers.indexOf('转股价值')).toBeLessThan(headers.indexOf('转股价'));
 expect(checkbox('代码').classes()).toContain('is-disabled');
 w.unmount();
});

it('renders zero, negative and missing yield distinctly with real table rows',async()=>{
 const w=mountResults({result:{selection_mode:'filter_only',rows:[{code:'1',name:'零',simple_maturity_yield_pct:0},{code:'2',name:'负',simple_maturity_yield_pct:-12},{code:'3',name:'缺',simple_maturity_yield_pct:null}],excluded_rows:[],meta:{}}});
 await flushPromises();
 expect(w.text()).toContain('0.00%');expect(w.text()).toContain('-12.00%');expect(w.text()).toContain('—');expect(w.text()).not.toContain('[object Object]');w.unmount();
});

it('warms current page discussions on mount without per-bond requests',async()=>{
 const w=mountResults({result:rowsPage});
 await flushPromises();
 expect(warmCbDiscussions).toHaveBeenCalledTimes(1);
 expect(warmCbDiscussions).toHaveBeenCalledWith(['1','2']);
 expect(getCbDiscussion).not.toHaveBeenCalled(); // 未悬浮不逐只请求
 w.unmount();
});

it('shows warmed discussions without extra request on hover',async()=>{
 warmCbDiscussions.mockResolvedValue({items:{1:[{title:'盛路转债怎么玩',url:'https://www.jisilu.cn/question/525018',replies:'11条回复',views:'2089次浏览',date:'2026-09-04'}]}});
 const w=mountResults({result:rowsPage});
 await flushPromises();
 expect(w.find('.popover-stub').text()).toContain('盛路转债怎么玩'); // 预热合并后常显即可见
 expect(w.find('.popover-stub').text()).toContain('11条回复');
 await w.find('.popover-stub').trigger('mouseenter'); // @show → ensureDiscussion
 await flushPromises();
 expect(getCbDiscussion).not.toHaveBeenCalled(); // 已 ok 不再发单只请求
 w.unmount();
});

it('falls back to lazy per-bond fetch when warm-up missed',async()=>{
 warmCbDiscussions.mockResolvedValue({items:{}}); // 预热未取到
 getCbDiscussion.mockResolvedValue({bond_id:'1',items:[{title:'兜底帖',url:'https://www.jisilu.cn/question/2',replies:'1条回复',views:'5次浏览',date:''}]});
 const w=mountResults({result:rowsPage});
 await flushPromises();
 expect(w.find('.popover-stub').text()).toContain('讨论加载中'); // 未取到也未悬浮: 非终态文案
 await w.find('.popover-stub').trigger('mouseenter');
 await flushPromises();
 expect(getCbDiscussion).toHaveBeenCalledWith('1');
 expect(w.find('.popover-stub').text()).toContain('兜底帖');
 w.unmount();
});

it('shows error state and allows retry on hover after failure',async()=>{
 warmCbDiscussions.mockResolvedValue({items:{}});
 getCbDiscussion.mockRejectedValueOnce(new Error('boom'));
 const w=mountResults({result:rowsPage});
 await flushPromises();
 await w.find('.popover-stub').trigger('mouseenter');
 await flushPromises();
 expect(w.find('.popover-stub').text()).toContain('加载失败');
 getCbDiscussion.mockResolvedValue({bond_id:'1',items:[]});
 await w.find('.popover-stub').trigger('mouseenter'); // error 状态允许重试
 await flushPromises();
 expect(getCbDiscussion).toHaveBeenCalledTimes(2);
 expect(w.find('.popover-stub').text()).toContain('暂无相关讨论');
 w.unmount();
});

it('marks revised bonds with stars and loads logs on click popover only',async()=>{
 localStorage.setItem('cb-selection-results-preferences',JSON.stringify({columns:['convert_price']}));
 const adjRows={rows:[{code:'1',name:'甲',convert_price:3.47,adj_scnt:2},{code:'2',name:'乙',convert_price:10.5,adj_scnt:0}],excluded_rows:[],meta:{}};
 getCbAdjustment.mockResolvedValue({bond_id:'1',items:[{meeting_date:'2026-07-16',price_before:5.26,price_after:3.47,effective_date:'2026-07-17',floor_price:3.47}]});
 const w=mountResults({result:adjRows});
 await flushPromises();
 expect(w.text()).toContain('3.47'); // 转股价列渲染
 const stars=w.findAll('.adj-stars'); // 星标只给下修过的行: 甲 ** 乙无
 expect(stars).toHaveLength(1);
 expect(stars[0].text()).toBe('**');
 expect(getCbAdjustment).not.toHaveBeenCalled(); // 未点击不请求
 // 触发转股价 popover 的 @show(stub 的 mouseenter 挂在外层, mouseenter 不冒泡,
 // 须在含 .cp-cell 的 stub 上触发) → 惰性拉取
 const cpStub=w.findAll('.popover-stub').find((s)=>s.find('.cp-cell').exists());
 await cpStub.trigger('mouseenter');
 await flushPromises();
 expect(getCbAdjustment).toHaveBeenCalledWith('1');
 expect(w.text()).toContain('2026-07-16'); // 明细表渲染
 expect(w.text()).toContain('转股价下修记录');
 w.unmount();
});

it('shows adjustment error state and retries on reopen',async()=>{
 localStorage.setItem('cb-selection-results-preferences',JSON.stringify({columns:['convert_price']}));
 getCbAdjustment.mockRejectedValueOnce(new Error('boom'));
 const w=mountResults({result:{rows:[{code:'1',name:'甲',convert_price:3.47,adj_scnt:1}],excluded_rows:[],meta:{}}});
 await flushPromises();
 const cpStub=()=>w.findAll('.popover-stub').find((s)=>s.find('.cp-cell').exists());
 await cpStub().trigger('mouseenter');
 await flushPromises();
 expect(w.text()).toContain('加载失败'); // 弹窗内失败态
 getCbAdjustment.mockResolvedValue({bond_id:'1',items:[]});
 await cpStub().trigger('mouseenter'); // error 状态重新打开可重试
 await flushPromises();
 expect(getCbAdjustment).toHaveBeenCalledTimes(2);
 expect(w.text()).toContain('暂无下修记录');
 w.unmount();
});
