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
  // 详情页会用到的其余端点(AddAssetDialog 的注册/解析、成员行重试)
  probeAsset: vi.fn(),
  createAsset: vi.fn(),
  refreshAsset: vi.fn(),
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
  const mountPage = async ({ assets = [], listAssetsResult = [], runResult = null } = {}) => {
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

  // 区间快捷选择(控制条「请选择」下拉, 韭圈儿同款: 成立以来 / 事件锚点 / 按年份)
  const RUN_OK = {
    id: 1,
    result: {
      windows: WINDOWS,
      t0_date: '2013-04-26',
      metrics: {},
      drawdown: null,
      data_range: { start: '2013-04-26', last: '2026-09-18' },
      assets: MEMBERS,
    },
  };

  it('控制条有「请选择」区间快捷下拉', async () => {
    const wrapper = await mountPage({ assets: MEMBERS });
    const select = wrapper.findComponent(ElSelect);
    expect(select.exists()).toBe(true);
    expect(select.props('placeholder')).toBe('请选择');
  });

  it('区间快捷: 选「成立以来」把起始日设为 T0 并立即重跑', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    api.runBacktest.mockClear();

    wrapper.findComponent(ElSelect).vm.$emit('change', 'inception');
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

    wrapper.findComponent(ElSelect).vm.$emit('change', 'inception');
    await flushPromises();

    expect(api.runBacktest).not.toHaveBeenCalled();
  });

  // 快捷区间条(照韭圈儿底部那一条): 相对区间按钮 + 「请选择」下拉
  const STRIP_LABELS = ['今年以来', '近1月', '近3月', '近6月', '近1年', '近3年', '近5年', '近10年'];

  it('快捷区间条渲染 8 个相对区间 + 请选择下拉', async () => {
    const wrapper = await mountPage({ assets: MEMBERS, runResult: RUN_OK });
    const labels = wrapper.findAll('.range-strip button').map((btn) => btn.text());
    expect(labels).toEqual(STRIP_LABELS);
    expect(wrapper.find('.range-strip').findComponent(ElSelect).exists()).toBe(true);
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
});
