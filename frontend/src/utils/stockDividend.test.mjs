// stockDividend.mjs 契约与纯函数测试: node --test 前端工具测试
// 运行: node --test src/utils/stockDividend.test.mjs (或 pnpm test:node)
// 覆盖: 列配置完整性(21 项无会员占位列)/市场判定/每类筛选控件边界
//       (负 PE、null 行过滤、区间空集、北交所防御、sw_cd null 拒、仅国资、
//       分红率派生)/sanitizeForm 预设收敛/行业树分层回退排序/省份去重/
//       排序 null 沉底/格式化色阶档
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  STOCK_DIVIDEND_COLUMNS,
  DEFAULT_SORT,
  PAGE_SIZES,
  emptyForm,
  sanitizeForm,
  marketOf,
  payoutOf,
  withPayoutRate,
  matchStock,
  filterRows,
  buildIndustryTree,
  collectProvinceOptions,
  sortRows,
  fmtNum,
  fmtVolume,
  tempText,
  tempClass,
  signedText,
  signedClass,
} from './stockDividend.mjs';

/** 测试行工厂: 全字段有值的基准行, 用 over 覆盖局部字段。 */
function baseRow(over = {}) {
  return {
    trade_date: '2026-09-11',
    stock_id: '600001',
    stock_nm: '示例股份',
    sw_cd: '801160',
    industry_nm2: '交通运输-铁路公路-铁路运输',
    industry_nm: '铁路运输',
    province: '北京',
    price: 10.5,
    increase_rt: 1.23,
    volume: 123456.0,
    total_value: 300.0,
    float_value: 280.0,
    pe: 8.5,
    pb: 0.9,
    roe: 12.0,
    roe_average: 11.0,
    pe_temperature: 30.0,
    pb_temperature: 45.0,
    dividend_rate: 5.0,
    dividend_rate2: 4.8,
    aft_dividend: 4.5,
    revenue_average: 5.0,
    profit_average: 4.0,
    cashflow_average: 3.0,
    eps_growth_ttm: 2.0,
    int_debt_rate: 25.0,
    debt_rate: 40.0,
    ...over,
  };
}

/** 仅启用一个条件的便捷 form。 */
function formWith(over = {}) {
  return { ...emptyForm(), ...over };
}

// ---------------------------------------------------------------------------
// 列配置 / 常量
// ---------------------------------------------------------------------------

test('列配置: 恰 21 项, 字段唯一, 不含会员占位列与按需隐藏列', () => {
  assert.equal(STOCK_DIVIDEND_COLUMNS.length, 21);
  const fields = STOCK_DIVIDEND_COLUMNS.map((c) => c.field);
  assert.equal(new Set(fields).size, 21);
  // 会员占位列不复刻
  assert.equal(fields.includes('stdevry'), false);
  assert.equal(fields.includes('pledge_rt'), false);
  // 按需求不展示(涨幅/成交额/双温度的筛选条件仍保留;
  // aft_dividend(5年平均股息率)列与筛选条件均已移除, 由派生列「分红率」取代)
  for (const hidden of ['increase_rt', 'volume', 'pe_temperature', 'pb_temperature', 'aft_dividend']) {
    assert.equal(fields.includes(hidden), false);
  }
  // 分红率为前端派生列(股息率TTM×PE, 排在静态股息率之后)
  const payoutIdx = fields.indexOf('payout_rate');
  assert.equal(payoutIdx, fields.indexOf('dividend_rate2') + 1);
  // 每列必备骨架字段
  for (const c of STOCK_DIVIDEND_COLUMNS) {
    assert.equal(typeof c.field, 'string');
    assert.equal(typeof c.label, 'string');
    assert.equal(typeof c.width, 'number');
    assert.ok(['left', 'center', 'right'].includes(c.align));
    assert.ok(['num2', 'volume', 'signed', 'temp', 'text'].includes(c.fmt));
  }
});

