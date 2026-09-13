import { it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import ElementPlus from 'element-plus';
// 「相关讨论」依赖的两个 API 必须先 mock(挂载即触发当前页批量预热, 不能真发请求)
vi.mock('../../src/api/index.js',()=>({getCbDiscussion:vi.fn(),warmCbDiscussions:vi.fn()}));
import { getCbDiscussion, warmCbDiscussions } from '../../src/api/index.js';
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
 warmCbDiscussions.mockReset().mockResolvedValue({items:{}});
 getCbDiscussion.mockReset();
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
