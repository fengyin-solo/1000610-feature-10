<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">实验室样品检测平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向样品受理、任务派发、检测执行、仪器校准与报告出具的一体化实验室检测管理后台。</span>
        <span class="head-user">当前值班：{{ store.operator }} · {{ store.shiftLabel }}</span>
      </header>
      <RouterView v-slot="{ Component }">
        <!-- 检测任务页缓存：刷新由后端排序/超期口径兜底，从复核页等路由返回时保留列表现场 -->
        <KeepAlive :include="['TaskView']">
          <component :is="Component" />
        </KeepAlive>
      </RouterView>
    </main>
  </div>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const navItems = [{ label: "运营概览", path: "/" }, { label: "样品受理", path: "/sample" }, { label: "委托单位", path: "/client" }, { label: "检测项目", path: "/project" }, { label: "检测任务", path: "/task" }, { label: "检测执行", path: "/execute" }, { label: "检测结果", path: "/result" }, { label: "结果复核", path: "/review" }, { label: "仪器设备", path: "/instrument" }, { label: "校准记录", path: "/calibration" }, { label: "试剂耗材", path: "/reagent" }, { label: "耗材领用", path: "/consume" }, { label: "环境监控", path: "/environment" }, { label: "报告出具", path: "/report" }, { label: "报告变更", path: "/issue" }, { label: "质量控制", path: "/qc" }, { label: "投诉处理", path: "/complaint" }, { label: "样品流转", path: "/stockin" }, { label: "检测结算", path: "/settlement" }]
</script>