test('列配置: 前两列代码/名称 fixed left, 股息率TTM 列存在', () => {
  assert.equal(STOCK_DIVIDEND_COLUMNS[0].field, 'stock_id');
  assert.equal(STOCK_DIVIDEND_COLUMNS[0].fixed, 'left');
  assert.equal(STOCK_DIVIDEND_COLUMNS[1].field, 'stock_nm');
  assert.equal(STOCK_DIVIDEND_COLUMNS[1].fixed, 'left');
  assert.equal(typeof STOCK_DIVIDEND_COLUMNS.find((c) => c.field === 'dividend_rate'), 'object');
});

test('默认排序 = 股息率TTM 降序, 分页档 20/50/100', () => {
  assert.deepEqual(DEFAULT_SORT, { prop: 'dividend_rate', order: 'descending' });
  assert.deepEqual(PAGE_SIZES, [20, 50, 100]);
});

test('emptyForm: 全部未启用(markets 空/字符串空/阈值 null), 每次返回新对象', () => {
  const a = emptyForm();
  a.peMax = 5;
  a.markets.push('sh');
  a.industries.push('80');
  a.soeOnly = true;
  const b = emptyForm();
  assert.equal(b.peMax, null);
  assert.deepEqual(b.markets, []);
  assert.deepEqual(b.industries, []);
  assert.deepEqual(b.excludeIndustries, []);
  assert.equal(b.soeOnly, false);
});

// ---------------------------------------------------------------------------
// sanitizeForm(服务端预设 → 合法表单)
// ---------------------------------------------------------------------------

test('sanitizeForm: 合法输入逐键透传, 缺失键回退 emptyForm 默认值', () => {
  const src = {
    markets: ['sh'], industries: ['8011'], excludeIndustries: ['90', '11'], provinces: ['北京'],
    peMax: 15, dividendMin: 3, payoutMin: 60, soeOnly: true,
  };
  const f = sanitizeForm(src);
  assert.deepEqual(f, {
    ...emptyForm(),
    markets: ['sh'], industries: ['8011'], excludeIndustries: ['90', '11'], provinces: ['北京'],
    peMax: 15, dividendMin: 3, payoutMin: 60, soeOnly: true,
  });
  // 旧版已下线键(aftDividendMin)按未知键丢弃
  assert.equal('aftDividendMin' in sanitizeForm({ aftDividendMin: 3 }), false);
});

test('sanitizeForm: 未知键丢弃, 非法类型逐键回退(字符串数字/NaN/Infinity 不收)', () => {
  const f = sanitizeForm({
    markets: ['sh', 'bj', 3], // 'bj'/3 剔除, 'sh' 保留
    industries: ['80', 42, null, ''], // 非字符串/空串剔除
    peMax: '15', // 字符串数字不收
    dividendMin: NaN,
    roeMin: Infinity,
    soeOnly: 'yes', // 非布尔回退 false
    evil: { hack: true }, // 未知键丢弃
  });
  assert.deepEqual(f.markets, ['sh']);
  assert.deepEqual(f.industries, ['80']);
  assert.equal(f.excludeIndustries.length, 0);
  assert.equal(f.peMax, null);
  assert.equal(f.dividendMin, null);
  assert.equal(f.roeMin, null);
  assert.equal(f.soeOnly, false);
  assert.equal('evil' in f, false);
});

test('sanitizeForm: null/undefined 输入得空表单(纯函数不改输入)', () => {
  assert.deepEqual(sanitizeForm(null), emptyForm());
  assert.deepEqual(sanitizeForm(undefined), emptyForm());
  assert.deepEqual(sanitizeForm({}), emptyForm());
  const src = { peMax: 15 };
  sanitizeForm(src);
  assert.deepEqual(src, { peMax: 15 });
});

// ---------------------------------------------------------------------------
// marketOf
// ---------------------------------------------------------------------------

test('marketOf: 60/68→sh, 00/30→sz, 北交所/空/非常规→null', () => {
  assert.equal(marketOf('600000'), 'sh');
  assert.equal(marketOf('688981'), 'sh');
  assert.equal(marketOf('000001'), 'sz');
  assert.equal(marketOf('300750'), 'sz');
  assert.equal(marketOf('830799'), null); // 北交所防御
  assert.equal(marketOf('430047'), null);
  assert.equal(marketOf(''), null);
  assert.equal(marketOf(null), null);
  assert.equal(marketOf(undefined), null);
});

