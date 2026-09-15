<script setup>
import { ref, computed, watch } from 'vue';
import { eastmoneyF10Url } from '../../utils/eastmoney.mjs';
import { getCbDiscussion, warmCbDiscussions, getCbAdjustment } from '../../api/index.js';
const props=defineProps({result:Object,stale:Boolean});
const emit=defineEmits(['blacklist']);
const view=ref('all'),search=ref(''),page=ref(1),size=ref(50),sort=ref({prop:'rank',order:'ascending'});
const storageKey='cb-selection-results-preferences';
const columns=[
 {field:'code',label:'代码',width:68,default:true,align:'center',fixed:'left',sortable:false},
 {field:'name',label:'名称',width:128,default:true,fixed:'left',sortable:false},
 {field:'rank',label:'排名',width:58,default:true,align:'center',fixed:'left',sortable:true},
 {field:'stock_nm',label:'正股',width:106,default:true,align:'left',fixed:'left',sortable:false},
 {field:'industry_name',label:'行业',width:92,default:true,sortable:false},
 {field:'rating',label:'评级',width:62,default:true,align:'center',sortable:false},
 {field:'sprice',label:'正股价',width:70,default:true,align:'right',sortable:true},
 {field:'price',label:'价格',width:68,default:true,align:'right',sortable:true},
 {field:'premium_rt',label:'溢价',width:68,default:true,align:'right',sortable:true},
 {field:'simple_maturity_yield_pct',label:'到期收益',width:82,default:true,align:'right',sortable:true},
 {field:'dblow',label:'双低',width:68,default:true,align:'right',sortable:true},
 {field:'convert_value',label:'转股价值',width:82,default:true,align:'right',sortable:true},
 {field:'convert_price',label:'转股价',width:78,optional:true,align:'right',sortable:true},
 {field:'year_left',label:'剩余年限',width:78,default:true,align:'right',sortable:true},
 {field:'curr_iss_amt',label:'规模(亿)',width:78,optional:true,align:'right',sortable:true},
 {field:'pb',label:'市净率',width:70,optional:true,align:'right',sortable:true},
 {field:'redeem',label:'强赎',width:78,default:true,align:'center',sortable:false},
 {field:'stock_financial_profit',label:'利润指标',width:82,default:true,align:'right',sortable:true},
 {field:'redeem_price',label:'赎回价',width:74,optional:true,align:'right',sortable:true},
 {field:'total_score',label:'得分',width:62,optional:true,align:'right',sortable:true},
];
const defaultFields=columns.filter(c=>c.default).map(c=>c.field);
const requiredFields=['code','name','rank','stock_nm'];
const saved=(()=>{try{return JSON.parse(localStorage.getItem(storageKey)||'{}')}catch{return {}}})();
const density=ref(saved.density==='comfortable'?'comfortable':'compact');
const savedFields=Array.isArray(saved.columns)?saved.columns.filter(f=>columns.some(c=>c.field===f)):[];
const selectedFields=ref(savedFields.length?[...requiredFields,...savedFields.filter(f=>!requiredFields.includes(f))].filter((f,i,a)=>a.indexOf(f)===i):defaultFields);
const visibleColumns=computed(()=>selectedFields.value.map(f=>columns.find(c=>c.field===f)).filter(Boolean));
function persist(){localStorage.setItem(storageKey,JSON.stringify({density:density.value,columns:selectedFields.value}));}
function resetColumns(){selectedFields.value=[...defaultFields];persist();}
function toggleColumn(field){if(requiredFields.includes(field))return;if(selectedFields.value.includes(field))selectedFields.value=selectedFields.value.filter(f=>f!==field);else selectedFields.value=[...selectedFields.value,field];persist();}
watch(density,persist);
// 列序: 标识(排名/代码/名称/行业/评级)→股侧(正股名称/正股)→核心五联(价格/溢价/收益率/年限/规模相邻)
// →转换链(转股价/转股价值)→打分(双低)→债性(赎回价)→属性(市净率/强赎)→得分。
// 重点列标题着色: 溢价/收益率是选债核心输出, 表头橙加粗突出; 价格列数值加粗。
const hlHeads=new Set(['premium_rt','simple_maturity_yield_pct']);
const numberFields=new Set(['price','sprice','redeem_price','simple_maturity_yield_pct','dblow','premium_rt','curr_iss_amt','convert_price','convert_value','year_left','pb','total_score','stock_financial_profit']);
const redeemBadges={'NO_REDEEM_ANNOUNCED':{text:'不强赎',cls:'b-green'},'TRIGGER_MET':{text:'已满足',cls:'b-blue'},'ANNOUNCED_INTENT':{text:'拟强赎',cls:'b-orange'},'ANNOUNCED_REDEEM':{text:'已强赎',cls:'b-red'},'NEAR_MATURITY':{text:'临期',cls:'b-red'}};
const badge=row=>redeemBadges[row?.redeem_state?.status_code];
const soeFlag=row=>({'中央国有企业':{t:'央',c:'soe-central'},'地方国有企业':{t:'国',c:'soe-local'}})[row?.enterprise_nature];
function display(row,field){if(field==='stock_financial_profit'){const v=row.stock_financial?.profit_average;return v==null?'—':`${Number(v).toFixed(2)}%`;}const v=row[field];if(v==null||v==='')return field==='rating'?'无评级':'—';return numberFields.has(field)?`${Number(v).toFixed(2)}${['simple_maturity_yield_pct','premium_rt','stock_financial_profit'].includes(field)?'%':''}`:v;}
function sortValue(row,field){return field==='stock_financial_profit'?row.stock_financial?.profit_average:row[field];}
function sortBy(field,order='ascending'){sort.value={prop:field,order};}
const rows=computed(()=>{
 let a=view.value==='excluded'?props.result.excluded_rows||[]:props.result.rows||[];
 const q=search.value.trim().toLowerCase();a=a.filter(r=>!q||`${r.code} ${r.name}`.toLowerCase().includes(q));
 const {prop,order}=sort.value;
 return [...a].sort((x,y)=>{const av=sortValue(x,prop),bv=sortValue(y,prop);if(av==null)return bv==null?0:1;if(bv==null)return -1;const c=typeof av==='number'?av-bv:String(av).localeCompare(String(bv));return (order==='descending'?-c:c)||((x.rank||0)-(y.rank||0));});
});
defineExpose({toggleColumn,resetColumns,sortValue,sortBy,rows});
const paged=computed(()=>rows.value.slice((page.value-1)*size.value,page.value*size.value));
watch([view,search,size,()=>props.result],()=>{page.value=1;});
watch(()=>props.result,()=>{view.value='all';});
const reasons=row=>(row.exclude_reasons||[]).map(r=>typeof r==='string'?r:r.message).join('；');
// 「相关讨论」悬浮展示(集思录详情页按需代理): 当前页批量预热 + 悬浮惰性兜底。
// disc 形如 {code:{status:'loading'|'ok'|'error',items:[...]}}; 跨筛选结果保留(讨论只跟代码走)。
const disc=ref({});
function ensureDiscussion(code){
 if(!code)return;
 const cur=disc.value[code];
 if(cur&&cur.status!=='error')return; // loading/ok 不重复请求; error 允许重试
 disc.value={...disc.value,[code]:{status:'loading',items:[]}};
 getCbDiscussion(code).then((d)=>{disc.value={...disc.value,[code]:{status:'ok',items:d.items||[]}};})
  .catch(()=>{disc.value={...disc.value,[code]:{status:'error',items:[]}};});
}
async function warmDiscussions(){
 const codes=[...new Set(paged.value.map((r)=>r.code).filter(Boolean))];
 const missing=codes.filter((c)=>{const s=disc.value[c];return !s||s.status==='error';});
 if(!missing.length)return;
 try{
  const d=await warmCbDiscussions(missing.slice(0,100));
  const next={...disc.value};
  for(const [c,items] of Object.entries(d.items||{}))next[c]={status:'ok',items};
  disc.value=next;
 }catch{/* 预热失败静默: 悬浮时 ensureDiscussion 惰性兜底 */}
}
watch(paged,warmDiscussions,{immediate:true});
// 转股价「下修记录」点击弹窗(集思录 adj_logs 公开接口按需代理): 仅下修过(adj_scnt>0)
// 的行可点, 惰性拉取, 失败可重试; adj 形如 {code:{status:'loading'|'ok'|'error',items:[...]}}。
const adj=ref({});
function ensureAdjustment(code){
 if(!code)return;
 const cur=adj.value[code];
 if(cur&&cur.status!=='error')return; // loading/ok 不重复请求; error 允许重试
 adj.value={...adj.value,[code]:{status:'loading',items:[]}};
 getCbAdjustment(code).then((d)=>{adj.value={...adj.value,[code]:{status:'ok',items:d.items||[]}};})
  .catch(()=>{adj.value={...adj.value,[code]:{status:'error',items:[]}};});
}
</script>
<template>
 <section class="results">
  <el-alert v-if="stale" title="条件或数据源已修改，当前为上次结果，请重新筛选" type="warning" :closable="false"/>
  <el-alert v-if="result.meta?.data_status==='no_snapshot'" title="尚无行情快照，请前往数据管理同步或改用实时行情" type="info" :closable="false"/>
  <el-alert v-for="w in result.meta?.warnings||[]" :key="w" :title="w" type="warning" :closable="false"/>
  <p class="meta">{{ result.source==='live'?'实时行情':'数据库快照' }} · 行情日期 {{ result.meta?.trade_date || (result.source==='live' ? '实时' : '未确认') }} · 赎回日期 {{ result.meta?.redeem_trade_date || (result.source==='live' ? '实时' : '未确认') }}<span v-if="result.meta?.fetched_at"> · 请求时间 {{ result.meta.fetched_at }}</span></p>
  <div class="tools"><el-radio-group v-model="view"><el-radio-button value="all">全部符合</el-radio-button><el-radio-button value="excluded">排除明细</el-radio-button></el-radio-group><el-input v-model="search" placeholder="当前结果内查找代码/名称" clearable/><el-button @click="sort={prop:'rank',order:'ascending'}">恢复默认排序</el-button><el-button @click="density=density==='compact'?'comfortable':'compact'">{{ density==='compact'?'舒适密度':'紧凑密度' }}</el-button><el-dropdown trigger="click" :hide-on-click="false"><el-button>列设置</el-button><template #dropdown><el-dropdown-menu><div class="column-settings"><div class="column-group-title">默认列</div><label v-for="c in columns.filter(x=>x.default)" :key="c.field"><el-checkbox :model-value="selectedFields.includes(c.field)" :disabled="requiredFields.includes(c.field)">{{ c.label }}</el-checkbox></label><div class="column-group-title optional-title">可选列</div><label v-for="c in columns.filter(x=>x.optional)" :key="c.field"><el-checkbox :model-value="selectedFields.includes(c.field)" @change="toggleColumn(c.field)">{{ c.label }}</el-checkbox></label><el-button link type="primary" @click="resetColumns">恢复默认</el-button></div></el-dropdown-menu></template></el-dropdown></div>
  <el-table :class="['density-'+density]" :data="paged" stripe highlight-current-row max-height="620" @sort-change="sort=$event.prop&&$event.order?$event:{prop:'rank',order:'ascending'}">
   <el-table-column v-for="c in visibleColumns" :key="c.field" :prop="c.field" :label="c.label" :width="c.width" :min-width="c.min" :fixed="c.fixed||false" :align="c.align||'left'" :sortable="c.sortable?'custom':false" :show-overflow-tooltip="c.field!=='name'">
    <template #header><el-tooltip v-if="c.field==='simple_maturity_yield_pct'" content="(到期赎回价－当前价格) / 当前价格 ×100；未年化，不含票息和税"><span :class="{'hl-head':hlHeads.has(c.field)}">{{c.label}} ⓘ</span></el-tooltip><el-tooltip v-else-if="c.field==='stock_financial_profit'" content="来源：集思录高股息字段 profit_average；仅展示，不参与筛选或因子计算"><span>{{c.label}} ⓘ</span></el-tooltip><span v-else :class="{'hl-head':hlHeads.has(c.field)}">{{c.label}}</span></template>
    <template #default="{row}"><el-tooltip v-if="c.field==='industry_name'&&row.industry_is_fallback" :content="`使用申万 ${row.industry_level} 级回退映射，原始码 ${row.industry_code}`"><span>{{ display(row,c.field) }} ⓘ</span></el-tooltip><a v-else-if="c.field==='code'&&row.code" class="ext-link" :href="`https://www.jisilu.cn/data/convert_bond_detail/${row.code}`" target="_blank" rel="noopener">{{ display(row,c.field) }}</a><a v-else-if="c.field==='stock_nm'&&eastmoneyF10Url(row.stock_id)" class="ext-link" :href="eastmoneyF10Url(row.stock_id)" target="_blank" rel="noopener">{{ display(row,c.field) }}<span v-if="soeFlag(row)" class="soe-badge" :class="soeFlag(row).c" :title="row.enterprise_nature">{{ soeFlag(row).t }}</span></a><el-popover v-else-if="c.field==='name'" trigger="hover" placement="top" :width="300" :show-after="200" @show="ensureDiscussion(row.code)"><template #reference><span class="name-cell" :title="display(row,c.field)">{{ display(row,c.field) }}<span v-if="badge(row)" class="redeem-badge" :class="badge(row).cls" :title="row.redeem_state?.status_label">{{ badge(row).text }}</span></span></template><div v-if="disc[row.code]?.status!=='ok'" class="disc-tip">{{ disc[row.code]?.status==='error'?'加载失败，稍后重试':'讨论加载中…' }}</div><ul v-else-if="disc[row.code].items.length" class="disc-list"><li v-for="t in disc[row.code].items" :key="t.url"><a class="disc-link" :href="t.url" target="_blank" rel="noopener">{{ t.title }}</a><span class="disc-meta">{{ t.replies }} · {{ t.views }}<template v-if="t.date"> · {{ t.date }}</template></span></li></ul><div v-else class="disc-tip">暂无相关讨论</div></el-popover><el-popover v-else-if="c.field==='convert_price'&&row.adj_scnt>0" trigger="click" placement="top" :width="400" @show="ensureAdjustment(row.code)"><template #reference><span class="cp-cell" title="点击查看下修记录">{{ display(row,c.field) }}<span class="adj-stars">{{ '*'.repeat(row.adj_scnt) }}</span></span></template><div class="adj-cap">转股价下修记录 · {{ row.name }}</div><div v-if="adj[row.code]?.status!=='ok'" class="adj-tip">{{ adj[row.code]?.status==='error'?'加载失败，重新打开重试':'下修记录加载中…' }}</div><table v-else-if="adj[row.code].items.length" class="adj-tbl"><thead><tr><th>股东大会日</th><th>下修前</th><th>下修后</th><th>生效日</th><th>下修底价</th></tr></thead><tbody><tr v-for="t in adj[row.code].items" :key="t.meeting_date+t.effective_date"><td>{{ t.meeting_date }}</td><td class="num">{{ t.price_before?.toFixed(2)??'—' }}</td><td class="num down">{{ t.price_after?.toFixed(2)??'—' }}</td><td>{{ t.effective_date }}</td><td class="num">{{ t.floor_price?.toFixed(2)??'—' }}</td></tr></tbody></table><div v-else class="adj-tip">暂无下修记录</div></el-popover><span v-else :class="{'price-cell':c.field==='price'}">{{ display(row,c.field) }}</span></template>
   </el-table-column>
   <el-table-column v-if="view==='excluded'" label="排除原因" min-width="300"><template #default="{row}">{{reasons(row)}}</template></el-table-column>
   <el-table-column label="操作" width="88" fixed="right"><template #default="{row}"><el-button size="small" text @click="emit('blacklist',row)">加黑名单</el-button></template></el-table-column>
  </el-table>
  <div class="footer"><span class="counts">{{ result.total_all }} 全量 · {{ result.total_filtered }} 符合 · {{ result.total_excluded }} 排除</span><el-pagination v-model:current-page="page" v-model:page-size="size" :page-sizes="[20,50,100]" layout="prev,pager,next,sizes,total" :total="rows.length"/></div>
 </section>
