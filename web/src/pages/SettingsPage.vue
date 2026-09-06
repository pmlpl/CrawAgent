<script setup>
// 设置页外壳（tab 化信息架构）：顶栏页签 + 区块组件组合面。
// 状态层在 useSettings / useBg 组合式（模块级共享），各区块组件直接取用；
// 数据加载只在此处做一次。站点级凭据（如微信读书 Cookie）不在这里——
// 它们属于站点档案，在「站点档案」页按站点配置。
import { onMounted, ref } from 'vue'
import { useSettings } from '../composables/useSettings'
import SettingsModels from '../components/settings/SettingsModels.vue'
import SettingsAppearance from '../components/settings/SettingsAppearance.vue'
import SettingsEcosystem from '../components/settings/SettingsEcosystem.vue'
import SettingsAdvanced from '../components/settings/SettingsAdvanced.vue'
import '../components/settings/settings.css'

const { load, loadEcosystem } = useSettings()

// 页签表
const TABS = [
  { key: 'models', label: '对话模型' },
  { key: 'appearance', label: '界面外观' },
  { key: 'eco', label: '生态扩展' },
  { key: 'advanced', label: '高级' },
]
const tab = ref('models')

onMounted(async () => {
  await load()
  loadEcosystem()
})
</script>

<template>
  <div class="page settings-page">
    <div class="page-head">
      <h1>设置</h1>
      <p class="muted">模型、外观与生态扩展的集中配置</p>
    </div>

    <nav class="settings-tabs" role="tablist" aria-label="设置分区">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        role="tab"
        class="settings-tab"
        :class="{ active: tab === t.key }"
        :aria-selected="tab === t.key"
        @click="tab = t.key"
      >{{ t.label }}</button>
    </nav>

    <div v-show="tab === 'models'" class="tab-pane"><SettingsModels /></div>
    <div v-show="tab === 'appearance'" class="tab-pane"><SettingsAppearance /></div>
    <div v-show="tab === 'eco'" class="tab-pane"><SettingsEcosystem /></div>
    <div v-show="tab === 'advanced'" class="tab-pane"><SettingsAdvanced /></div>
  </div>
</template>

<style scoped>
.page {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  max-width: var(--maxw);
  margin: 0 auto;
  padding: 28px 20px 40px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.page-head h1 {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px;
}

.tab-pane {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.settings-tabs {
  display: flex;
  gap: 4px;
  width: fit-content;
  padding: 4px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 10px;
}
.settings-tab {
  appearance: none;
  background: none;
  border: none;
  border-radius: 7px;
  color: var(--dim);
  font-size: 13.5px;
  font-weight: 500;
  padding: 8px 16px;
  cursor: pointer;
  transition: color .15s, background .15s;
}
.settings-tab:hover { color: var(--accent); }
.settings-tab.active { background: var(--accent-soft); color: var(--accent); }
.settings-tab.active:hover { color: var(--accent); }

@media (max-width: 640px) {
  .page { padding: 20px 14px 24px; }
}
</style>
