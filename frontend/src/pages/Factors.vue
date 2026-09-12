<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import * as api from '../api';
import { useSelectionWorkspace, errorText } from '../composables/useSelectionWorkspace';
import FactorEditor from '../components/selection/FactorEditor.vue';
import SelectionResults from '../components/selection/SelectionResults.vue';

const ws=useSelectionWorkspace(api);
const {current,templates,editingId,saved,source,dirty,anyDirty,result,stale,pending,running,saving,errors}=ws;
const loading=ref(true),loadError=ref(''),catalog=ref([]),ratings=ref([]),industries=ref([]),editorOpen=ref(false);
const blacklistOpen=ref(false),blacklist=ref([]),blacklistCode=ref(''),blacklistBusy=ref(false);
const currentRunning=computed(()=>!!running.value[editingId.value]);
async function action(fn){try{return await fn();}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(errorText(e));}}
async function load(){loading.value=true;loadError.value='';try{
 const [cat,r,i]=await Promise.all([api.getFactorCatalog(),api.getRatingCatalog(),api.getIndustryCatalog()]);catalog.value=cat;ratings.value=r;industries.value=i;await ws.load();
 if(!pending.value)await action(()=>ws.run());
 }catch(e){loadError.value=errorText(e);}finally{loading.value=false;}}
async function nameDialog(kind){await action(async()=>{
 const answer=await ElMessageBox.prompt(kind==='new'?'请输入新模板名称':'请输入模板名称',kind==='new'?'新建筛选模板':'重命名模板',{inputValue:kind==='new'?'':current.value.name,inputValidator:v=>!!v?.trim()&&[...v.trim()].length<=40||'名称需要 1～40 个字符'});
 if(kind==='new')ws.create(answer.value);else ws.rename(answer.value);
 });}
async function remove(){await action(async()=>{await ElMessageBox.confirm(`删除“${current.value.name}”？删除默认模板时将使用首个已保存模板作为默认。`,'删除模板',{type:'warning'});await ws.remove();});}
async function save(){await action(async()=>{await ws.save();ElMessage.success('当前模板已保存');});}
async function reload(){await action(async()=>{if(anyDirty.value)await ElMessageBox.confirm('重新加载会放弃所有未保存草稿，是否继续？','重新加载');await load();});}
async function loadBlacklist(){blacklistBusy.value=true;try{blacklist.value=await api.getBlacklist();}finally{blacklistBusy.value=false;}}
async function openBlacklist(){blacklistOpen.value=true;await action(loadBlacklist);}
async function addBlacklist(row){await action(async()=>{await ElMessageBox.confirm(`将 ${row.name||row.code} 加入所有模板共用的黑名单？`,'加入黑名单');await api.addBlacklist({bond_id:row.code,bond_nm:row.name});ws.invalidate();if(blacklistOpen.value)await loadBlacklist();});}
async function addCode(){const code=blacklistCode.value.trim().toUpperCase().replace(/\.(SH|SZ)$/,'');if(!/^\d{6}$/.test(code)){ElMessage.warning('请输入六位转债代码');return;}await addBlacklist({code});blacklistCode.value='';}
async function removeBlacklisted(row){await action(async()=>{await api.removeBlacklist(row.bond_id);ws.invalidate();await loadBlacklist();});}
function beforeUnload(e){if(anyDirty.value){e.preventDefault();e.returnValue='';}}
onMounted(()=>{window.addEventListener('beforeunload',beforeUnload);load();});
onBeforeUnmount(()=>{window.removeEventListener('beforeunload',beforeUnload);});
onBeforeRouteLeave(async()=>{if(!anyDirty.value)return true;try{await ElMessageBox.confirm('尚有模板草稿未保存，离开将丢失修改。','离开页面');return true;}catch{return false;}});
</script>
<template>
 <div class="workspace" v-loading="loading">
  <el-alert v-if="loadError" :title="loadError" type="error" :closable="false"/>
  <div class="toolbar">
   <el-select v-model="editingId" filterable placeholder="选择筛选模板" aria-label="筛选模板"><el-option v-for="t in templates" :key="t.id" :value="t.id" :label="`${t.name}${saved?.active_id===t.id?' · 默认':''}`"/></el-select>
   <el-button @click="nameDialog('new')" :disabled="!saved||saving">新建</el-button>
   <el-button @click="nameDialog('rename')" :disabled="!current||saving">重命名</el-button>
   <el-button @click="action(ws.duplicate)" :disabled="!current||saving">复制</el-button>
   <el-dropdown @command="cmd=>cmd==='delete'?remove():cmd==='default'?action(ws.setDefault):cmd==='discard'?ws.discard():openBlacklist()">
    <el-button :disabled="!current||saving">更多 ▾</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="default">设为默认</el-dropdown-item><el-dropdown-item command="discard">放弃当前修改</el-dropdown-item><el-dropdown-item command="delete">删除模板</el-dropdown-item><el-dropdown-item command="blacklist" divided>全局黑名单</el-dropdown-item></el-dropdown-menu></template>
   </el-dropdown>
   <el-button @click="reload" :disabled="saving">重新加载</el-button>
  </div>
  <template v-if="current">
   <div class="toolbar actions">
    <span class="status" :class="{dirty}">{{dirty?'草稿未保存':'已保存'}}</span>
    <el-radio-group v-model="source"><el-radio-button value="db">数据库快照</el-radio-button><el-radio-button value="live">实时行情</el-radio-button></el-radio-group>
    <el-button @click="editorOpen=!editorOpen">{{editorOpen?'收起配置':'配置条件'}}</el-button>
    <el-button :disabled="!dirty||saving" :loading="saving" @click="save">保存模板</el-button>
    <el-button type="primary" :disabled="pending||currentRunning" :loading="currentRunning" @click="action(ws.run)">执行筛选</el-button>
   </div>
   <el-alert v-if="pending" title="当前模板有待确认的旧规则，请打开配置条件并处理后执行" type="warning" :closable="false"/>
   <el-alert v-if="errors[editingId]" :title="errors[editingId]" type="error" :closable="false"/>
   <div class="content">
    <FactorEditor v-if="editorOpen" :template="current" :catalog="catalog" :ratings="ratings" :industries="industries" @patch="ws.patch"/>
    <SelectionResults v-if="result" :result="result" :stale="stale" @blacklist="addBlacklist"/>
    <el-empty v-else :description="pending?'处理迁移规则后执行筛选':'点击执行筛选查看结果'"/>
   </div>
  </template>
  <el-drawer v-model="blacklistOpen" title="全局黑名单 · 对所有模板生效" size="min(100%, 540px)"><div v-loading="blacklistBusy"><div class="toolbar"><el-input v-model="blacklistCode" placeholder="六位代码"/><el-button @click="addCode">添加</el-button></div><el-table :data="blacklist"><el-table-column prop="bond_id" label="代码"/><el-table-column prop="bond_nm" label="名称"/><el-table-column label="操作"><template #default="{row}"><el-button text @click="removeBlacklisted(row)">移除</el-button></template></el-table-column></el-table></div></el-drawer>
 </div>
</template>
<style scoped>
.workspace{min-width:0}.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}.toolbar>.el-select{width:260px}.toolbar>.el-input{width:220px}.status{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#909399}.status::before{content:'';width:6px;height:6px;border-radius:50%;background:#67c23a}.status.dirty::before{background:#e6a23c}.content{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;margin-top:16px;min-width:0}.el-alert{margin:10px 0}@media(max-width:767px){.toolbar>.el-select{width:100%}.toolbar{gap:6px}.actions>.el-radio-group{width:100%}}
</style>