// ---------------------------------------------------------------------------
// payoutOf / withPayoutRate(分红率派生)
// ---------------------------------------------------------------------------

test('payoutOf: 分红率=股息率TTM×PE; 任一缺失或 PE≤0(亏损无意义) → null', () => {
  assert.equal(payoutOf({ dividend_rate: 5, pe: 8 }), 40);
  assert.equal(payoutOf({ dividend_rate: 5, pe: 0.5 }), 2.5);
  assert.equal(payoutOf({ dividend_rate: 0, pe: 8 }), 0);
  assert.equal(payoutOf({ dividend_rate: null, pe: 8 }), null); // 股息缺数
  assert.equal(payoutOf({ dividend_rate: 5, pe: null }), null); // PE 缺数
  assert.equal(payoutOf({ dividend_rate: 5, pe: -20 }), null); // 亏损
  assert.equal(payoutOf({ dividend_rate: 5, pe: 0 }), null); // PE=0 同无意义
  assert.equal(payoutOf({ dividend_rate: NaN, pe: 8 }), null);
  assert.equal(payoutOf({}), null);
  assert.equal(payoutOf(null), null);
});

test('withPayoutRate: 每行物化 payout_rate 字段(浅拷贝, 不改原行), 容忍空输入', () => {
  const rows = [baseRow({ dividend_rate: 5, pe: 8 }), baseRow({ pe: -5 })];
  const out = withPayoutRate(rows);
  assert.equal(out[0].payout_rate, 40);
  assert.equal(out[1].payout_rate, null);
  assert.equal('payout_rate' in rows[0], false); // 原行不加键(纯函数)
  assert.equal(out[0].dividend_rate, 5); // 其余字段透传
  assert.deepEqual(withPayoutRate([]), []);
  assert.deepEqual(withPayoutRate(null), []);
});

// ---------------------------------------------------------------------------
// matchStock / filterRows(筛选语义)
// ---------------------------------------------------------------------------

test('空表单/缺省 form: 全部行命中(含 null 字段行)', () => {
  const rows = [baseRow(), baseRow({ stock_id: '000002', pe: null, dividend_rate: null })];
  assert.equal(matchStock(rows[0], emptyForm()), true);
  assert.equal(matchStock(rows[1], emptyForm()), true);
  assert.equal(matchStock(rows[1], null), true); // 缺省按空表单
  assert.equal(filterRows(rows, emptyForm()).length, 2);
});

test('市场筛选: sh 只留沪市; 北交所行(market=null)在市场启用时被剔除', () => {
  const rows = [
    baseRow({ stock_id: '600001' }),
    baseRow({ stock_id: '000002' }),
    baseRow({ stock_id: '830799' }), // 北交所 → marketOf=null
  ];
  const ids = filterRows(rows, formWith({ markets: ['sh'] })).map((r) => r.stock_id);
  assert.deepEqual(ids, ['600001']);
});

test('市场筛选: 同时勾选 sh+sz 时北交所仍被剔除', () => {
  const rows = [baseRow({ stock_id: '600001' }), baseRow({ stock_id: '830799' })];
  const ids = filterRows(rows, formWith({ markets: ['sh', 'sz'] })).map((r) => r.stock_id);
  assert.deepEqual(ids, ['600001']);
});

test('行业筛选(多选): 任一选中前缀命中即过(OR), 含整棵子树', () => {
  const row = baseRow({ sw_cd: '801160' });
  assert.equal(matchStock(row, formWith({ industries: ['80'] })), true); // 一级
  assert.equal(matchStock(row, formWith({ industries: ['8011'] })), true); // 二级
  assert.equal(matchStock(row, formWith({ industries: ['801160'] })), true); // 三级
  assert.equal(matchStock(row, formWith({ industries: ['90'] })), false); // 其他一级行业
  // 多选 OR: 90(不中) + 8011(中) → 过
  assert.equal(matchStock(row, formWith({ industries: ['90', '8011'] })), true);
  assert.equal(matchStock(row, formWith({ industries: ['90', '11'] })), false);
});

