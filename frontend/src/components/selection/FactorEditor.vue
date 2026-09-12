<script setup>
import { clone, uid } from '../../composables/useSelectionWorkspace';
const props=defineProps({template:Object,catalog:Array,ratings:Array,industries:Array});
const emit=defineEmits(['patch']);
const meta=f=>props.catalog.find(x=>x.field===f)||{};
const ops={gte:'≥',lte:'≤',gt:'>',between:'区间（含边界）',in:'属于',not_in:'不属于',not_any:'不包含',eq:'等于'};
function patchCondition(index,patch){const a=clone(props.template.conditions);a[index]={...a[index],...patch};emit('patch',{conditions:a});}
function defaults(field){const m=meta(field);return {field,op:m.operators?.[0],value:m.type==='number'?0:m.type==='boolean'?false:[],missing:'exclude',negative:'compare'};}
function add(){emit('patch',{conditions:[...props.template.conditions,{id:uid(),enabled:true,...defaults(props.catalog[0].field)}]});}
function changeOp(i,op){patchCondition(i,{op,value:op==='between'?[0,0]:meta(props.template.conditions[i].field).type==='number'?0:props.template.conditions[i].value});}
function options(c){
 if(c.field==='industry_code')return props.industries.map(x=>({value:x.industry_code,label:`${x.industry_name||'未映射'} · ${x.industry_code}${x.industry_is_fallback?'（回退映射）':''}`}));
 if(c.field==='rating_cd')return props.ratings;
 return meta(c.field).options||[];
}
function score(i,patch){const a=clone(props.template.strategy_factors);a[i]={...a[i],...patch};emit('patch',{strategy_factors:a});}
function addScore(){const m=props.catalog.find(x=>x.scorable&&!props.template.strategy_factors.some(f=>f.field===x.field));if(m)emit('patch',{strategy_factors:[...props.template.strategy_factors,{field:m.field,ascending:true,weight:1,enabled:true}]});}
function resolveIssue(i,status){const a=clone(props.template.migration_issues);a[i].status=status;emit('patch',{migration_issues:a});}
</script>
<template>
 <section class="editor">
  <div v-for="(issue,i) in template.migration_issues" :key="issue.id">
   <el-alert v-if="issue.status==='pending'" type="warning" :closable="false" :title="issue.message">
    <details><summary>查看原规则</summary><pre>{{ JSON.stringify(issue.original,null,2) }}</pre></details>
    <p>添加并检查替代条件后确认，或明确删除旧规则。</p>
    <el-button size="small" @click="resolveIssue(i,'resolved')">已配置替代条件，确认</el-button>
    <el-button size="small" @click="resolveIssue(i,'archived')">删除旧规则</el-button>
   </el-alert>
  </div>
  <div class="config-grid">
   <section class="panel filters">
    <h3>筛选条件 <small>{{template.conditions.filter(c=>c.enabled).length}} 条启用 · 同时满足</small></h3>
    <p v-if="!template.conditions.length" class="empty">暂无条件，保留全部转债（仍应用全局黑名单）。</p>
    <div class="condition" v-for="(c,i) in template.conditions" :key="c.id">
     <div class="row">
      <el-switch size="small" :model-value="c.enabled" @update:model-value="patchCondition(i,{enabled:$event})" aria-label="启用条件"/>
      <el-select size="small" class="field" :model-value="c.field" filterable @update:model-value="patchCondition(i,defaults($event))" aria-label="条件字段"><el-option v-for="m in catalog.filter(x=>x.filterable)" :key="m.field" :value="m.field" :label="m.label"/></el-select>
      <el-select size="small" class="op" :model-value="c.op" @update:model-value="changeOp(i,$event)" aria-label="条件比较方式"><el-option v-for="op in meta(c.field).operators" :key="op" :value="op" :label="ops[op]"/></el-select>
      <div class="value">
       <template v-if="meta(c.field).type==='number'">
        <template v-if="c.op==='between'"><el-input-number v-for="n in [0,1]" :key="n" size="small" :model-value="c.value[n]" :controls="false" :min="meta(c.field).allow_negative?undefined:0" @update:model-value="patchCondition(i,{value:c.value.map((v,j)=>j===n?$event:v)})" :aria-label="n===0?'区间下限':'区间上限'"/></template>
        <el-input-number v-else size="small" :model-value="c.value" :controls="false" :min="meta(c.field).allow_negative?undefined:0" @update:model-value="patchCondition(i,{value:$event})" aria-label="条件数值"/>
       </template>
       <el-select v-else-if="meta(c.field).type==='boolean'" size="small" :model-value="c.value" @update:model-value="patchCondition(i,{value:$event})"><el-option :value="false" label="否"/><el-option :value="true" label="是"/></el-select>
       <el-select v-else size="small" :model-value="c.value" multiple filterable :allow-create="c.field==='code'||c.field==='rating_cd'" default-first-option collapse-tags collapse-tags-tooltip :placeholder="c.field==='code'?'代码回车添加':'选择值'" @update:model-value="patchCondition(i,{value:$event})"><el-option v-for="o in options(c)" :key="o.value" :value="o.value" :label="o.label"/></el-select>
      </div>
      <small class="unit">{{meta(c.field).unit}}</small>
      <el-button text type="danger" size="small" @click="emit('patch',{conditions:template.conditions.filter((_,n)=>n!==i)})">删除</el-button>
      <el-popover trigger="click" :width="280"><template #reference><el-button text size="small" aria-label="条件说明及缺失处理">···</el-button></template>
       <p>{{meta(c.field).description}}</p><p>数据缺失时</p>
       <el-select size="small" :model-value="c.missing" :disabled="['simple_maturity_yield_pct','redeem_price'].includes(c.field)" @update:model-value="patchCondition(i,{missing:$event})"><el-option value="exclude" label="排除"/><el-option value="include" label="放行（未计数也包含）"/></el-select>
      </el-popover>
     </div>
    </div>
    <el-button size="small" text type="primary" @click="add" :disabled="!catalog.length">＋ 添加条件</el-button>
   </section>
   <section class="panel scoring">
    <h3>排序 <small>{{template.strategy_factors.filter(f=>f.enabled).length}} 个评分因子</small></h3>
    <p v-if="!template.strategy_factors.length" class="empty">当前为纯筛选，展示全部符合条件的转债。</p>
    <div class="score-row" v-for="(f,i) in template.strategy_factors" :key="i">
     <el-switch size="small" :model-value="f.enabled" @update:model-value="score(i,{enabled:$event})"/>
     <el-select size="small" :model-value="f.field" @update:model-value="score(i,{field:$event})"><el-option v-for="m in catalog.filter(x=>x.scorable)" :key="m.field" :value="m.field" :label="m.label"/></el-select>
     <el-select size="small" :model-value="f.ascending" @update:model-value="score(i,{ascending:$event})"><el-option :value="true" label="越小越好"/><el-option :value="false" label="越大越好"/></el-select>
     <el-input-number size="small" :model-value="f.weight" :min="0.01" :step="0.5" :controls="false" @update:model-value="score(i,{weight:$event})" aria-label="评分权重"/>
     <el-button size="small" text type="danger" @click="emit('patch',{strategy_factors:template.strategy_factors.filter((_,n)=>n!==i)})">删除</el-button>
    </div>
    <el-button size="small" text type="primary" @click="addScore">＋ 添加评分因子</el-button>
    <small>评分仅决定排序与总分展示，不代表持仓或自动交易。</small>
    <details class="description"><summary>模板说明</summary><el-input type="textarea" :model-value="template.description" placeholder="模板说明" @update:model-value="emit('patch',{description:$event})"/></details>
   </section>
  </div>
 </section>
