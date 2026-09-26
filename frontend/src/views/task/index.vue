<template>
  <section class="page" data-module="task">
    <header class="page-head">
      <div>
        <h2>检测任务管理</h2>
        <p class="page-desc">维护检测任务，围绕任务编号、关联样品、检测项目、承检人员做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检测任务</button>
        <button class="btn" type="button" @click="exportRows">导出检测任务清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>超期标记</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '计划完成日' && isOverdue(row)" class="overdue-date">{{ row[column] || '—' }}</span>
            <span v-else>{{ row[column] || '—' }}</span>
          </td>
          <td>
            <span v-if="isOverdue(row)" class="tag tag-overdue">超期</span>
            <span v-else class="tag tag-ok">正常</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无检测任务数据，可先登记检测任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测任务记录（顺序：优先级 → 同优先级超期优先 → 计划完成日）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="dispatchTarget" class="modal-mask" @click.self="closeDispatch">
      <div class="modal" role="dialog" aria-modal="true" aria-label="派发检测任务">
        <h3>派发检测任务 {{ dispatchTarget['任务编号'] }}</h3>
        <p class="modal-tip">
          检测项目「{{ dispatchTarget['检测项目'] }}」 · 计划完成日 {{ dispatchTarget['计划完成日'] }}
          <span v-if="isOverdue(dispatchTarget)" class="tag tag-overdue">已超期</span>
        </p>
        <label class="modal-field">
          <span>承检人员</span>
          <select v-model="dispatchAssignee">
            <option value="" disabled>请选择承检人员</option>
            <option v-for="person in inspectors" :key="person['承检人员']" :value="person['承检人员']">
              {{ person['承检人员'] }}（在手 {{ person['在手任务数'] }}/{{ person['在手上限'] }}）
            </option>
          </select>
        </label>
        <p v-if="selectedInspector" class="modal-tip">
          可检项目：{{ selectedInspector['可检项目'].join('、') || '—' }}
        </p>
        <p v-if="selectedInspector && !isQualified(selectedInspector)" class="error-text">
          该承检人员不具备「{{ dispatchTarget['检测项目'] }}」资质，提交后会被后端拦下
        </p>
        <p v-else-if="selectedInspector && selectedInspector['已满负荷']" class="warn-text">
          该承检人员在手任务已达上限 {{ selectedInspector['在手上限'] }} 件，提交后会被后端拦下
        </p>
        <p v-if="dispatchError" class="error-text">{{ dispatchError }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeDispatch">取消</button>
          <button
            class="btn primary"
            type="button"
            :disabled="dispatchSubmitting || !canSubmitDispatch"
            @click="confirmDispatch"
          >
            {{ dispatchSubmitting ? '派发中…' : '确认派发' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

// KeepAlive 按名称缓存本页，从复核页等路由返回时保留顺序与超期标记现场
defineOptions({ name: 'TaskView' })

type Row = Record<string, string | number | boolean | null>
type Inspector = {
  承检人员: string
  可检项目: string[]
  在手任务数: number
  在手上限: number
  已满负荷: boolean
}

const ENDPOINT = '/api/task'
const columns = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级", "任务状态"]
// 不同状态下只暴露合法动作：非待派发任务不再显示「派发任务」，从界面侧避免重复派发
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  '待派发': ['派发任务'],
  '检测中': ['提交复核'],
  '待复核': ['确认完成'],
  '已完成': [],
}
const stats = ref([
  { label: '待派发任务', value: 0 },
  { label: '检测中任务', value: 0 },
  { label: '超期任务', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const inspectors = ref<Inspector[]>([])
const dispatchTarget = ref<Row | null>(null)
const dispatchAssignee = ref('')
const dispatchSubmitting = ref(false)
const dispatchError = ref('')

const selectedInspector = computed<Inspector | null>(
  () => inspectors.value.find((person) => person.承检人员 === dispatchAssignee.value) ?? null,
)

const canSubmitDispatch = computed(() => {
  const person = selectedInspector.value
  if (!person || !dispatchTarget.value) {
    return false
  }
  return !person.已满负荷 && isQualified(person)
})

function isOverdue(row: Row): boolean {
  // 超期标记以后端派发口径为准（未完成且计划完成日早于当天），刷新后由列表原样带回
  return row.超期 === true
}

function isQualified(person: Inspector): boolean {
  if (!dispatchTarget.value) {
    return false
  }
  return person.可检项目.includes(String(dispatchTarget.value['检测项目'] ?? ''))
}

function availableActions(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '检测任务登记入口尚未接入审批流'
}

function closeDispatch() {
  dispatchTarget.value = null
  dispatchAssignee.value = ''
  dispatchError.value = ''
  dispatchSubmitting.value = false
}

async function openDispatch(row: Row) {
  dispatchTarget.value = row
  dispatchAssignee.value = ''
  dispatchError.value = ''
  if (!inspectors.value.length) {
    await loadInspectors()
  }
}

async function confirmDispatch() {
  if (!dispatchTarget.value || !dispatchAssignee.value || dispatchSubmitting.value) {
    return
  }
  dispatchSubmitting.value = true
  dispatchError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${dispatchTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '派发任务', 承检人员: dispatchAssignee.value } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      // 后端对在手达上限、资质不匹配、重复派发都会给出具体原因，原样展示
      throw new Error(payload?.message || '派发未生效，请稍后重试')
    }
    closeDispatch()
    await Promise.all([reload(), loadStats(), loadInspectors()])
  } catch (error) {
    dispatchError.value = error instanceof Error ? error.message : '派发任务失败'
  } finally {
    dispatchSubmitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  if (action === '派发任务') {
    await openDispatch(row)
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '检测任务动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadStats(), loadInspectors()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务操作失败'
  }
}

async function loadInspectors() {
  try {
    const response = await request(`${ENDPOINT}/inspectors`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    inspectors.value = payload.items ?? []
  } catch {
    // 名册加载失败不阻塞列表，派发时后端仍会拦截
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    const items = payload.items ?? {}
    stats.value = [
      { label: '待派发任务', value: Number(items['待派发'] ?? 0) },
      { label: '检测中任务', value: Number(items['检测中'] ?? 0) },
      { label: '超期任务', value: Number(items['超期'] ?? 0) },
    ]
  } catch {
    // 看板读数失败时保留上一次结果，不打断派发操作
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('检测任务列表读取失败')
    }
    const payload = await response.json()
    // 列表顺序与超期标记均取后端结果，不在前端重排，保证刷新/返回复核页后口径一致
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadStats()
  void loadInspectors()
})
</script>

<style scoped>
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 18px;
}
.tag-overdue {
  color: #b42318;
  background: #fee4e2;
  border: 1px solid #fda29b;
}
.tag-ok {
  color: #027a48;
  background: #ecfdf3;
  border: 1px solid #abefc6;
}
.overdue-date {
  color: #b42318;
  font-weight: 600;
}
.warn-text {
  color: #b54708;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  width: 420px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  box-shadow: 0 12px 32px rgba(16, 24, 40, 0.18);
}
.modal h3 {
  margin: 0 0 8px;
}
.modal-tip {
  color: #475467;
  font-size: 13px;
  margin: 6px 0;
}
.modal-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 12px 0;
}
.modal-field select {
  padding: 6px 8px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.modal-actions .btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
