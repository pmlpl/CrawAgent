<script setup>
// 应用侧边栏（参考 crawagent-pro Sidebar 设计）
// - 顶部 Logo + 品牌
// - 新对话按钮
// - 会话列表（悬停显示删除）
// - 多选模式：批量删除会话
// - 底部：站点档案 + 设置 入口
import { ref, onMounted, onUnmounted, computed } from 'vue'
import logoUrl from '../assets/concept-A-spider.png'

const props = defineProps({
  open: { type: Boolean, default: true },
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, required: true },
  isSettingsPage: { type: Boolean, default: false },
  isSitesPage: { type: Boolean, default: false },
})
const emit = defineEmits(['new-chat', 'select-session', 'delete-session', 'archive-session', 'bulk-delete', 'rename-session', 'open-settings', 'open-sites', 'close'])

// 当前展开操作菜单的会话 ID（null = 全部收起）
const actionMenuId = ref(null)

function toggleActionMenu(e, id) {
  e.stopPropagation()
  actionMenuId.value = actionMenuId.value === id ? null : id
}

function closeActionMenu() {
  actionMenuId.value = null
}

function onConfirmDelete(e, id) {
  e.stopPropagation()
  closeActionMenu()
  emit('delete-session', id)
}

function onConfirmArchive(e, id) {
  e.stopPropagation()
  closeActionMenu()
  emit('archive-session', id)
}

// 响应式窗口宽度（避免模板直接访问 window）
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1200)
function onResize() { windowWidth.value = window.innerWidth }
// 点击侧栏外任意位置收起操作菜单；菜单内按钮已 stopPropagation，不会误触发收起
onMounted(() => {
  window.addEventListener('resize', onResize)
  document.addEventListener('click', closeActionMenu)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('click', closeActionMenu)
})

const isMobile = computed(() => windowWidth.value <= 860)

// ---- 多选模式 ----
const selectMode = ref(false)
const selectedIds = ref([])  // 用 Array 而非 Set，保证响应式稳定

function toggleSelectMode() {
  selectMode.value = !selectMode.value
  selectedIds.value = []
}

function toggleSelect(id) {
  const idx = selectedIds.value.indexOf(id)
  if (idx >= 0) selectedIds.value.splice(idx, 1)
  else selectedIds.value.push(id)
}

function selectAll() {
  selectedIds.value = props.sessions.map(s => s.id)
}

function clearSelection() {
  selectedIds.value = []
}

function isSelected(id) {
  return selectedIds.value.includes(id)
}

function onSelect(id) {
  closeActionMenu()
  if (selectMode.value) {
    toggleSelect(id)
  } else {
    emit('select-session', id)
    if (isMobile.value) emit('close')
  }
}

function onRename(e, s) {
  e.stopPropagation()
  const current = s.title || s.id
  const title = window.prompt('重命名会话（留空恢复原始 ID）', current)
  if (title === null) return // 用户点了取消
  const trimmed = title.trim()
  if (trimmed === current) return // 没变
  emit('rename-session', s.id, trimmed)
}

function onBulkDelete() {
  const ids = selectedIds.value
  if (!ids.length) return
  const msg = `确定删除选中的 ${ids.length} 个会话？记录将归档到 logs/ 目录。`
  if (window.confirm(msg)) {
    emit('bulk-delete', ids)
    selectedIds.value = []
    selectMode.value = false
  }
}
</script>

