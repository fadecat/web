// 组合详情页组件测试(vitest + @vue/test-utils)。
//
// 覆盖区域①(收益条)与区域⑥(相关性矩阵)这两块"规则写在模板里、最容易被改错"的地方。
// 曲线(NavChart)不在此测: 它依赖 echarts 的 canvas 渲染, jsdom 下不可靠 ——
// 数值换算逻辑已由 src/utils/backtestView.test.mjs 覆盖。
import { describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import ElementPlus, { ElSelect } from 'element-plus';

import AddAssetDialog from '../../src/components/portfolio/AddAssetDialog.vue';
import CorrelationMatrix from '../../src/components/portfolio/CorrelationMatrix.vue';
import ReturnBar from '../../src/components/portfolio/ReturnBar.vue';

// 页面级用例需要打桩路由与 API(jsdom 下没有真实后端)
const api = vi.hoisted(() => ({
  getPortfolio: vi.fn(),
  listAssets: vi.fn(),
  patchPortfolio: vi.fn(),
  deletePortfolio: vi.fn(),
  runBacktest: vi.fn(),
  // 详情页会用到的其余端点(AddAssetDialog 的注册/解析、成员行重试、基准下拉)
  probeAsset: vi.fn(),
  createAsset: vi.fn(),
  refreshAsset: vi.fn(),
  listBenchmarks: vi.fn(),
}));
vi.mock('../../src/api/portfolio', () => api);
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '1' } }),
  useRouter: () => ({ push: vi.fn() }),
}));

// ⚠ 必须在 vi.mock 之后导入(否则拿到的是未打桩的模块)
import PortfolioDetail from '../../src/pages/PortfolioDetail.vue';

const WINDOWS = {
  d1: { value: 1.08, actual_start: null, actual_end: '2026-09-18', composite: true },
  w1: { value: -0.41, actual_start: '2026-09-11', actual_end: '2026-09-18', composite: false },
  m1: { value: -0.62, actual_start: '2026-08-19', actual_end: '2026-09-18', composite: false },
  ytd: { value: 6.67, actual_start: '2026-01-02', actual_end: '2026-09-18', composite: false },
  y1: { value: 10.4, actual_start: '2025-09-18', actual_end: '2026-09-18', composite: false },
  y3: { value: 53.51, actual_start: '2023-09-18', actual_end: '2026-09-18', composite: false },
  inception: { value: 335.07, actual_start: '2013-04-26', actual_end: '2026-09-18', composite: false },
};

const mountWith = (Component, props) =>
  mount(Component, { props, global: { plugins: [ElementPlus] } });

describe('ReturnBar(区域① 收益条)', () => {
  it('渲染七格且标签顺序固定', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const labels = wrapper.findAll('.cell .label').map((n) => n.text().replace('ⓘ', '').trim());
    expect(labels).toEqual(['近1日', '近1周', '近1月', '今年来', '近1年', '近3年', '成立来']);
  });

  it('首格放大, 其余不放大', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const cells = wrapper.findAll('.cell');
    expect(cells[0].classes()).toContain('is-featured');
    expect(cells[1].classes()).not.toContain('is-featured');
  });

  it('涨红跌绿: 正数 trend-up, 负数 trend-down', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const values = wrapper.findAll('.cell .value');
    expect(values[0].classes()).toContain('trend-up');
    expect(values[1].classes()).toContain('trend-down');
    expect(values[0].text()).toBe('1.08%');
  });

  it('只有「近1日」带合成口径标记', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const marks = wrapper.findAll('.cell .mark');
    expect(marks).toHaveLength(1);
    expect(marks[0].element.parentElement.textContent).toContain('近1日');
  });

  it('空数据(不可回测)显示破折号而不是 0.00%', () => {
    const wrapper = mountWith(ReturnBar, { windows: null });
    const values = wrapper.findAll('.cell .value');
    expect(values).toHaveLength(7);
    values.forEach((v) => {
      expect(v.text()).toBe('—');
      expect(v.classes()).toContain('trend-flat');
    });
  });
});

