<script setup>
import { ref, computed, watch } from 'vue';
const props=defineProps({result:Object,stale:Boolean,name:String});
const emit=defineEmits(['blacklist']);
const view=ref('all'),search=ref(''),page=ref(1),size=ref(50),sort=ref({prop:'rank',order:'ascending'});
const columns=[['rank','排名',65],['code','代码',100],['name','名称',110],['industry_name','细分行业',140],['rating','评级',75],['price','当前价格',100],['sprice','正股价',100],['redeem_price','到期赎回价',115],['simple_maturity_yield_pct','简单到期收益率',150],['dblow','双低',90],['premium_rt','溢价率',100],['curr_iss_amt','剩余规模(亿)',115],['convert_value','转股价值',110],['year_left','剩余年限',100],['pb','市净率',90],['redeem','强赎状态',240],['total_score','得分',90]];
const numberFields=new Set(['price','sprice','redeem_price','simple_maturity_yield_pct','dblow','premium_rt','curr_iss_amt','convert_value','year_left','pb','total_score']);
function display(row,field){const v=row[field];if(v==null||v==='')return field==='rating'?'无评级':'—';return numberFields.has(field)?`${Number(v).toFixed(2)}${['simple_maturity_yield_pct','premium_rt'].includes(field)?'%':''}`:v;}
const rows=computed(()=>{
 let a=view.value==='excluded'?props.result.excluded_rows||[]:props.result.rows||[];
 if(view.value==='selected')a=a.filter(r=>r.selected);
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
  <h3>{{ name }} · {{ result.source==='live'?'实时行情':'数据库快照' }}</h3>
  <el-alert v-if="stale" title="条件或数据源已修改，当前为上次结果，请重新筛选" type="warning" :closable="false"/>
  <el-alert v-if="result.meta?.data_status==='no_snapshot'" title="尚无行情快照，请前往数据管理同步或改用实时行情" type="info" :closable="false"/>
  <el-alert v-for="w in result.meta?.warnings||[]" :key="w" :title="w" type="warning" :closable="false"/>
  <p class="meta">行情日期 {{ result.meta?.trade_date||'未确认' }} · 赎回日期 {{ result.meta?.redeem_trade_date||'未确认' }}<span v-if="result.meta?.fetched_at"> · 请求时间 {{ result.meta.fetched_at }}</span></p>
  <p>{{ result.total_all }} 只全量 / {{ result.total_filtered }} 只符合 / {{ result.total_excluded }} 只排除 <span v-if="result.selection_mode==='scored'"> / {{ result.selected_count }} 只入选 / {{ result.buffer_count }} 只容差保留</span></p>
  <div class="tools"><el-radio-group v-model="view"><el-radio-button value="all">全部符合</el-radio-button><el-radio-button v-if="result.selection_mode==='scored'" value="selected">入选</el-radio-button><el-radio-button value="excluded">排除明细</el-radio-button></el-radio-group><el-input v-model="search" placeholder="当前结果内查找代码/名称" clearable/><el-button @click="sort={prop:'rank',order:'ascending'}">恢复默认排序</el-button></div>
  <el-table :data="paged" stripe max-height="620" @sort-change="sort=$event.prop&&$event.order?$event:{prop:'rank',order:'ascending'}">
   <el-table-column v-for="[field,label,width] in columns" :key="field" :prop="field" :label="label" :width="width" :fixed="field==='name'?'left':false" sortable="custom">
    <template #header><el-tooltip v-if="field==='simple_maturity_yield_pct'" content="(到期赎回价－当前价格) / 当前价格 ×100；未年化，不含票息和税"><span>{{label}} ⓘ</span></el-tooltip><span v-else>{{label}}</span></template>
    <template #default="{row}"><el-tooltip v-if="field==='industry_name'&&row.industry_is_fallback" :content="`使用申万 ${row.industry_level} 级回退映射，原始码 ${row.industry_code}`"><span>{{ display(row,field) }} ⓘ</span></el-tooltip><span v-else>{{ display(row,field) }}</span></template>
   </el-table-column>
   <el-table-column label="入选状态" width="110"><template #default="{row}">{{view==='excluded'?'已排除':result.selection_mode==='filter_only'?'符合条件':row.selected?'入选':row.holdable?'容差保留':'其余'}}</template></el-table-column>
   <el-table-column v-if="view==='excluded'" label="排除原因" min-width="300"><template #default="{row}">{{reasons(row)}}</template></el-table-column>
   <el-table-column label="操作" width="100"><template #default="{row}"><el-button size="small" text @click="emit('blacklist',row)">加入黑名单</el-button></template></el-table-column>
  </el-table>
  <el-pagination v-model:current-page="page" v-model:page-size="size" :page-sizes="[20,50,100]" layout="prev,pager,next,sizes,total" :total="rows.length"/>
 </section>
</template>
<style scoped>
.results{min-width:0;background:#fff;padding:16px;border:1px solid #e4e7ed;border-radius:8px}.results h3{margin-top:0}.tools{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.tools>.el-input{width:240px}.meta{font-size:12px;color:#737b88}.el-alert{margin:8px 0}.el-pagination{margin-top:16px;flex-wrap:wrap}.results :deep(.el-table){width:100%}
</style>
