import { ref, computed } from 'vue';

export const clone = value => JSON.parse(JSON.stringify(value));
// Server serialization can reorder object keys without changing a rule.
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
export const signature = t => JSON.stringify(canonical([t?.conditions, t?.strategy_factors, t?.target_count, t?.hold_tolerance, t?.migration_issues]));
export const uid = () => (globalThis.crypto?.randomUUID?.() ?? `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`);
export function useSelectionWorkspace(api) {
  const saved = ref(null), drafts = ref({}), editingId = ref(''), source = ref('db');
  const results = ref({}), running = ref({}), saving = ref(false), errors = ref({});
  let requests = {};
  const current = computed(() => drafts.value[editingId.value]);
  const templates = computed(() => Object.values(drafts.value));
  const dirtyFor = id => JSON.stringify(drafts.value[id]) !== JSON.stringify(saved.value?.templates.find(t => t.id === id));
  const dirty = computed(() => dirtyFor(editingId.value));
  const anyDirty = computed(() => templates.value.some(t => dirtyFor(t.id)));
  const result = computed(() => results.value[editingId.value]?.data || null);
  const stale = computed(() => !!result.value && (results.value[editingId.value].signature !== signature(current.value) || results.value[editingId.value].source !== source.value));
  const pending = computed(() => (current.value?.migration_issues || []).some(i => i.status === 'pending'));
  async function load() {
    const cfg = await api.getFactors(); saved.value = clone(cfg);
    drafts.value = Object.fromEntries(cfg.templates.map(t => [t.id, clone(t)]));
    editingId.value = cfg.active_id; results.value = {}; errors.value = {}; requests = {};
  }
  function patch(value) { drafts.value[editingId.value] = {...current.value, ...clone(value)}; }
  function validateName(name, id) {
    const clean = name.trim();
    if (!clean || [...clean].length > 40) throw new Error('模板名需要 1～40 个字符');
    if (templates.value.some(t => t.id !== id && t.name.toLocaleLowerCase() === clean.toLocaleLowerCase())) throw new Error('模板名称重复');
    return clean;
  }
  function rename(name) { patch({name:validateName(name, editingId.value)}); }
  function create(name) {
    const id=uid(); const clean=validateName(name,id);
    drafts.value[id]={id,name:clean,description:'',conditions:[
      {id:uid(),field:'redeem_status_code',op:'not_in',value:['ANNOUNCED_REDEEM','ANNOUNCED_INTENT','TRIGGER_MET'],enabled:true,missing:'exclude',negative:'compare'},
      {id:uid(),field:'stock_is_st',op:'eq',value:false,enabled:true,missing:'exclude',negative:'compare'},
    ],strategy_factors:[],target_count:10,hold_tolerance:0,migration_issues:[]}; editingId.value=id;
  }
  function duplicate() {
    let n=1, name; do { const suffix=n===1?' 副本':` 副本 ${n}`; name=[...current.value.name].slice(0,40-[...suffix].length).join('')+suffix;n++; } while(templates.value.some(t=>t.name===name));
    const id=uid();drafts.value[id]={...clone(current.value),id,name};editingId.value=id;
  }
  function discard() {
    const original=saved.value.templates.find(t=>t.id===editingId.value);
    if(original) drafts.value[editingId.value]=clone(original);
    else {delete drafts.value[editingId.value];editingId.value=saved.value.active_id;}
  }
  async function persist(payload, snapshots={}) {
    if(saving.value) throw new Error('正在保存，请稍候'); saving.value=true;
    try {
      const response=await api.saveFactors(payload);const cfg=response.data; saved.value=clone(cfg);
      for(const t of cfg.templates) if(!drafts.value[t.id] || JSON.stringify(drafts.value[t.id])===JSON.stringify(snapshots[t.id])) drafts.value[t.id]=clone(t);
      return cfg;
    } finally { saving.value=false; }
  }
  async function save() {
    const t=clone(current.value); t.name=validateName(t.name,t.id);
    const items=saved.value.templates.filter(x=>x.id!==t.id).concat(t);
    await persist({...saved.value,templates:items},{[t.id]:t});
  }
  async function setDefault() {
    const id=editingId.value;if(!saved.value.templates.some(t=>t.id===id)) throw new Error('请先保存新模板');
    await persist({...saved.value,active_id:id});
  }
  async function remove() {
    const id=editingId.value;
    if(templates.value.length<=1) throw new Error('至少保留一个模板');
    if(saved.value.templates.some(t=>t.id===id)) {
      const items=saved.value.templates.filter(t=>t.id!==id);
      if(!items.length) throw new Error('至少保留一个已保存模板');
      await persist({...saved.value,templates:items,active_id:saved.value.active_id===id?items[0].id:saved.value.active_id});
    }
    delete drafts.value[id];delete results.value[id];editingId.value=saved.value.active_id;
  }
  async function run() {
    const t=clone(current.value), id=t.id, token=uid(), chosen=source.value;
    if((t.migration_issues||[]).some(i=>i.status==='pending')) throw new Error('请先处理迁移项');
    requests[id]=token;running.value[id]=true;errors.value[id]='';
    try { const data=await api.screenBonds({...t,schema_version:3},chosen);
      if(requests[id]===token && drafts.value[id]) results.value[id]={data,signature:signature(t),source:chosen};
    } catch(e) {if(requests[id]===token) errors.value[id]=errorText(e);throw e;}
    finally {if(requests[id]===token) running.value[id]=false;}
  }
  function invalidate() {for(const r of Object.values(results.value)) r.signature='invalid';}
  return {saved,drafts,editingId,source,templates,current,dirty,anyDirty,result,stale,pending,running,saving,errors,load,patch,rename,create,duplicate,discard,save,setDefault,remove,run,invalidate};
}
export function errorText(e) {
  const d=e?.response?.data?.detail;return typeof d==='string'?d:d?.message ? `${d.path?d.path+'：':''}${d.message}` : e?.message || '操作失败';
}
