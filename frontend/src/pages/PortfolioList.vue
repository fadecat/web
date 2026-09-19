<script setup>
// 组合列表页(L1 · 我的组合) —— 严格照 docs/portfolio-lab-page-spec.md §九 还原:
//   两列卡片网格; 每卡三行 = 组合名 + ✎ + 删除 ｜ 成立时间 + 收益时间 ｜ 三格收益;
//   右上唯一按钮「创建组合」; 点卡片本体进详情; hover 显示「复制」; 不做排序/搜索/分组。
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  createPortfolio, deletePortfolio, listPortfolios,
} from '../api/portfolio';
import { buildPortfolioCard, EMPTY } from '../utils/portfolioList.mjs';

const router = useRouter();

const loading = ref(false);
const errorText = ref('');
const portfolios = ref([]);
const renamingId = ref(null);
const renameDraft = ref('');
const busyId = ref(null);

const cards = computed(() => portfolios.value.map(buildPortfolioCard));

const load = async () => {
  loading.value = true;
  errorText.value = '';
  try {
    portfolios.value = await listPortfolios();
  } catch (err) {
    // 不吞异常: 页面把问题摆出来, 而不是静默显示空列表
    errorText.value = err?.response?.data?.detail || '加载失败，请稍后重试';
  } finally {
    loading.value = false;
  }
};

// 「创建组合」= 直接创建 + 默认名(我的组合N), 不弹输入框(规格 9.4-7)
const onCreate = async () => {
  try {
    const created = await createPortfolio({});
    ElMessage.success(`已创建 ${created.name}`);
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '创建失败');
  }
};

// 点卡片本体进入详情(规格 9.4-1)
const onOpen = (id) => router.push({ name: 'portfolio-detail', params: { id } });

// 「复制」是卡片级操作, 自动命名 复制_原名(规格 9.4-4)
const onCopy = async (card) => {
  busyId.value = card.id;
  try {
    const copied = await createPortfolio({ fromId: card.id });
    ElMessage.success(`已复制为 ${copied.name}`);
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '复制失败');
  } finally {
    busyId.value = null;
  }
};

// ✎ = 就地改名(inline 输入框替换标题)
const startRename = (card) => {
  renamingId.value = card.id;
  renameDraft.value = card.name;
};
const cancelRename = () => { renamingId.value = null; renameDraft.value = ''; };
const commitRename = async (card) => {
  const name = renameDraft.value.trim();
  if (!name || name === card.name) return cancelRename();
  try {
    const { patchPortfolio } = await import('../api/portfolio');
    await patchPortfolio(card.id, { name });
    ElMessage.success('已改名');
    cancelRename();
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '改名失败');
  }
};

// 删除 = 软删(归档可恢复) + 二次确认; 不级联删标的与 Run(规格 9.4-3)
const onDelete = async (card) => {
  try {
    await ElMessageBox.confirm(
      `确认删除「${card.name}」？组合会被归档（可恢复），不会删除标的与历史回测。`,
      '删除组合',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    );
  } catch {
    return; // 用户取消
  }
  try {
    await deletePortfolio(card.id);
    ElMessage.success('已删除');
    await load();
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '删除失败');
  }
};

onMounted(load);
</script>

<template>
  <div class="portfolio-list-page">
    <h2 class="page-title">组合回测</h2>

    <div class="section-head">
      <h3 class="section-title">我的组合</h3>
      <el-button type="primary" class="create-btn" @click="onCreate">创建组合</el-button>
    </div>

    <el-alert v-if="errorText" :title="errorText" type="error" show-icon :closable="false" />

    <div v-else-if="loading" class="loading">加载中…</div>

    <div v-else-if="cards.length === 0" class="empty">
      还没有组合，点右上角「创建组合」开始。
    </div>

    <div v-else class="card-grid">
      <div
        v-for="card in cards"
        :key="card.id"
        class="portfolio-card"
        @click="onOpen(card.id)"
      >
        <div class="card-head">
          <div class="card-name-wrap" @click.stop>
            <template v-if="renamingId === card.id">
              <el-input
                v-model="renameDraft"
                size="small"
                class="rename-input"
                @keyup.enter="commitRename(card)"
                @keyup.esc="cancelRename"
              />
              <el-button size="small" type="primary" text @click="commitRename(card)">确定</el-button>
              <el-button size="small" text @click="cancelRename">取消</el-button>
            </template>
            <template v-else>
              <span class="card-name">{{ card.name }}</span>
              <span class="pencil" title="改名" @click.stop="startRename(card)">✎</span>
            </template>
          </div>
          <div class="card-actions" @click.stop>
            <span
              class="copy-link"
              :class="{ disabled: busyId === card.id }"
              @click="onCopy(card)"
            >复制</span>
            <span class="delete-link" @click="onDelete(card)">删除</span>
          </div>
        </div>

        <div class="card-meta">
          <span>成立时间：{{ card.createdAt }}</span>
          <span>收益时间：{{ card.asofDate }}</span>
        </div>

        <div class="card-metrics">
          <div v-for="cell in card.metrics" :key="cell.key" class="metric">
            <div class="metric-value" :class="cell.className">{{ cell.text }}</div>
            <div class="metric-label">{{ cell.label }}</div>
          </div>
        </div>
      </div>
    </div>

    <p class="page-foot">
      列表三格固定用「不平衡 + 日/近一月/今年以来」，与详情页收益条同口径（数字必须一致）。
      空组合显示「{{ EMPTY }}」。
    </p>
  </div>
</template>

<style scoped>
.portfolio-list-page { padding: 4px 0; }
.page-title { margin: 0 0 16px; font-size: 20px; font-weight: 600; }
.section-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--el-border-color-lighter);
}
.section-title { margin: 0; font-size: 16px; font-weight: 600; }
.loading, .empty { color: var(--el-text-color-secondary); padding: 32px 0; }
.card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.portfolio-card {
  border: 1px solid var(--el-border-color-light); border-radius: 8px;
  background: var(--el-bg-color); padding: 14px 16px; cursor: pointer;
  transition: box-shadow .2s ease, border-color .2s ease;
}
.portfolio-card:hover { box-shadow: 0 2px 10px rgba(0, 0, 0, .08); border-color: var(--el-border-color); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card-name-wrap { display: flex; align-items: center; gap: 6px; min-width: 0; }
.card-name {
  font-size: 15px; font-weight: 600; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.pencil { cursor: pointer; color: var(--el-text-color-secondary); font-size: 13px; }
.rename-input { width: 160px; }
.card-actions { display: flex; align-items: center; gap: 12px; flex: none; }
.copy-link, .delete-link {
  font-size: 12px; color: var(--el-text-color-secondary); cursor: pointer;
  opacity: 0; transition: opacity .15s ease;
}
.portfolio-card:hover .copy-link, .portfolio-card:hover .delete-link { opacity: 1; }
.copy-link:hover, .delete-link:hover { color: var(--el-color-primary); }
.copy-link.disabled { pointer-events: none; opacity: .4; }
.card-meta {
  display: flex; justify-content: space-between; gap: 12px;
  margin: 8px 0 14px; font-size: 12px; color: var(--el-text-color-secondary);
}
.card-metrics { display: grid; grid-template-columns: repeat(3, 1fr); text-align: center; }
.metric-value { font-size: 18px; font-weight: 600; line-height: 1.4; }
.metric-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
/* 涨红跌绿(A 股习惯) */
.trend-up { color: #d93025; }
.trend-down { color: #1a8f3c; }
.trend-flat { color: var(--el-text-color-secondary); }
.page-foot { margin-top: 16px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>