test('行业筛选: sw_cd 为 null 的行被拒(防御, 与集思录空行业行一致)', () => {
  const row = baseRow({ sw_cd: null });
  assert.equal(matchStock(row, formWith({ industries: ['80'] })), false);
});

test('排除行业(多选): 任一选中前缀命中(任意层级含子树)即剔除, 空串不过滤', () => {
  const row = baseRow({ sw_cd: '801160' }); // 交通运输-铁路公路-铁路运输
  assert.equal(matchStock(row, formWith({ excludeIndustries: ['80'] })), false); // 一级整棵子树
  assert.equal(matchStock(row, formWith({ excludeIndustries: ['8011'] })), false); // 二级
  assert.equal(matchStock(row, formWith({ excludeIndustries: ['801160'] })), false); // 三级
  assert.equal(matchStock(row, formWith({ excludeIndustries: ['90'] })), true); // 其他行业不受影响
  // 多选: 只要一个命中即剔
  assert.equal(matchStock(row, formWith({ excludeIndustries: ['90', '80'] })), false);
  assert.equal(matchStock(row, formWith({ excludeIndustries: ['90', '11'] })), true);
  // sw_cd 为空的行不受排除影响(与「行业」包含条件的拒空行为相反)
  assert.equal(matchStock(baseRow({ sw_cd: null }), formWith({ excludeIndustries: ['80'] })), true);
});

test('行业与排除行业可并用: 先含后除', () => {
  const rows = [
    baseRow({ stock_id: 'S1', sw_cd: '801160' }), // 交通运输-铁路运输
    baseRow({ stock_id: 'S2', sw_cd: '801170' }), // 交通运输-高铁
    baseRow({ stock_id: 'S3', sw_cd: '110000' }), // 能源
  ];
  // 含 [80, 11] 且排 [8011] → 只剩能源
  assert.deepEqual(
    filterRows(rows, formWith({ industries: ['80', '11'], excludeIndustries: ['8011'] })).map((r) => r.stock_id),
    ['S3'],
  );
  // 含 [80] 且排 [11] → 留交通运输两只
  assert.deepEqual(
    filterRows(rows, formWith({ industries: ['80'], excludeIndustries: ['11'] })).map((r) => r.stock_id),
    ['S1', 'S2'],
  );
});

test('省份筛选(多选): 任一精确命中即过(OR); 启用时 null 省份行被剔除', () => {
  assert.equal(matchStock(baseRow(), formWith({ provinces: ['北京'] })), true);
  assert.equal(matchStock(baseRow({ province: '上海' }), formWith({ provinces: ['北京'] })), false);
  assert.equal(matchStock(baseRow({ province: '上海' }), formWith({ provinces: ['北京', '上海'] })), true);
  assert.equal(matchStock(baseRow({ province: null }), formWith({ provinces: ['北京'] })), false);
  assert.equal(matchStock(baseRow({ province: null }), formWith({ provinces: [] })), true);
});

test('仅国资筛选: soeOnly 启用时留有 enterprise_nature 的行, 空/缺失被剔除', () => {
  assert.equal(matchStock(baseRow({ enterprise_nature: '中央国有企业' }), formWith({ soeOnly: true })), true);
  assert.equal(matchStock(baseRow({ enterprise_nature: '地方国有企业' }), formWith({ soeOnly: true })), true);
  assert.equal(matchStock(baseRow({ enterprise_nature: '' }), formWith({ soeOnly: true })), false); // 未命中名单
  assert.equal(matchStock(baseRow({ enterprise_nature: null }), formWith({ soeOnly: true })), false);
  assert.equal(matchStock(baseRow({}), formWith({ soeOnly: true })), false); // 键缺失
  assert.equal(matchStock(baseRow({}), formWith({ soeOnly: false })), true); // 未启用全过
  assert.equal(matchStock(baseRow({}), emptyForm()), true);
});

