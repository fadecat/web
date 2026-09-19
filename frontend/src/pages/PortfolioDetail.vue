<script setup>
// 组合详情页(L2) —— **P2 范围**: 只渲染 P2 已有的数据
//   (成员 + 目标权重 + 添加后的收益 + 权重合计/ready + 数据就绪含共同起点 T0)。
// ⚠ 页面上标了「P3」的区域(收益条/净值曲线/相关性/指标卡)属于回测引擎, 本期不做 ——
//    不放假数据, 直接写明, 避免"看着像有其实没有"。
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import {
  getPortfolio, listAssets, patchPortfolio,
} from '../api/portfolio';
import {
  EMPTY, formatDate, formatReturnPct, formatWeight, readinessText, trendOf, weightSummaryText,
} from '../utils/portfolioList.mjs';

const route = useRoute();
const router = useRouter();
const portfolioId = Number(route.params.id);

const loading = ref(false);
const errorText = ref('');
const detail = ref(null);
const registered = ref([]);
const editing = ref(false);
const draft = ref([]);   // [{symbol, name, security_type, target_weight}]
const saving = ref(false);

const TYPE_LABEL = { STOCK: '股票', ETF: 'ETF', FUND: '场外基金' };
const BASIS_LABEL = { HFQ: '后复权价', NAV_ADJ: '分红再投净值', PRICE: '价格指数' };

const weightState = computed(() => ({
  assets: detail.value?.assets || [],
  weight_sum: detail.value?.weight_sum,
}));
const tips = computed(() => ({
  weight: weightSummaryText(weightState.value),
  readiness: readinessText(detail.value?.data_readiness),
}));

const load = async () => {
  loading.value = true;
  errorText.value = '';
  try {
    detail.value = await getPortfolio(portfolioId);
    draft.value = (detail.value.assets || []).map((a) => ({ ...a }));
  } catch (err) {
    errorText.value = err?.response?.status === 404
      ? `组合不存在（id=${portfolioId}）`
      : (err?.response?.data?.detail || '加载失败');
  } finally {
    loading.value = false;
  }
};

const loadRegistered = async () => {
  try {
    registered.value = await listAssets();
  } catch {
    registered.value = []; // 标的库读取失败不阻塞详情页
  }
};

const startEdit = async () => {
  await loadRegistered();
  editing.value = true;
};
const cancelEdit = () => { editing.value = false; draft.value = (detail.value?.assets || []).map((a) => ({ ...a })); };

// 可选但尚未加入的已注册标的(只让用户从"已注册"里挑 —— 注册走标的库页, 含抓取与同步状态)
const selectable = computed(() => {
  const inDraft = new Set(draft.value.map((a) => a.symbol));
  return registered.value.filter((a) => !inDraft.has(a.symbol) && a.enabled !== false);
});

const addRow = (symbol) => {
  const asset = registered.value.find((a) => a.symbol === symbol);
  if (!asset) return;
  draft.value.push({
    symbol: asset.symbol, name: asset.name, security_type: asset.security_type, target_weight: null,
  });
};
const removeRow = (symbol) => { draft.value = draft.value.filter((a) => a.symbol !== symbol); };

