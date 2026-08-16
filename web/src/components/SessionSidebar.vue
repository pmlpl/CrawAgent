<script setup>
// 会话侧栏：桌面端固定左栏，移动端为抽屉（open 控制 + 遮罩）。
// 列表数据来自 /api/sessions，CLI 与 WebUI 共享 sessions.db，两端会话互通。
defineProps({
  sessions: { type: Array, default: () => [] }, // [{id, preview}]
  activeId: { type: String, required: true },
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'close', 'delete'])

function pick(id) {
  emit('select', id)
  emit('close')
}

function del(id) {
  if (window.confirm('删除该会话？此操作不可恢复。')) emit('delete', id)
}
</script>

<template>
  <div v-if="open" class="backdrop" @click="emit('close')" />

  <aside class="sidebar" :class="{ open }" aria-label="会话列表">
    <div class="side-head">
      <span class="side-title">会话 · sessions</span>
      <span class="side-count">{{ sessions.length }}</span>
    </div>

    <p v-if="!sessions.length" class="side-empty">
      还没有历史会话。<br>终端里聊过的也会出现在这里。
    </p>

    <ul v-else class="side-list">
      <li v-for="s in sessions" :key="s.id" class="side-row">
        <button
          type="button"
          class="side-item"
          :class="{ active: s.id === activeId }"
          @click="pick(s.id)"
        >
          <span class="side-id">{{ s.id }}</span>
          <span class="side-preview">{{ s.preview || '（空会话）' }}</span>
        </button>
        <button type="button" class="side-del" title="删除会话" @click.stop="del(s.id)">×</button>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.sidebar {
  flex: none;
  width: 250px;
  border-right: 1px solid var(--line);
  padding: 18px 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.side-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0 8px;
}
.side-title {
  font-family: var(--font-mono);
  font-size: 11.5px;
  letter-spacing: .16em;
  text-transform: uppercase;
  color: var(--faint);
}
.side-count {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--accent);
}

.side-empty {
  padding: 8px;
  font-size: 13px;
  color: var(--faint);
  line-height: 1.7;
}

.side-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.side-row { position: relative; }
.side-del {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--faint);
  font-size: 16px;
  line-height: 1;
  border-radius: 6px;
  cursor: pointer;
  opacity: 0;
  transition: opacity .15s, color .15s, background .15s;
}
.side-row:hover .side-del { opacity: 1; }
.side-del:hover { color: var(--danger); background: var(--danger-soft); }
.side-item {
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 10px;
  padding: 9px 12px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 44px;
  transition: background .15s, border-color .15s;
}
.side-item:hover { background: var(--accent-soft); }
.side-item.active {
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}
.side-id {
  font-family: var(--font-mono);
  font-size: 12.5px;
  font-weight: 600;
  color: var(--accent);
}
.side-item.active .side-id { color: var(--accent-strong); }
.side-preview {
  font-size: 12.5px;
  color: var(--dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backdrop { display: none; }

/* 移动端：抽屉形态 */
@media (max-width: 860px) {
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 30;
    width: min(280px, 82vw);
    background: var(--panel);
    transform: translateX(-100%);
    transition: transform .28s var(--ease);
    box-shadow: var(--shadow);
  }
  .sidebar.open { transform: none; }
  .backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 25;
    background: rgba(0, 0, 0, .45);
  }
}
</style>