test('上限筛选: 阈值启用时 null 字段行被过滤(对齐 SQL NULL 比较恒假)', () => {
  const nullPe = baseRow({ pe: null });
  assert.equal(matchStock(nullPe, formWith({ peMax: 10 })), false);
  assert.equal(matchStock(nullPe, emptyForm()), true); // 未启用时保留
  // 其余上限同类抽查
  assert.equal(matchStock(baseRow({ pb: null }), formWith({ pbMax: 1 })), false);
  assert.equal(matchStock(baseRow({ pe_temperature: null }), formWith({ peTMax: 50 })), false);
  assert.equal(matchStock(baseRow({ pb_temperature: null }), formWith({ pbTMax: 50 })), false);
  assert.equal(matchStock(baseRow({ int_debt_rate: null }), formWith({ intDebtMax: 30 })), false);
});

test('上限筛选: 边界闭区间(值=阈值命中), 超阈值不命中', () => {
  assert.equal(matchStock(baseRow({ pe: 10 }), formWith({ peMax: 10 })), true);
  assert.equal(matchStock(baseRow({ pe: 10.01 }), formWith({ peMax: 10 })), false);
});

test('负 PE 正常参与 ≤ 比较: PE=-5 在 PE≤10 下命中, 在 PE≤-10 下不命中', () => {
  assert.equal(matchStock(baseRow({ pe: -5 }), formWith({ peMax: 10 })), true);
  assert.equal(matchStock(baseRow({ pe: -5 }), formWith({ peMax: -10 })), false);
});

test('下限筛选: 阈值启用时 null 字段行被过滤, 边界闭区间', () => {
  assert.equal(matchStock(baseRow({ dividend_rate: null }), formWith({ dividendMin: 3 })), false);
  assert.equal(matchStock(baseRow({ dividend_rate: 3 }), formWith({ dividendMin: 3 })), true);
  assert.equal(matchStock(baseRow({ dividend_rate: 2.99 }), formWith({ dividendMin: 3 })), false);
  // 其余下限同类抽查(5年平均类与增长类)
  assert.equal(matchStock(baseRow({ roe: null }), formWith({ roeMin: 5 })), false);
  assert.equal(matchStock(baseRow({ roe_average: null }), formWith({ roeAverageMin: 5 })), false);
  assert.equal(matchStock(baseRow({ revenue_average: null }), formWith({ revenueAvgMin: 0 })), false);
  assert.equal(matchStock(baseRow({ profit_average: null }), formWith({ profitAvgMin: 0 })), false);
  assert.equal(matchStock(baseRow({ cashflow_average: null }), formWith({ cashflowAvgMin: 0 })), false);
  assert.equal(matchStock(baseRow({ eps_growth_ttm: null }), formWith({ epsGrowthTtmMin: 0 })), false);
});

test('分红率筛选(派生指标): payoutMin 按股息率×PE 判定, 亏损/缺数行在启用时被滤', () => {
  // baseRow 基准: 股息率 5 × PE 8.5 = 42.5
  assert.equal(matchStock(baseRow(), formWith({ payoutMin: 42.5 })), true); // 恰阈值(闭区间)
  assert.equal(matchStock(baseRow(), formWith({ payoutMin: 43 })), false);
  assert.equal(matchStock(baseRow({ pe: 20 }), formWith({ payoutMin: 60 })), true); // 5×20=100 过
  assert.equal(matchStock(baseRow({ pe: 20 }), formWith({ payoutMin: 101 })), false);
  // 亏损(PE≤0)与缺数 → 无值: 启用时被滤, 未启用保留
  assert.equal(matchStock(baseRow({ pe: -5 }), formWith({ payoutMin: 40 })), false);
  assert.equal(matchStock(baseRow({ pe: null }), formWith({ payoutMin: 40 })), false);
  assert.equal(matchStock(baseRow({ dividend_rate: null }), formWith({ payoutMin: 40 })), false);
  assert.equal(matchStock(baseRow({ pe: -5 }), emptyForm()), true);
  // 物化过的行(withPayoutRate)同样按派生值判定
  assert.equal(matchStock(withPayoutRate([baseRow()])[0], formWith({ payoutMin: 42.5 })), true);
});