describe('CorrelationMatrix(区域⑥ 相关性矩阵)', () => {
  const correlation = {
    start: '2016-09-19',
    end: '2026-09-18',
    symbols: ['100001.OF', '100002.OF', '100003.OF'],
    matrix: [
      [1, 0.43, 0.06],
      [0.43, 1, 0.09],
      [0.06, 0.09, 1],
    ],
  };
  const assets = [
    { symbol: '100001.OF', name: '测试基金A' },
    { symbol: '100002.OF', name: '测试基金B' },
    { symbol: '100003.OF', name: '测试基金C' },
  ];

  it('显式标注计算区间(与回测区间对齐方向不同)', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    expect(wrapper.find('.range').text()).toContain('2016-09-19');
    expect(wrapper.find('.range').text()).toContain('2026-09-18');
  });

  it('行列表头按 1..N 编号, 且带名称与代码', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    const colHeads = wrapper.findAll('.col-head').map((n) => n.text());
    expect(colHeads).toEqual(['1', '2', '3']);
    expect(wrapper.findAll('.row-head')).toHaveLength(3);
    expect(wrapper.find('.row-head .name').text()).toBe('测试基金A');
    expect(wrapper.find('.row-head .code').text()).toBe('100001.OF');
  });

  it('对角线为 1 且带 is-diagonal 标记', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    const firstRow = wrapper.findAll('tbody tr')[0];
    const cells = firstRow.findAll('.value');
    expect(cells[0].text()).toBe('1.00');
    expect(cells[0].classes()).toContain('is-diagonal');
    expect(cells[1].text()).toBe('0.43');
  });

  it('弱相关不转白字, 强相关才转(保证数字可读)', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    const firstRow = wrapper.findAll('tbody tr')[0];
    const cells = firstRow.findAll('.value');
    expect(cells[1].classes()).not.toContain('is-strong'); // 0.43 < 0.55
  });

  it('无相关性数据时不渲染表格', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation: null, assets: [] });
    expect(wrapper.find('table').exists()).toBe(false);
  });
});