</template>
<style scoped>
.results{min-width:0;background:var(--el-bg-color);padding:12px;border:1px solid var(--el-border-color-light);border-radius:8px}.tools{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.tools>.el-input{width:240px}.meta{font-size:12px;color:var(--el-text-color-secondary)}.el-alert{margin:6px 0}.footer{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;white-space:nowrap}.counts{font-size:12px;color:var(--el-text-color-secondary)}.footer :deep(.el-pagination){flex-wrap:nowrap}.results :deep(.el-table){width:100%;font-size:12px}.results :deep(.el-table .cell){font-variant-numeric:tabular-nums}.name-cell{font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.column-settings label{display:block}.column-group-title{font-size:12px;color:var(--el-text-color-secondary);margin:2px 0}.optional-title{margin-top:8px}.redeem-badge{margin-left:4px;padding:0 4px;border-radius:3px;font-size:10px;line-height:16px;font-weight:400;white-space:nowrap}.b-green{color:#388e3c;background:rgba(103,194,58,.12)}.b-blue{color:#409eff;background:rgba(64,158,255,.12)}.b-orange{color:#e6a23c;background:rgba(230,162,60,.14)}.b-red{color:#f56c6c;background:rgba(245,108,108,.12)}.soe-badge{margin-left:4px;padding:0 4px;border-radius:3px;font-size:10px;line-height:16px;font-weight:400;white-space:nowrap}.soe-central{color:#b03a2e;background:rgba(214,69,65,.12)}.soe-local{color:#409eff;background:rgba(64,158,255,.12)}.ext-link{color:#2563eb;text-decoration:none}html.dark .ext-link{color:#60a5fa}.ext-link:hover{text-decoration:underline}.disc-list{margin:0;padding:0;list-style:none;max-height:260px;overflow:auto}.disc-list li{padding:6px 0;border-bottom:1px solid var(--el-border-color-lighter)}.disc-list li:last-child{border-bottom:0}.disc-link{display:block;color:#2563eb;text-decoration:none;font-size:13px;line-height:1.4;word-break:break-all}.disc-link:hover{text-decoration:underline}html.dark .disc-link{color:#60a5fa}.disc-meta{display:block;margin-top:2px;font-size:11px;color:var(--el-text-color-secondary)}.disc-tip{padding:4px 0;font-size:12px;color:var(--el-text-color-secondary)}.cp-cell{cursor:pointer}.adj-stars{color:#f56c6c;font-weight:700;margin-left:2px;white-space:nowrap}.adj-cap{font-size:12px;color:var(--el-text-color-secondary);margin-bottom:6px}.adj-tip{padding:4px 0;font-size:12px;color:var(--el-text-color-secondary)}.adj-tbl{border-collapse:collapse;width:100%;font-size:12px}.adj-tbl th,.adj-tbl td{padding:4px 8px;border-bottom:1px solid var(--el-border-color-lighter);text-align:left;white-space:nowrap}.adj-tbl th{color:var(--el-text-color-secondary);font-weight:400}.adj-tbl tr:last-child td{border-bottom:0}.adj-tbl td.num{text-align:right;font-variant-numeric:tabular-nums}.adj-tbl td.down{color:#f56c6c;font-weight:600}.hl-head{color:var(--el-color-warning);font-weight:700}.price-cell{font-weight:600}.results :deep(.el-table__cell){padding:3px 0}.results :deep(.el-table .cell){padding:0 5px;line-height:24px}.density-comfortable :deep(.el-table__cell){padding:5px 0}.density-comfortable :deep(.el-table .cell){line-height:28px}
/* 当前行高亮用主题浅蓝(EP 默认 current-row 与悬浮灰同色起不到突出作用; 加一层类名压过斑马纹) */
.results :deep(.el-table__body .el-table__row.current-row>td.el-table__cell){background-color:var(--el-color-primary-light-8)}
 .results :deep(.el-table__header-wrapper th){height:34px}
@media (max-width:700px){.results{padding:8px}.tools>.el-input{width:100%}.footer{white-space:normal;align-items:flex-start}.footer :deep(.el-pagination){max-width:100%;overflow:auto}}
</style>