test('负增长在下限 0 下不命中, 负下限可命中负增长行', () => {
  assert.equal(matchStock(baseRow({ eps_growth_ttm: -3.5 }), formWith({ epsGrowthTtmMin: 0 })), false);
  assert.equal(matchStock(baseRow({ eps_growth_ttm: -3.5 }), formWith({ epsGrowthTtmMin: -5 })), true);
});

test('温度负值仅显示层隐藏: 筛选照常参与(PE温度=-5 在 ≤10 下命中)', () => {
  assert.equal(matchStock(baseRow({ pe_temperature: -5 }), formWith({ peTMax: 10 })), true);
  assert.equal(matchStock(baseRow({ pe_temperature: -5 }), formWith({ peTMax: -10 })), false);
  assert.equal(tempText(-5), '—'); // 显示层才是 '—'
});

test('市值区间: 单边=开区间, 双边闭区间, min>max 按字面空集', () => {
  const row = baseRow({ total_value: 300, float_value: 280 });
  assert.equal(matchStock(row, formWith({ totalValueMin: 300 })), true); // v=min 命中
  assert.equal(matchStock(row, formWith({ totalValueMax: 300 })), true); // v=max 命中
  assert.equal(matchStock(row, formWith({ totalValueMin: 301 })), false);
  assert.equal(matchStock(row, formWith({ totalValueMax: 299 })), false);
  assert.equal(matchStock(row, formWith({ totalValueMin: 200, totalValueMax: 400 })), true);
  // min > max: 字面空集
  assert.equal(matchStock(row, formWith({ totalValueMin: 400, totalValueMax: 200 })), false);
  // 流通市值独立区间
  assert.equal(matchStock(row, formWith({ floatValueMin: 300 })), false);
  assert.equal(matchStock(row, formWith({ floatValueMax: 285 })), true);
});

test('市值区间: 区间启用时 null 字段行被过滤', () => {
  const row = baseRow({ total_value: null });
  assert.equal(matchStock(row, formWith({ totalValueMin: 100 })), false);
  assert.equal(matchStock(row, formWith({ totalValueMax: 100 })), false);
  assert.equal(matchStock(row, emptyForm()), true);
});

test('多条件 AND 合取: 任一不满足即剔除', () => {
  const rows = [
    baseRow({ stock_id: '600001', pe: 8, dividend_rate: 5, province: '北京' }),
    baseRow({ stock_id: '600002', pe: 12, dividend_rate: 5, province: '北京' }), // PE 超
    baseRow({ stock_id: '600003', pe: 8, dividend_rate: 3, province: '北京' }), // 股息不足
    baseRow({ stock_id: '600004', pe: 8, dividend_rate: 5, province: '上海' }), // 省份不符
    baseRow({ stock_id: '000005', pe: 8, dividend_rate: 5, province: '北京' }), // 深市
  ];
  const ids = filterRows(rows, formWith({
    markets: ['sh'], provinces: ['北京', '天津'], peMax: 10, dividendMin: 4,
  })).map((r) => r.stock_id);
  assert.deepEqual(ids, ['600001']);
});

test('filterRows: 纯函数不改输入数组与行对象', () => {
  const rows = [baseRow(), baseRow({ stock_id: '000002' })];
  const snapshot = JSON.parse(JSON.stringify(rows));
  filterRows(rows, formWith({ peMax: 10 }));
  assert.deepEqual(rows, snapshot);
});

// ---------------------------------------------------------------------------
// buildIndustryTree / collectProvinceOptions
// ---------------------------------------------------------------------------

