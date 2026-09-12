<script setup>
import { ref, computed, watch } from 'vue';
const props=defineProps({result:Object,stale:Boolean});
const emit=defineEmits(['blacklist']);
const view=ref('all'),search=ref(''),page=ref(1),size=ref(50),sort=ref({prop:'rank',order:'ascending'});
const columns=[{field:'rank',label:'排名',width:62},{field:'code',label:'代码',width:64},{field:'name',label:'名称',width:112},{field:'industry_name',label:'行业',width:96},{field:'rating',label:'评级',width:62},{field:'price',label:'价格',width:62},{field:'stock_nm',label:'正股名称',width:104},{field:'sprice',label:'正股',width:62},{field:'redeem_price',label:'赎回价',width:74},{field:'simple_maturity_yield_pct',label:'收益率',width:88},{field:'dblow',label:'双低',width:62},{field:'premium_rt',label:'溢价',width:62},{field:'curr_iss_amt',label:'规模(亿)',width:86},{field:'convert_value',label:'转股价值',width:86},{field:'year_left',label:'年限',width:62},{field:'pb',label:'市净率',width:74},{field:'redeem',label:'强赎',width:88},{field:'total_score',label:'得分',width:62}];
const numberFields=new Set(['price','sprice','redeem_price','simple_maturity_yield_pct','dblow','premium_rt','curr_iss_amt','convert_value','year_left','pb','total_score']);
const redeemBadges={'NO_REDEEM_ANNOUNCED':{text:'不强赎',cls:'b-green'},'TRIGGER_MET':{text:'已满足',cls:'b-blue'},'ANNOUNCED_INTENT':{text:'拟强赎',cls:'b-orange'},'ANNOUNCED_REDEEM':{text:'已强赎',cls:'b-red'},'NEAR_MATURITY':{text:'临期',cls:'b-red'}};
const badge=row=>redeemBadges[row?.redeem_state?.status_code];
const soeFlag=row=>({'中央国有企业':{t:'央',c:'soe-central'},'地方国有企业':{t:'国',c:'soe-local'}})[row?.enterprise_nature];
function display(row,field){const v=row[field];if(v==null||v==='')return field==='rating'?'无评级':'—';return numberFields.has(field)?`${Number(v).toFixed(2)}${['simple_maturity_yield_pct','premium_rt'].includes(field)?'%':''}`:v;}
const rows=computed(()=>{
 let a=view.value==='excluded'?props.result.excluded_rows||[]:props.result.rows||[];
 const q=search.value.trim().toLowerCase();a=a.filter(r=>!q||`${r.code} ${r.name}`.toLowerCase().includes(q));
 const {prop,order}=sort.value;
 return [...a].sort((x,y)=>{const av=x[prop],bv=y[prop];if(av==null)return bv==null?0:1;if(bv==null)return -1;const c=typeof av==='number'?av-bv:String(av).localeCompare(String(bv));return (order==='descending'?-c:c)||((x.rank||0)-(y.rank||0));});
});
const paged=computed(()=>rows.value.slice((page.value-1)*size.value,page.value*size.value));
watch([view,search,size,()=>props.result],()=>{page.value=1;});
watch(()=>props.result,()=>{view.value='all';});
const reasons=row=>(row.exclude_reasons||[]).map(r=>typeof r==='string'?r:r.message).join('；');
</script>
<template>
 <section class="results">
  <el-alert v-if="stale" title="条件或数据源已修改，当前为上次结果，请重新筛选" type="warning" :closable="false"/>
  <el-alert v-if="result.meta?.data_status==='no_snapshot'" title="尚无行情快照，请前往数据管理同步或改用实时行情" type="info" :closable="false"/>
  <el-alert v-for="w in result.meta?.warnings||[]" :key="w" :title="w" type="warning" :closable="false"/>
  <p class="meta">{{ result.source==='live'?'实时行情':'数据库快照' }} · 行情日期 {{ result.meta?.trade_date || (result.source==='live' ? '实时' : '未确认') }} · 赎回日期 {{ result.meta?.redeem_trade_date || (result.source==='live' ? '实时' : '未确认') }}<span v-if="result.meta?.fetched_at"> · 请求时间 {{ result.meta.fetched_at }}</span></p>
  <div class="tools"><el-radio-group v-model="view"><el-radio-button value="all">全部符合</el-radio-button><el-radio-button value="excluded">排除明细</el-radio-button></el-radio-group><el-input v-model="search" placeholder="当前结果内查找代码/名称" clearable/><el-button @click="sort={prop:'rank',order:'ascending'}">恢复默认排序</el-button></div>
  <el-table :data="paged" stripe max-height="620" @sort-change="sort=$event.prop&&$event.order?$event:{prop:'rank',order:'ascending'}">
   <el-table-column v-for="c in columns" :key="c.field" :prop="c.field" :label="c.label" :width="c.width" :min-width="c.min" :fixed="c.field==='name'?'left':false" :align="numberFields.has(c.field)?'right':'left'" sortable="custom" show-overflow-tooltip>
    <template #header><el-tooltip v-if="c.field==='simple_maturity_yield_pct'" content="(到期赎回价－当前价格) / 当前价格 ×100；未年化，不含票息和税"><span>{{c.label}} ⓘ</span></el-tooltip><span v-else>{{c.label}}</span></template>
    <template #default="{row}"><el-tooltip v-if="c.field==='industry_name'&&row.industry_is_fallback" :content="`使用申万 ${row.industry_level} 级回退映射，原始码 ${row.industry_code}`"><span>{{ display(row,c.field) }} ⓘ</span></el-tooltip><a v-else-if="c.field==='code'&&row.code" class="ext-link" :href="`https://www.jisilu.cn/data/convert_bond_detail/${row.code}`" target="_blank" rel="noopener">{{ display(row,c.field) }}</a><a v-else-if="c.field==='stock_nm'&&row.stock_id" class="ext-link" :href="`https://www.jisilu.cn/data/stock/${row.stock_id}`" target="_blank" rel="noopener">{{ display(row,c.field) }}<span v-if="soeFlag(row)" class="soe-badge" :class="soeFlag(row).c" :title="row.enterprise_nature">{{ soeFlag(row).t }}</span></a><span v-else-if="c.field==='name'" class="name-cell">{{ display(row,c.field) }}<span v-if="badge(row)" class="redeem-badge" :class="badge(row).cls" :title="row.redeem_state?.status_label">{{ badge(row).text }}</span></span><span v-else>{{ display(row,c.field) }}</span></template>
   </el-table-column>
   <el-table-column v-if="view==='excluded'" label="排除原因" min-width="300"><template #default="{row}">{{reasons(row)}}</template></el-table-column>
   <el-table-column label="操作" width="100"><template #default="{row}"><el-button size="small" text @click="emit('blacklist',row)">加入黑名单</el-button></template></el-table-column>
  </el-table>
  <div class="footer"><span class="counts">{{ result.total_all }} 只全量 / {{ result.total_filtered }} 只符合 / {{ result.total_excluded }} 只排除</span><el-pagination v-model:current-page="page" v-model:page-size="size" :page-sizes="[20,50,100]" layout="prev,pager,next,sizes,total" :total="rows.length"/></div>
 </section>