</template>
<style scoped>
.editor{min-width:0}.config-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:16px;align-items:start}.panel{min-width:0;padding:14px;background:var(--el-bg-color);border:1px solid var(--el-border-color-light);border-radius:8px}.panel h3{margin:0 0 12px;font-size:14px;font-weight:600;color:var(--el-text-color-primary)}.panel small{font-size:12px;color:var(--el-text-color-secondary);font-weight:400}.panel h3 small{margin-left:8px}.empty{font-size:12px;color:var(--el-text-color-secondary)}.row{display:flex;align-items:center;gap:6px;min-height:36px}.row .field{width:148px;min-width:110px;flex:1.2}.row .op{width:76px;min-width:65px;flex:.65}.value{display:flex;gap:4px;flex:1.1;min-width:100px}.value>.el-input-number,.value>.el-select{width:100%;min-width:0;flex:1}.row .unit{width:24px;flex-shrink:0}.row .el-button{padding:2px;margin:0}.score-row{display:grid;grid-template-columns:28px minmax(100px,1fr) 92px 58px 32px;gap:6px;align-items:center;min-height:36px}.score-row .el-input-number{width:58px}.score-row .el-button{padding:0}.scoring>small{display:block;margin:10px 0 0}.description{margin-top:16px;font-size:13px}.description summary{cursor:pointer;margin-bottom:8px}.el-alert{margin-bottom:12px;overflow-wrap:anywhere}.el-alert pre{white-space:pre-wrap} 
@media(max-width:1199px){.config-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:600px){.panel{padding:10px}.row{display:grid;grid-template-columns:28px minmax(0,1fr) 75px 32px 24px;gap:4px;padding:5px 0;border-bottom:1px solid var(--el-border-color-lighter)}.row .field{width:100%;min-width:0}.row .op{width:75px;min-width:0}.row .value{grid-column:2/4;grid-row:2;min-width:0}.row .unit{grid-column:4;grid-row:2}.row>.el-button{grid-column:4;grid-row:1}.row :deep(.el-popover__reference){grid-column:5;grid-row:1}.score-row{grid-template-columns:28px minmax(90px,1fr) 85px 45px 30px;gap:3px}.score-row .el-input-number{width:45px}}
</style>