describe('PortfolioDetail 页面结构(空组合必须有加标的入口)', () => {
  // 背景: 详情页最初只做了"从已注册标的里挑", 而标的库里的标的都已在本组合中
  //       → 下拉恒为空, 用户完全没法往组合里加东西。这里把"入口必须存在"锁死。
  const mountPage = async ({
    assets = [], listAssetsResult = [], runResult = null, benchmarks = [],
  } = {}) => {
    api.getPortfolio.mockResolvedValue({
      id: 1,
      name: '空组合',
      created_at: '2026-09-19T00:00:00',
      status: 'active',
      default_rebalance: null,
      assets,
      weight_sum: 0,
      ready: false,
      data_readiness: { assets: [], all_ready: false, common_start: null },
    });
    api.listAssets.mockResolvedValue(listAssetsResult);
    api.listBenchmarks.mockResolvedValue(benchmarks);
    if (runResult) {
      api.runBacktest.mockResolvedValue(runResult);
    } else {
      api.runBacktest.mockRejectedValue({
        response: { status: 422, data: { detail: '组合还没有成员' } },
      });
    }
    const wrapper = mount(PortfolioDetail, {
      global: {
        plugins: [ElementPlus],
        // 这几个子组件与本用例无关(曲线依赖 canvas, 相关性/收益条需要完整数据)
        stubs: { NavChart: true, CorrelationMatrix: true, ReturnBar: true },
      },
    });
    await flushPromises();
    await flushPromises();
    return wrapper;
  };

  it('空组合显示「还没有标的」并给出添加按钮', async () => {
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain('这个组合还没有标的');
    const buttons = wrapper.findAll('button').map((b) => b.text());
    expect(buttons.some((t) => t.includes('添加标的'))).toBe(true);
  });

  it('编辑态提供「+ 新标的」入口(而不只是"从已注册里挑")', async () => {
    const wrapper = await mountPage();
    // 进入编辑态
    const editBtn = wrapper.findAll('button').find((b) => b.text() === '编辑');
    expect(editBtn).toBeTruthy();
    await editBtn.trigger('click');
    await flushPromises();

    const labels = wrapper.findAll('button').map((b) => b.text());
    expect(labels.some((t) => t.includes('新标的'))).toBe(true);
  });

  it('AddAssetDialog 已挂载且初始关闭', async () => {
    const wrapper = await mountPage();
    const dialog = wrapper.findComponent(AddAssetDialog);
    expect(dialog.exists()).toBe(true);
    expect(dialog.props('modelValue')).toBe(false);
  });

  it('点「+ 新标的」打开注册对话框(可注册库外新标的)', async () => {
    const wrapper = await mountPage();
    const editBtn = wrapper.findAll('button').find((b) => b.text() === '编辑');
    await editBtn.trigger('click');
    await flushPromises();

    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('新标的'));
    await addBtn.trigger('click');
    await flushPromises();

    expect(wrapper.findComponent(AddAssetDialog).props('modelValue')).toBe(true);
  });

  // add-asset-ux §二第 4 步(状态列) + §四(失败可重试 / 无数据标红)
  const MEMBERS = [
    {
      id: 11, symbol: '600900.SH', name: '长江电力', security_type: 'STOCK',
      target_weight: 50, added_at: '2026-09-19T00:00:00', row_count: 0,
      last_sync_status: null, last_sync_error: null,
    },
    {
      id: 12, symbol: '159698.SZ', name: '粮食ETF', security_type: 'ETF',
      target_weight: 50, added_at: '2026-09-19T00:00:00', row_count: 1200,
      last_sync_status: 'success', last_sync_error: null,
    },
  ];

  const enterEdit = async (wrapper) => {
    const editBtn = wrapper.findAll('button').find((b) => b.text() === '编辑');
    await editBtn.trigger('click');
    await flushPromises();
  };

  it('编辑器状态列: 有行数=就绪, 没抓到数据=无数据且整行标红', async () => {
    const wrapper = await mountPage({ assets: MEMBERS });
    await enterEdit(wrapper);

    expect(wrapper.text()).toContain('就绪');
    expect(wrapper.text()).toContain('无数据');
    // 标红是"真的用不了"的信号, 只该出现在无数据那一行
    expect(wrapper.findAll('.row-blocked').length).toBe(1);
    expect(wrapper.findAll('button').some((b) => b.text() === '重试')).toBe(true);
  });

  it('点「重试」走单标的补抓, 且不重跑回测', async () => {
    api.refreshAsset.mockResolvedValue({ status: 'success', rows: 1200 });
    const wrapper = await mountPage({ assets: MEMBERS });
    api.runBacktest.mockClear(); // 进页面那次默认回测不算
    await enterEdit(wrapper);

    const retryBtn = wrapper.findAll('button').find((b) => b.text() === '重试');
    await retryBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    expect(api.refreshAsset).toHaveBeenCalledWith(11);
    expect(api.runBacktest).not.toHaveBeenCalled(); // 回测只由用户点按钮触发
  });

  // add-asset-ux §四: 权重合计 ≠ 100% → 按钮置灰(后端也 422, 前端先拦住)
  const backtestBtn = (wrapper) => wrapper.findAll('button').find((b) => b.text() === '组合回测');

  it('权重合计 100% 时「组合回测」可用', async () => {
    const wrapper = await mountPage({ assets: MEMBERS }); // 50 + 50
    expect(backtestBtn(wrapper).attributes('disabled')).toBeUndefined();
  });

  it('权重合计≠100% 时「组合回测」置灰并说明原因', async () => {
    const wrapper = await mountPage({
      assets: [{ ...MEMBERS[1], target_weight: 50 }],
    });
    expect(backtestBtn(wrapper).attributes('disabled')).toBeDefined();
    expect(wrapper.text()).toContain('权重合计须为 100%');
  });

  it('空组合也不能回测(按钮置灰, 不靠后端报错)', async () => {
    const wrapper = await mountPage();
    expect(backtestBtn(wrapper).attributes('disabled')).toBeDefined();
  });

  it('起点前移警示条可一键移除该标的(add-asset-ux §二第 5 步)', async () => {
    const wrapper = await mountPage({
      assets: [
        ...MEMBERS,
        {
          id: 13, symbol: '513100.SH', name: '纳指ETF国泰', security_type: 'ETF',
          target_weight: 0, added_at: '2026-09-19T00:00:00', row_count: 6494,
          last_sync_status: 'success', last_sync_error: null,
        },
      ],
    });
    await enterEdit(wrapper);
    expect(wrapper.text()).toContain('纳指ETF国泰');

    wrapper.findComponent(AddAssetDialog).vm.$emit('start-change', {
      asset: { symbol: '513100.SH', name: '纳指ETF国泰' },
      newStart: '2013-04-26',
      title: '⚠ 新的组合起点：2013-04-26',
      detail: '原起点 2003-12-02 → 前移 9.4 年',
    });
    await flushPromises();
    expect(wrapper.text()).toContain('新的组合起点');

    const remove = wrapper.findAll('button').find((b) => b.text() === '移除该标的');
    await remove.trigger('click');
    await flushPromises();

    expect(wrapper.text()).not.toContain('纳指ETF国泰'); // 该标的已撤出草稿
  });

  // 区间快捷选择(韭圈儿「请选择」+ 相对区间条) 与曲线头部 / 基准下拉
  const BENCHMARKS = [
    {
      symbol: '000300', name: '沪深300指数', kind: 'index', price_basis: 'PRICE',
      row_count: 5996, first_date: '2002-01-04', last_date: '2026-09-18',
    },
    {
      symbol: '510300.SH', name: '沪深300ETF', kind: 'asset', price_basis: 'HFQ',
      row_count: 1200, first_date: '2021-01-04', last_date: '2026-09-18',
    },
  ];

  const RUN_OK = {
    id: 1,
    result: {
      windows: WINDOWS,
      t0_date: '2013-04-26',
      metrics: {},
      drawdown: null,
      data_range: { start: '2013-04-26', last: '2026-09-18' },
      assets: MEMBERS,
      // 「近10年」= 2026-09-18 自然月回推 120 个月(头部据此显示区间名)
      start_date: '2016-09-18',
      end_date: null,
      // 实际生效的**意图**起点(未做交易日对齐, 与候选区间可比)
      effective_start: '2016-09-18',
      actual_start: '2016-09-14',
      actual_end: '2026-09-18',
      selected_return: 211.22,
      selected_drawdown: -16.15,
      benchmark: {
        symbol: '000300', name: '沪深300指数', price_basis: 'PRICE', total_return: 39.17,
        // 基准**自己**的回撤(与组合同口径同函数) —— 回撤模式下头部要显示它
        drawdown: {
          value: -45.6, peak_date: '2021-02-10', trough_date: '2024-09-13',
          recovery_date: null, recovery_days: null,
        },
      },
    },
  };

  const rangeSelect = (wrapper) => wrapper.findComponent('.range-preset');

  it('曲线头部: 收益模式显示「选中区间名 + 区间收益」', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const head = wrapper.find('.chart-headline');
    expect(head.text()).toContain('近10年'); // 由 result 的起止日反推出来的区间名
    expect(head.text()).toContain('211.22%');
  });

  it('曲线头部: 回撤模式把中间那格换成「区间最大回撤」', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const tab = wrapper.findAll('.el-tabs__item').find((t) => t.text() === '组合回撤');
    await tab.trigger('click');
    await flushPromises();

    const head = wrapper.find('.chart-headline');
    expect(head.text()).toContain('区间最大回撤');
    expect(head.text()).toContain('-16.15%');
  });

  it('⭐ 回撤模式: 头部也要带区间名(否则看不出这段回撤属于哪几年)', async () => {
    // 收益模式是「近10年: 211.22%」, 回撤模式原来只剩「区间最大回撤 -16.15%」 —— 数字旁边
    // 没有时间参照, 用户不知道这是哪一段的回撤(用户实测提出)。
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const tab = wrapper.findAll('.el-tabs__item').find((t) => t.text() === '组合回撤');
    await tab.trigger('click');
    await flushPromises();

    const head = wrapper.find('.chart-headline');
    expect(head.text()).toContain('近10年');
    expect(head.text()).toContain('区间最大回撤');
    expect(head.text()).toContain('-16.15%');
  });

  it('⭐ 进页面默认回测: 日期框回填生效起点, 快捷条高亮「近10年」', async () => {
    // 一进页面就自动跑了一次默认区间(表单为空 → 后端"末端回推 10 年"), 但表单原本是空的:
    // 日期框空着、底部一条都不高亮, 用户根本看不出这一屏数字属于哪段区间(用户实测提出)。
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });

    const pickers = wrapper.findAllComponents({ name: 'ElDatePicker' });
    expect(pickers.length).toBe(2);
    expect(pickers[0].props('modelValue')).toBe('2016-09-18'); // 回填的是 start_date(意图口径)
    expect(pickers[1].props('modelValue')).toBe('');           // 结束日留空 = 到最新

    const active = wrapper
      .findAll('.range-strip button')
      .filter((btn) => btn.classes().includes('el-button--primary'))
      .map((btn) => btn.text());
    expect(active).toContain('近10年');
  });

  it('基准下拉: 显示名称而不是代码, 并带上基准区间收益', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK, benchmarks: BENCHMARKS });
    const select = wrapper.findComponent('.bench-select');
    expect(select.exists()).toBe(true);
    expect(select.props('modelValue')).toBe('000300');
    expect(wrapper.find('.control-bench').text()).toBe('沪深300指数');
    expect(wrapper.find('.chart-benchmark').text()).toContain('39.17%');
  });

  it('⭐ 回撤模式: 基准那格换成「基准区间最大回撤」, 不再拿收益冒充', async () => {
    // 用户实测: 回撤模式下头部是「组合 区间最大回撤 -16.50%」并列「基准 +36.45%」(涨红),
    // 一个回撤一个收益, 且与图上蓝线最低点对不上 —— 基准那格没有跟着模式换指标。
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK, benchmarks: BENCHMARKS });
    const tab = wrapper.findAll('.el-tabs__item').find((t) => t.text() === '组合回撤');
    await tab.trigger('click');
    await flushPromises();

    const bench = wrapper.find('.chart-benchmark');
    expect(bench.text()).toContain('-45.60%'); // 基准自己的最大回撤
    expect(bench.text()).not.toContain('39.17%'); // 收益不能再出现在回撤模式
    expect(bench.find('b').classes()).toContain('trend-down'); // 回撤是"跌" → 绿, 不是涨红
  });

  it('回撤模式: 旧 Run 没有基准回撤字段时显示占位符, 不崩', async () => {
    // Run 是"跑那一次的快照", 引擎 v5 之前生成的旧 Run 里没有 benchmark.drawdown
    const legacy = JSON.parse(JSON.stringify(RUN_OK));
    delete legacy.result.benchmark.drawdown;
    const wrapper = await mountPage({ assets: MEMBERS, runResult: legacy, benchmarks: BENCHMARKS });
    const tab = wrapper.findAll('.el-tabs__item').find((t) => t.text() === '组合回撤');
    await tab.trigger('click');
    await flushPromises();

    const bench = wrapper.find('.chart-benchmark');
    expect(bench.text()).toContain('—');
    expect(bench.text()).not.toContain('39.17%');
  });

  it('⭐ 曲线头部: 第一格显示**回测区间起止**, 不是只有一个截止日', async () => {
    // 用户实测: 选了「成立以来」这格仍只写 2026-09-18, 看不出这段 335% 从哪年起算。
    // 起点取 `effective_start`(意图口径) → 与「自定义」输入框一致; 终点 end_date 为空 = 到最新。
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    expect(wrapper.find('.chart-asof').text()).toBe('2016-09-18 ~ 2026-09-18');

    // 头部读的是**结果**里的区间(旁边是实数, 标签必须跟着实数走), 所以按结果分别验证
    const inception = JSON.parse(JSON.stringify(RUN_OK));
    inception.result.start_date = '2013-04-26';
    inception.result.effective_start = '2013-04-26';
    const w1 = await mountPage({ assets: MEMBERS, runResult: inception });
    expect(w1.find('.chart-asof').text()).toBe('2013-04-26 ~ 2026-09-18');

    // 用户填了结束日时用 end_date, 不再回落到 actual_end
    const bounded = JSON.parse(JSON.stringify(RUN_OK));
    bounded.result.start_date = '2018-01-01';
    bounded.result.effective_start = '2018-01-01';
    bounded.result.end_date = '2024-12-31';
    const w2 = await mountPage({ assets: MEMBERS, runResult: bounded });
    expect(w2.find('.chart-asof').text()).toBe('2018-01-01 ~ 2024-12-31');
  });

  it('⭐ 没选区间时(打开任意组合): 日期框也要填出生效起点', async () => {
    // 用户实测: 打开「我的组合5」头部已显示 2022-04-21, 而前端日期选择器还是空的。
    // 根因: 不传区间时后端 `start_date` 是 null, 前端只拿得到 `actual_start`。
    // 现在后端给 `effective_start` = max(用户所选或默认区间起点, T0), 且**未做交易日对齐**。
    const noRange = JSON.parse(JSON.stringify(RUN_OK));
    noRange.result.start_date = null;               // 用户没选 → 走默认"末端整年回推10年"
    noRange.result.effective_start = '2022-04-21';  // 被 T0 顶上来的意图起点(数据不足10年)
    noRange.result.actual_start = '2022-04-21';

    const wrapper = await mountPage({ assets: MEMBERS, runResult: noRange });
    const pickers = wrapper.findAllComponents({ name: 'ElDatePicker' });
    expect(pickers[0].props('modelValue')).toBe('2022-04-21');
    expect(wrapper.find('.chart-asof').text()).toBe('2022-04-21 ~ 2026-09-18');
  });

  it('基准下拉: 选「无基准」时把 benchmarkSymbol 传成 null', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK, benchmarks: BENCHMARKS });
    api.runBacktest.mockClear();

    const select = wrapper.findComponent('.bench-select');
    // el-select 的真实顺序是先 update:modelValue(v-model 落值) 再 change(触发重跑)
    select.vm.$emit('update:modelValue', '');
    await flushPromises();
    select.vm.$emit('change', '');
    await flushPromises();

    expect(api.runBacktest.mock.calls.at(-1)[0].benchmarkSymbol).toBeNull();
  });

  it('控制条有「请选择」区间快捷下拉', async () => {
    const wrapper = await mountPage({ assets: MEMBERS });
    const select = rangeSelect(wrapper);
    expect(select.exists()).toBe(true);
    expect(select.props('placeholder')).toBe('请选择');
  });

  it('区间快捷: 选「成立以来」把起始日设为 T0 并立即重跑', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    api.runBacktest.mockClear();

    rangeSelect(wrapper).vm.$emit('change', 'inception');
    await flushPromises();

    expect(api.runBacktest).toHaveBeenCalledTimes(1);
    const payload = api.runBacktest.mock.calls[0][0];
    // ⚠ 必须显式给 T0: start 为空会落到后端"末端回推 10 年"的默认, 那不是"成立以来"
    expect(payload.start).toBe('2013-04-26');
    expect(payload.end).toBeNull();
  });

  it('区间快捷: 还没跑成过一次回测(无 T0)时不猜日期、不重跑', async () => {
    const wrapper = await mountPage({ assets: MEMBERS }); // runBacktest 被拒 → 无 result
    api.runBacktest.mockClear();

    rangeSelect(wrapper).vm.$emit('change', 'inception');
    await flushPromises();

    expect(api.runBacktest).not.toHaveBeenCalled();
  });

  // 快捷区间条(照韭圈儿底部那一条): 相对区间按钮 + 「请选择」下拉
  const STRIP_LABELS = ['今年以来', '近1月', '近3月', '近6月', '近1年', '近3年', '近5年', '近10年'];

  it('快捷区间条渲染 8 个相对区间 + 请选择下拉, 且位置在曲线下方', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const labels = wrapper.findAll('.range-strip button').map((btn) => btn.text());
    expect(labels).toEqual(STRIP_LABELS);
    expect(wrapper.find('.range-strip').findComponent('.range-preset').exists()).toBe(true);
    // 位置: 必须落在曲线容器里(韭圈儿把这条放在图下方, 不在控制条那一行)
    expect(wrapper.find('.chart-area').find('.range-strip').exists()).toBe(true);
  });

  it('快捷区间条: 点「近3月」按自然月回推设起始日并立即重跑', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    api.runBacktest.mockClear();

    const btn = wrapper.findAll('.range-strip button').find((b) => b.text() === '近3月');
    await btn.trigger('click');
    await flushPromises();

    const payload = api.runBacktest.mock.calls[0][0];
    expect(payload.start).toBe('2026-06-18'); // 2026-09-18 自然月回推 3 个月
    expect(payload.end).toBeNull();
  });

  it('快捷区间条: 选中项高亮由表单反推(手改日期后不高亮)', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const buttonByText = (text) => wrapper.findAll('.range-strip button').find((b) => b.text() === text);

    await buttonByText('近5年').trigger('click');
    await flushPromises();
    expect(buttonByText('近5年').classes()).toContain('el-button--primary');
    expect(buttonByText('近1月').classes()).not.toContain('el-button--primary');
  });

  it('⭐ 底部「请选择」下拉: 相对区间不得把裸 key 显示出来(span-10y)', async () => {
    // 相对区间已由左侧那排按钮承担, 这个下拉只管固定日期项(成立以来/事件锚点/按年份)。
    // 若把统一 key 直接绑给它, el-select 在选项里找不到 `span-*` → **把裸 key 原样回显**
    // (用户实测截图里出现过 `span-10y`)。这是 el-select 的通用陷阱: 值不在选项里就显示原始值。
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const select = wrapper.findComponent('.range-preset');
    expect(select.exists()).toBe(true);
    expect(select.props('modelValue')).toBe(''); // 默认区间 = 近10年(span-10y) → 下拉留空

    const btn = wrapper.findAll('.range-strip button').find((b) => b.text() === '近3月');
    await btn.trigger('click');
    await flushPromises();
    expect(select.props('modelValue')).toBe(''); // 相对区间一律不进这个下拉

    // 固定日期项仍要正常回显(别把功能一起修没了)
    select.vm.$emit('change', 'inception');
    await flushPromises();
    expect(wrapper.findComponent('.range-preset').props('modelValue')).toBe('inception');
  });
});
