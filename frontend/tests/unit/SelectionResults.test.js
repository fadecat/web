import { it, expect } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import ElementPlus from 'element-plus';
import Results from '../../src/components/selection/SelectionResults.vue';
it('renders zero, negative and missing yield distinctly with real table rows',async()=>{
 const w=mount(Results,{props:{result:{selection_mode:'filter_only',rows:[{code:'1',name:'零',simple_maturity_yield_pct:0},{code:'2',name:'负',simple_maturity_yield_pct:-12},{code:'3',name:'缺',simple_maturity_yield_pct:null}],excluded_rows:[],meta:{}}},global:{plugins:[ElementPlus]}});
 await flushPromises();
 expect(w.text()).toContain('0.00%');expect(w.text()).toContain('-12.00%');expect(w.text()).toContain('—');expect(w.text()).not.toContain('[object Object]');w.unmount();
});