test('buildIndustryTree: 按 sw_cd 2/4/6 位分层, label 取 industry_nm2 对应段', () => {
  const tree = buildIndustryTree([
    baseRow({ sw_cd: '801160', industry_nm2: '交通运输-铁路公路-铁路运输' }),
    baseRow({ sw_cd: '801170', industry_nm2: '交通运输-铁路公路-高铁' }),
    baseRow({ sw_cd: '110000', industry_nm2: '能源-煤炭-动煤' }),
  ]);
  assert.deepEqual(tree.map((n) => n.value), ['110000'.slice(0, 2), '801160'.slice(0, 2)].sort()); // ['11','80'] 字典序
  const jiaotong = tree.find((n) => n.value === '80');
  assert.equal(jiaotong.label, '交通运输');
  const lv2 = jiaotong.children[0];
  assert.equal(lv2.value, '8011');
  assert.equal(lv2.label, '铁路公路');
  assert.deepEqual(lv2.children.map((c) => c.value), ['801160', '801170']);
  assert.equal(lv2.children[0].label, '铁路运输');
  assert.equal(lv2.children[1].label, '高铁');
});

test('buildIndustryTree: 三级 label 缺失回退 industry_nm; 一级回退码本身; 4位码无三级', () => {
  const tree = buildIndustryTree([
    baseRow({ sw_cd: '801160', industry_nm2: '', industry_nm: '铁路运输' }),
    baseRow({ sw_cd: '901020', industry_nm2: '医药-化学制药' , industry_nm: '化学制药' }), // nm2 仅两段
    baseRow({ sw_cd: '9020', industry_nm2: '医药-中药', industry_nm: '中药' }), // sw_cd 仅 4 位
  ]);
  const yi = tree.find((n) => n.value === '90');
  assert.equal(yi.label, '医药');
  const huaxue = yi.children.find((n) => n.value === '9010');
  assert.equal(huaxue.label, '化学制药');
  assert.deepEqual(huaxue.children.map((c) => c.value), ['901020']);
  assert.equal(huaxue.children[0].label, '化学制药'); // 三级段缺失回退 industry_nm

  const zhongyao = yi.children.find((n) => n.value === '9020');
  assert.equal(zhongyao.label, '中药');
  assert.deepEqual(zhongyao.children, []); // 4 位码不产生三级节点

  const jiaotong = tree.find((n) => n.value === '80');
  assert.equal(jiaotong.label, '80'); // 一级段缺失回退码本身
  const third = jiaotong.children[0].children[0];
  assert.equal(third.label, '铁路运输'); // 三级段缺失回退 industry_nm
});

test('buildIndustryTree: sw_cd 为空/null 的行不进树; 空输入得 []', () => {
  assert.deepEqual(buildIndustryTree([baseRow({ sw_cd: '' }), baseRow({ sw_cd: null })]), []);
  assert.deepEqual(buildIndustryTree([]), []);
  assert.deepEqual(buildIndustryTree(null), []);
});

test('collectProvinceOptions: 非空去重 + 中文排序, 容忍空行', () => {
  const opts = collectProvinceOptions([
    baseRow({ province: '浙江' }),
    baseRow({ province: '北京' }),
    baseRow({ province: '浙江' }),
    baseRow({ province: '' }),
    baseRow({ province: null }),
    baseRow({ province: undefined }),
  ]);
  assert.deepEqual(opts, ['北京', '浙江'].sort((a, b) => a.localeCompare(b, 'zh')));
  assert.deepEqual(collectProvinceOptions([]), []);
});

// ---------------------------------------------------------------------------
// sortRows
// ---------------------------------------------------------------------------

test('sortRows: 降序 + null 恒沉底(升序也沉底) + stock_id 升序 tie-break', () => {
  const rows = [
    baseRow({ stock_id: 'S4', dividend_rate: null }),
    baseRow({ stock_id: 'S2', dividend_rate: 5.0 }),
    baseRow({ stock_id: 'S1', dividend_rate: 9.1 }),
    baseRow({ stock_id: 'S3', dividend_rate: 5.0 }),
  ];
  assert.deepEqual(
    sortRows(rows, { prop: 'dividend_rate', order: 'descending' }).map((r) => r.stock_id),
    ['S1', 'S2', 'S3', 'S4'],
  );
  assert.deepEqual(
    sortRows(rows, { prop: 'dividend_rate', order: 'ascending' }).map((r) => r.stock_id),
    ['S2', 'S3', 'S1', 'S4'], // null 仍沉底
  );
});

