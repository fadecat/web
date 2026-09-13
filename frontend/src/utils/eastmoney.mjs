// 东方财富 F10 个股页外链(财报道航 #/cpbd)
// 市场前缀: 沪市 60/68 开头 → SH, 深市 00/30 开头 → SZ;
// 其余(北交所等)无前缀映射, 返回 null, 调用方退回纯文本不加链接
export function eastmoneyF10Url(stockId) {
  if (!stockId) return null;
  const code = String(stockId);
  const prefix = code.startsWith('60') || code.startsWith('68') ? 'SH'
    : code.startsWith('00') || code.startsWith('30') ? 'SZ'
      : null;
  if (!prefix) return null;
  return `https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code=${prefix}${code}&color=b#/cpbd`;
}