<template>
  <!-- 移动端遮罩（仅抽屉打开时显示） -->
  <div v-if="open && isMobile" class="backdrop" @click="emit('close')" />

  <aside class="sidebar" :class="{ open }" aria-label="侧边栏">
    <!-- Logo 区域 -->
    <div class="logo-wrap">
      <a class="logo" href="#/chat">
        <img class="logo-mark" :src="logoUrl" alt="CrawAgent" width="34" height="34" />
        <span>
          <span class="logo-name">CrawAgent</span>
          <span class="logo-tag">吐丝 · 结网</span>
        </span>
      </a>
      <!-- 收起会话列表（桌面端折叠为 0 宽，移动端合上抽屉） -->
      <button type="button" class="collapse-btn" title="收起侧栏" aria-label="收起侧栏" @click="emit('close')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
    </div>

    <!-- 新对话按钮 -->
    <div class="actions">
      <button type="button" class="btn-new" @click="emit('new-chat')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        新对话
      </button>
    </div>

    <!-- 会话列表 -->
    <div class="list-wrap">
      <div class="list-head">
        <span class="list-title">{{ selectMode ? '选择会话' : '会话' }}</span>
        <button
          v-if="!selectMode && sessions.length"
          type="button"
          class="btn-select"
          title="批量删除"
          @click="toggleSelectMode"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="9 11 12 14 22 4" />
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
        </button>
        <span v-else class="list-count">{{ selectMode ? selectedIds.length + '/' + sessions.length : sessions.length }}</span>
        <button
          v-if="selectMode"
          type="button"
          class="btn-cancel-select"
          @click="toggleSelectMode"
        >取消</button>
      </div>

      <!-- 批量操作栏 -->
      <div v-if="selectMode" class="bulk-bar">
        <button type="button" class="bulk-btn" @click="selectAll">全选</button>
        <button type="button" class="bulk-btn" @click="clearSelection">清空</button>
        <button
          type="button"
          class="bulk-btn bulk-delete"
          :disabled="!selectedIds.length"
          @click="onBulkDelete"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6" />
            <path d="M10 11v6M14 11v6" />
            <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
          </svg>
          删除 {{ selectedIds.length }}
        </button>
      </div>

      <p v-if="!sessions.length" class="empty-tip">
        还没有历史会话。<br>终端里聊过的也会出现在这里。
      </p>

      <ul v-else class="session-list">
        <li
          v-for="s in sessions"
          :key="s.id"
          class="session-row"
          :class="{ active: s.id === activeId, selected: selectMode && isSelected(s.id) }"
        >
          <label v-if="selectMode" class="session-check" @click.stop>
            <input type="checkbox" :checked="isSelected(s.id)" @change="toggleSelect(s.id)" />
          </label>
          <button type="button" class="session-item" @click="onSelect(s.id)">
            <svg class="session-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <div class="session-text">
              <span class="session-id">{{ s.title || s.id }}</span>
              <span class="session-preview">{{ s.preview || '（空会话）' }}</span>
            </div>
          </button>
          <!-- 默认操作：重命名 + 删除（点击展开菜单） -->
          <template v-if="!selectMode && actionMenuId !== s.id">
            <button type="button" class="session-act session-ren" title="重命名" @click="e => onRename(e, s)">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
              </svg>
            </button>
            <button type="button" class="session-act session-del" title="删除会话" @click="e => toggleActionMenu(e, s.id)">×</button>
          </template>
          <!-- 展开菜单：归档（蓝） + 删除（红） -->
          <template v-else-if="!selectMode && actionMenuId === s.id">
            <button type="button" class="session-act-btn act-archive" title="归档到 logs/ 目录" @click="e => onConfirmArchive(e, s.id)">归档</button>
            <button type="button" class="session-act-btn act-delete" title="彻底删除，不可恢复" @click="e => onConfirmDelete(e, s.id)">删除</button>
          </template>
        </li>
      </ul>
    </div>

    <!-- 底部功能入口：站点档案 + 设置 -->
    <div class="foot-wrap">
      <button
        type="button"
        class="foot-btn"
        :class="{ active: isSitesPage }"
        @click="emit('open-sites')"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <line x1="2" y1="12" x2="22" y2="12" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
        <span>站点档案</span>
      </button>

      <button
        type="button"
        class="foot-btn"
        :class="{ active: isSettingsPage }"
        @click="emit('open-settings')"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
        <span>设置</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  flex: none;
  width: 260px;
  border-right: 1px solid var(--line);
  background: var(--panel);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: transform .28s var(--ease), width .28s var(--ease);
}

/* 桌面端收起：宽度折叠为 0（移动端走上面的 transform 抽屉） */
@media (min-width: 861px) {
  .sidebar:not(.open) {
    width: 0;
    border-right-color: transparent;
  }
}

/* 桌面端默认打开，移动端通过 transform 控制 */
@media (max-width: 860px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 30;
    width: min(280px, 85vw);
    box-shadow: var(--shadow);
    transform: translateX(-102%);
  }
  .sidebar.open { transform: none; }
}

.backdrop {
  position: fixed;
  inset: 0;
  z-index: 25;
  background: rgba(0, 0, 0, .45);
  animation: fadein .2s ease both;
}
@keyframes fadein { from { opacity: 0; } to { opacity: 1; } }

/* ---------- Logo ---------- */
.logo-wrap {
  flex: none;
  position: relative;
  padding: 16px 16px 8px;
  border-bottom: 1px solid var(--line);
}
.collapse-btn {
  position: absolute;
  top: 14px;
  right: 10px;
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  color: var(--faint);
  border-radius: 7px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: color .15s, background .15s;
}
.collapse-btn:hover {
  color: var(--accent);
  background: var(--hover);
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  color: var(--ink);
}
.logo-mark {
  flex: none;
  width: 34px;
  height: 34px;
  display: block;
  border-radius: 8px;
  object-fit: contain;
}
.logo-name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 19px;
  letter-spacing: .01em;
  display: block;
  line-height: 1.1;
}
.logo-tag {
  font-size: 11.5px;
  color: var(--faint);
  letter-spacing: .16em;
  display: block;
  margin-top: 1px;
}