test('sortRows: 行业列按 sw_cd 排序(sortKey), 文本列用中文 locale', () => {
  const rows = [
    baseRow({ stock_id: 'S1', sw_cd: '901020' }),
    baseRow({ stock_id: 'S2', sw_cd: '801160' }),
  ];
  assert.deepEqual(
    sortRows(rows, { prop: 'industry_nm', order: 'ascending' }).map((r) => r.stock_id),
    ['S2', 'S1'],
  );
});

test('sortRows: 纯函数不改输入; 无排序条件返回浅拷贝', () => {
  const rows = [baseRow({ dividend_rate: 1 }), baseRow({ dividend_rate: 2 })];
  const snapshot = JSON.parse(JSON.stringify(rows));
  const desc = sortRows(rows, DEFAULT_SORT);
  assert.deepEqual(rows, snapshot);
  assert.notEqual(desc, rows); // 新数组
  assert.equal(sortRows(rows, null)[0].dividend_rate, 1); // 无条件原样拷贝
  assert.deepEqual(sortRows(null, DEFAULT_SORT), []);
});

// ---------------------------------------------------------------------------
// 格式化 / 色阶
// ---------------------------------------------------------------------------

test('fmtNum: null/undefined/NaN/Infinity →「—」, 正常值 toFixed', () => {
  assert.equal(fmtNum(null), '—');
  assert.equal(fmtNum(undefined), '—');
  assert.equal(fmtNum(NaN), '—');
  assert.equal(fmtNum(Infinity), '—');
  assert.equal(fmtNum('abc'), '—');
  assert.equal(fmtNum(3.14159), '3.14');
  assert.equal(fmtNum(3.14159, 3), '3.142');
  assert.equal(fmtNum(-5), '-5.00');
  assert.equal(fmtNum(0), '0.00');
});

test('fmtVolume: 千分位取整, 空/非法 →「—」', () => {
  assert.equal(fmtVolume(null), '—');
  assert.equal(fmtVolume(NaN), '—');
  assert.equal(fmtVolume(123456.7), '123,457');
  assert.equal(fmtVolume(1234567), '1,234,567');
  assert.equal(fmtVolume(0), '0');
});

test('tempText: 负值/null/非法 →「—」, 非负两位小数', () => {
  assert.equal(tempText(null), '—');
  assert.equal(tempText(undefined), '—');
  assert.equal(tempText(NaN), '—');
  assert.equal(tempText(-0.01), '—');
  assert.equal(tempText(-100), '—');
  assert.equal(tempText(0), '0.00');
  assert.equal(tempText(24.999), '25.00');
  assert.equal(tempText(74.5), '74.50');
});

test('tempClass: <25 青 / [25,50) 绿 / [50,75) 橙 / ≥75 红(边界档位)', () => {
  assert.equal(tempClass(null), '');
  assert.equal(tempClass(-1), '');
  assert.equal(tempClass(0), 't-cyan');
  assert.equal(tempClass(24.99), 't-cyan');
  assert.equal(tempClass(25), 't-green'); // 恰 25 → 绿
  assert.equal(tempClass(49.99), 't-green');
  assert.equal(tempClass(50), 't-orange'); // 恰 50 → 橙
  assert.equal(tempClass(74.99), 't-orange');
  assert.equal(tempClass(75), 't-red'); // 恰 75 → 红
  assert.equal(tempClass(100), 't-red');
});

test('signedText: 正数带 +, 负数原样, 0 无符号, 空 →「—」', () => {
  assert.equal(signedText(null), '—');
  assert.equal(signedText(NaN), '—');
  assert.equal(signedText(1.5), '+1.50');
  assert.equal(signedText(-1.5), '-1.50');
  assert.equal(signedText(0), '0.00');
  assert.equal(signedText(2.456, 1), '+2.5');
});

test('signedClass: 涨红跌绿, 0/空不着色', () => {
  assert.equal(signedClass(null), '');
  assert.equal(signedClass(NaN), '');
  assert.equal(signedClass(0), '');
  assert.equal(signedClass(0.01), 'up');
  assert.equal(signedClass(-0.01), 'down');
});