const save = async () => {
  saving.value = true;
  try {
    // PATCH 的 assets 是**全量替换**: 增/删/改权重都走这一条
    detail.value = await patchPortfolio(portfolioId, {
      assets: draft.value.map((a) => ({ symbol: a.symbol, target_weight: a.target_weight })),
    });
    draft.value = (detail.value.assets || []).map((a) => ({ ...a }));
    editing.value = false;
    ElMessage.success('已保存');
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <div class="portfolio-detail-page">
    <div class="detail-head">
      <el-button text @click="router.push({ name: 'portfolio-list' })">← 返回组合列表</el-button>
    </div>

    <el-alert v-if="errorText" :title="errorText" type="error" show-icon :closable="false" />

    <template v-else-if="detail">
      <h2 class="page-title">
        {{ detail.name }}
        <span class="meta">成立时间：{{ formatDate(detail.created_at) }}</span>
      </h2>

      <!-- 数据就绪(回测前检查): 不阻塞, 把问题摆出来 -->
      <el-alert
        :type="detail.data_readiness?.all_ready ? 'success' : 'warning'"
        :title="tips.readiness"
        :closable="false"
        show-icon
        class="readiness"
      />

      <!-- 三格收益: P3 才有(列表页的这三格也依赖同一套账本) -->
      <div class="pending-box">
        <div class="pending-title">收益条 / 净值曲线 / 指标卡 / 相关性矩阵</div>
        <div class="pending-desc">
          <b>P3 回测引擎</b>尚未实现，这里不放占位假数据。
          列表页那三格（日收益 / 近一月 / 今年以来）同样要等它 —— 因为规格要求
          <b>列表页与详情页必须共用同一条「区间解析 + 份额法账本」</b>，数字才可能一致。
        </div>
      </div>

      <div class="section-head">
        <h3 class="section-title">组合标的</h3>
        <div>
          <el-button v-if="!editing" size="small" @click="startEdit">编辑权重</el-button>
          <template v-else>
            <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
            <el-button size="small" @click="cancelEdit">取消</el-button>
          </template>
        </div>
      </div>

      <p class="weight-tip">{{ tips.weight }}</p>

      <el-table :data="editing ? draft : (detail.assets || [])" size="small" class="asset-table">
        <el-table-column label="标的" min-width="180">
          <template #default="{ row }">
            <div class="asset-name">{{ row.name || EMPTY }}</div>
            <div class="asset-code">{{ row.symbol }}</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">{{ TYPE_LABEL[row.security_type] || EMPTY }}</template>
        </el-table-column>
        <el-table-column label="复权口径" width="120">
          <template #default="{ row }">
            {{ BASIS_LABEL[row.since_added_return?.price_basis] || EMPTY }}
          </template>
        </el-table-column>
        <el-table-column label="初始比例" width="130">
          <template #default="{ row }">
            <el-input-number
              v-if="editing"
              v-model="row.target_weight"
              :min="0" :max="100" :step="1" :precision="2"
              size="small" controls-position="right" style="width: 110px"
            />
            <span v-else>{{ formatWeight(row.target_weight) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="添加日" width="110">
          <template #default="{ row }">{{ formatDate(row.added_at) }}</template>
        </el-table-column>
        <el-table-column label="添加后收益" width="120">
          <template #default="{ row }">
            <span :class="`trend-${trendOf(row.since_added_return?.value)}`">
              {{ formatReturnPct(row.since_added_return?.value) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column v-if="editing" label="" width="70">
          <template #default="{ row }">
            <el-button size="small" text type="danger" @click="removeRow(row.symbol)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="editing" class="add-row">
        <el-select
          placeholder="从已注册标的里添加"
          style="width: 260px"
          :model-value="null"
          @change="addRow"
        >
          <el-option
            v-for="a in selectable"
            :key="a.symbol"
            :label="`${a.name} ${a.symbol}`"
            :value="a.symbol"
          />
        </el-select>
        <span class="add-hint">
          没有想加的？去
          <a @click="router.push({ name: 'portfolio-assets' })">标的库</a>
          注册并抓取。
        </span>
      </div>

      <p class="foot-note">
        ⚠「添加后收益」是<b>纯展示列</b>（= 该标的自身自其添加日的收益，与权重无关），对回测零影响；
        「当前占比」是<b>不平衡持有至今的漂移权重</b>，需要份额法账本，属 P3。
      </p>
    </template>

    <div v-else-if="loading" class="loading">加载中…</div>
  </div>
</template>

<style scoped>
.portfolio-detail-page { padding: 4px 0; }
.detail-head { margin-bottom: 8px; }
.page-title { margin: 0 0 14px; font-size: 20px; font-weight: 600; }
.page-title .meta { margin-left: 12px; font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); }
.readiness { margin-bottom: 14px; }
.pending-box {
  border: 1px dashed var(--el-border-color); border-radius: 8px;
  padding: 12px 16px; margin-bottom: 18px; background: var(--el-fill-color-lighter);
}
.pending-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.pending-desc { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.7; }
.section-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--el-border-color-lighter);
}
.section-title { margin: 0; font-size: 15px; font-weight: 600; }
.weight-tip { margin: 0 0 10px; font-size: 12px; color: var(--el-text-color-secondary); }
.asset-name { font-weight: 500; }
.asset-code { font-size: 12px; color: var(--el-text-color-secondary); }
.add-row { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.add-hint { font-size: 12px; color: var(--el-text-color-secondary); }
.add-hint a { color: var(--el-color-primary); cursor: pointer; }
.foot-note { margin-top: 16px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.7; }
/* 涨红跌绿 */
.trend-up { color: #d93025; }
.trend-down { color: #1a8f3c; }
.trend-flat { color: var(--el-text-color-secondary); }
.loading { color: var(--el-text-color-secondary); padding: 32px 0; }
</style>