</template>
<style scoped>
.results{min-width:0;background:var(--el-bg-color);padding:16px;border:1px solid var(--el-border-color-light);border-radius:8px}.tools{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.tools>.el-input{width:240px}.meta{font-size:12px;color:var(--el-text-color-secondary)}.el-alert{margin:8px 0}.footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:16px}.counts{font-size:12px;color:var(--el-text-color-secondary)}.footer :deep(.el-pagination){flex-wrap:wrap}.results :deep(.el-table){width:100%;font-size:12px}.name-cell{font-weight:600}.redeem-badge{margin-left:4px;padding:0 4px;border-radius:3px;font-size:10px;line-height:16px;font-weight:400;white-space:nowrap}.b-green{color:#388e3c;background:rgba(103,194,58,.12)}.b-blue{color:#409eff;background:rgba(64,158,255,.12)}.b-orange{color:#e6a23c;background:rgba(230,162,60,.14)}.b-red{color:#f56c6c;background:rgba(245,108,108,.12)}.soe-badge{margin-left:4px;padding:0 4px;border-radius:3px;font-size:10px;line-height:16px;font-weight:400;white-space:nowrap}.soe-central{color:#b03a2e;background:rgba(214,69,65,.12)}.soe-local{color:#409eff;background:rgba(64,158,255,.12)}.ext-link{color:#2563eb;text-decoration:none}html.dark .ext-link{color:#60a5fa}.ext-link:hover{text-decoration:underline}.results :deep(.el-table .el-table__cell){padding:4px 0}.results :deep(.el-table .cell){padding:0 6px;line-height:20px}
</style>
