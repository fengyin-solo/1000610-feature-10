<template>
  <section class="page" data-module="task">
    <header class="page-head">
      <div>
        <h2>检测任务管理</h2>
        <p class="page-desc">按任务优先级与计划完成日排定派发顺序，超期任务排在同优先级普通任务之前；派发时校验承检人员资质与在手任务上限。</p>
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
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'overdue-row': row.overdue }">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td><span v-if="row.overdue" class="tag-overdue">超期</span><span v-else>—</span></td>
          <td class="row-actions">
            <button
              v-for="action in actions"
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
      <span>共 {{ total }} 条检测任务记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Assignee = { 姓名: string; 资质: string[]; 在手任务: number; 在手上限: number }

const ENDPOINT = '/api/task'
const columns = ["任务编号", "关联样品", "检测项目", "承检人员", "计划完成日", "实际完成日", "任务优先级", "任务状态"]
const actions = ["派发任务", "提交复核", "确认完成"]
const statuses = ["待派发", "检测中", "待复核", "已完成"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const assignees = ref<Assignee[]>([])
const stats = ref([
  { label: '待派发任务', value: 0 },
  { label: '检测中任务', value: 0 },
  { label: '超期任务', value: 0 },
])

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

function pickAssignee(row: Row): string | null {
  const lines = assignees.value.map(
    (item) => `${item.姓名}（资质：${item.资质.join('、')}，在手 ${item.在手任务}/${item.在手上限}）`,
  )
  const current = String(row['承检人员'] ?? '')
  const hint = lines.length ? `\n可选承检人员：\n${lines.join('\n')}` : ''
  const picked = window.prompt(`请输入承检人员姓名${hint}`, current)
  return picked === null ? null : picked.trim()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  const values: Record<string, string> = { action }
  if (action === '派发任务') {
    const assignee = pickAssignee(row)
    if (assignee === null) {
      return
    }
    if (!assignee) {
      errorMessage.value = '派发前请先指定承检人员'
      return
    }
    values['承检人员'] = assignee
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify(values),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) {
      throw new Error(result.message ?? result.detail ?? '检测任务动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  const keyword = (filters.value['任务编号'] ?? '').trim()
  if (keyword) {
    params.set('keyword', keyword)
  }
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('检测任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '待派发任务', value: rows.value.filter((row) => row['任务状态'] === '待派发').length },
      { label: '检测中任务', value: rows.value.filter((row) => row['任务状态'] === '检测中').length },
      { label: '超期任务', value: rows.value.filter((row) => row.overdue).length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测任务列表读取失败'
  }
}

async function loadAssignees() {
  try {
    const response = await request(`${ENDPOINT}/assignees`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    assignees.value = payload.items ?? []
  } catch {
    assignees.value = []
  }
}

onMounted(() => {
  void reload()
  void loadAssignees()
})
</script>
