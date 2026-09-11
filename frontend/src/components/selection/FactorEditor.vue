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
  <h3>筛选条件 <small>全部条件同时满足</small></h3>
  <div v-for="(issue,i) in template.migration_issues" :key="issue.id">
   <el-alert v-if="issue.status==='pending'" type="warning" :closable="false" :title="issue.message">
    <p>原规则：{{ JSON.stringify(issue.original) }}</p>
    <p>请在下方添加并检查替代条件，或明确删除旧规则。</p>
    <el-button size="small" @click="resolveIssue(i,'resolved')">已配置替代条件，确认</el-button>
    <el-button size="small" @click="resolveIssue(i,'archived')">删除旧规则</el-button>
   </el-alert>
  </div>
  <p v-if="!template.conditions.length">暂无条件，保留全部转债（仍应用全局黑名单）。</p>
  <div class="condition" v-for="(c,i) in template.conditions" :key="c.id">
   <div class="line">
    <el-switch :model-value="c.enabled" @update:model-value="patchCondition(i,{enabled:$event})" aria-label="启用条件" />
    <el-select :model-value="c.field" filterable @update:model-value="patchCondition(i,defaults($event))" aria-label="条件字段">
     <el-option v-for="m in catalog.filter(x=>x.filterable)" :key="m.field" :value="m.field" :label="m.label" />
    </el-select>
    <el-button text type="danger" @click="emit('patch',{conditions:template.conditions.filter((_,n)=>n!==i)})">删除</el-button>
   </div>
   <div class="line">
    <el-select :model-value="c.op" @update:model-value="changeOp(i,$event)" aria-label="条件比较方式"><el-option v-for="op in meta(c.field).operators" :key="op" :value="op" :label="ops[op]" /></el-select>
    <template v-if="meta(c.field).type==='number'">
     <template v-if="c.op==='between'"><el-input-number v-for="n in [0,1]" :key="n" :model-value="c.value[n]" :controls="false" :min="meta(c.field).allow_negative?undefined:0" @update:model-value="patchCondition(i,{value:c.value.map((v,j)=>j===n?$event:v)})" /></template>
     <el-input-number v-else :model-value="c.value" :controls="false" :min="meta(c.field).allow_negative?undefined:0" @update:model-value="patchCondition(i,{value:$event})" />
    </template>
    <el-select v-else-if="meta(c.field).type==='boolean'" :model-value="c.value" @update:model-value="patchCondition(i,{value:$event})"><el-option :value="false" label="否"/><el-option :value="true" label="是"/></el-select>
    <el-select v-else :model-value="c.value" multiple filterable :allow-create="c.field==='code'||c.field==='rating_cd'" default-first-option collapse-tags :placeholder="c.field==='code'?'输入六位代码并回车':'选择值'" @update:model-value="patchCondition(i,{value:$event})"><el-option v-for="o in options(c)" :key="o.value" :value="o.value" :label="o.label" /></el-select>
   </div>
   <small>{{ meta(c.field).description }}</small>
   <div class="line"><span>数据缺失</span><el-select :model-value="c.missing" :disabled="['simple_maturity_yield_pct','redeem_price'].includes(c.field)" @update:model-value="patchCondition(i,{missing:$event})"><el-option value="exclude" label="排除"/><el-option value="include" label="放行（包含未计数）"/></el-select></div>
  </div>
  <el-button @click="add" :disabled="!catalog.length">＋ 添加条件</el-button>
  <el-collapse class="scoring"><el-collapse-item name="score" :title="`排序与入选 · ${template.strategy_factors.filter(f=>f.enabled).length} 个评分因子`">
   <p>无评分因子时展示全部符合条件的转债。</p>
   <div class="condition" v-for="(f,i) in template.strategy_factors" :key="i">
    <div class="line"><el-switch :model-value="f.enabled" @update:model-value="score(i,{enabled:$event})"/>
     <el-select :model-value="f.field" @update:model-value="score(i,{field:$event})"><el-option v-for="m in catalog.filter(x=>x.scorable)" :key="m.field" :value="m.field" :label="m.label"/></el-select>
     <el-button text @click="emit('patch',{strategy_factors:template.strategy_factors.filter((_,n)=>n!==i)})">删除</el-button></div>
    <div class="line"><el-select :model-value="f.ascending" @update:model-value="score(i,{ascending:$event})"><el-option :value="true" label="越小越好"/><el-option :value="false" label="越大越好"/></el-select><el-input-number :model-value="f.weight" :min="0.01" :step="0.5" :controls="false" @update:model-value="score(i,{weight:$event})" aria-label="评分权重"/></div>
   </div>
   <el-button @click="addScore">＋ 添加评分因子</el-button>
   <div class="line">目标数量<el-input-number :model-value="template.target_count" :min="1" :max="50" @update:model-value="emit('patch',{target_count:$event})"/></div>
   <div class="line">容差数量<el-input-number :model-value="template.hold_tolerance" :min="0" :max="20" @update:model-value="emit('patch',{hold_tolerance:$event})"/></div>
   <small>数量仅用于结果标记，不代表持仓或自动交易。</small>
  </el-collapse-item></el-collapse>
  <el-input type="textarea" :model-value="template.description" placeholder="模板说明" @update:model-value="emit('patch',{description:$event})"/>
 </section>
</template>
<style scoped>
.editor{min-width:0;padding:14px;background:#fff;border:1px solid #e4e7ed;border-radius:8px}.editor h3{margin:0 0 16px;font-size:16px}.editor small{color:#737b88;font-size:12px}.condition{padding:12px 0;border-bottom:1px solid #eee;margin-bottom:10px}.line{display:flex;align-items:center;gap:6px;margin:8px 0}.line>.el-select{flex:1;min-width:75px}.line>.el-input-number{width:100px;min-width:0;flex:1}.scoring{margin:18px 0}.el-alert{margin-bottom:12px;overflow-wrap:anywhere}
</style>