/* ---------- 新对话 ---------- */
.actions {
  flex: none;
  padding: 12px;
}
.btn-new {
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 9px 14px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--accent) 50%, transparent);
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
  font-size: 13.5px;
  cursor: pointer;
  transition: background .15s, transform .1s;
}
.btn-new:hover { background: color-mix(in srgb, var(--accent-soft) 70%, white); }
.btn-new:active { transform: scale(.98); }

/* ---------- 列表区 ---------- */
.list-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.list-head {
  flex: none;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 6px 6px 0;
  position: sticky;
  top: 0;
  background: var(--panel);
  z-index: 1;
  padding-top: 10px;
}
.list-title {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: .16em;
  text-transform: uppercase;
  color: var(--faint);
}
.list-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent);
}
.empty-tip {
  padding: 14px 10px;
  font-size: 13px;
  color: var(--faint);
  line-height: 1.7;
  border: 1px dashed var(--line);
  border-radius: 10px;
}

.session-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 0;
  padding: 0;
}
.session-row {
  position: relative;
}
.session-item {
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 10px;
  padding: 9px 56px 9px 10px;
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 9px;
  min-height: 44px;
  transition: background .15s, border-color .15s;
}
.session-item:hover { background: var(--hover); }
.session-row.active .session-item {
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}
.session-icon {
  flex: none;
  margin-top: 2px;
  color: var(--dim);
}
.session-row.active .session-icon { color: var(--accent); }
.session-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.session-id {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  line-height: 1.2;
}
.session-preview {
  font-size: 12.5px;
  color: var(--dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.3;
}
.session-act {
  position: absolute;
  top: 10px;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: var(--faint);
  font-size: 16px;
  line-height: 1;
  border-radius: 6px;
  cursor: pointer;
  opacity: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: opacity .15s, color .15s, background .15s;
}
.session-row:hover .session-act { opacity: 1; }
.session-ren { right: 34px; }
.session-del { right: 8px; }
.session-del:hover { color: var(--danger); background: var(--danger-soft); }
.session-ren:hover { color: var(--accent); background: var(--hover); }

/* 展开后的文字操作按钮 */
.session-act-btn {
  position: absolute;
  top: 10px;
  height: 22px;
  padding: 0 8px;
  border: none;
  border-radius: 6px;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: opacity .15s, filter .15s;
  opacity: 1;
  white-space: nowrap;
}
.session-row:hover .session-act-btn { opacity: 1; }
.act-archive {
  right: 52px;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
}
.act-archive:hover { filter: brightness(1.1); }
.act-delete {
  right: 8px;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
}
.act-delete:hover { filter: brightness(1.1); }

/* ---------- 多选模式 ---------- */
.list-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.btn-select {
  flex: none;
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--faint);
  border-radius: 6px;
  cursor: pointer;
  transition: color .15s, background .15s;
}
.btn-select:hover { color: var(--accent); background: var(--hover); }
.btn-cancel-select {
  flex: none;
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.btn-cancel-select:hover { background: var(--hover); }
.bulk-bar {
  display: flex;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.bulk-btn {
  flex: none;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--dim);
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background .15s, color .15s, border-color .15s;
}
.bulk-btn:hover { background: var(--hover); color: var(--ink); }
.bulk-delete {
  margin-left: auto;
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
  color: var(--danger);
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.bulk-delete:hover {
  background: var(--danger-soft);
  border-color: var(--danger);
}
.bulk-delete:disabled {
  opacity: .4;
  cursor: not-allowed;
}
.bulk-delete:disabled:hover {
  background: transparent;
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
}
.session-check {
  flex: none;
  margin-top: 4px;
  margin-right: 2px;
  cursor: pointer;
}
.session-check input[type="checkbox"] {
  width: 15px;
  height: 15px;
  accent-color: var(--accent);
  cursor: pointer;
}
.session-row.selected .session-item {
  background: color-mix(in srgb, var(--accent) 15%, transparent);
  border-color: color-mix(in srgb, var(--accent) 30%, transparent);
}

/* ---------- 底部入口 ---------- */
.foot-wrap {
  flex: none;
  padding: 10px;
  border-top: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.foot-btn {
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  color: var(--dim);
  font: inherit;
  font-size: 13.5px;
  font-weight: 500;
  padding: 9px 12px;
  border-radius: 9px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  transition: background .15s, color .15s;
}
.foot-btn:hover {
  background: var(--hover);
  color: var(--ink);
}
.foot-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

/* 滚动条 */
.list-wrap::-webkit-scrollbar { width: 6px; }
.list-wrap::-webkit-scrollbar-track { background: transparent; }
.list-wrap::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--silk) 50%, transparent);
  border-radius: 999px;
}
</style>
