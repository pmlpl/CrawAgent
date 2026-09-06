<script setup>
// AskCard — ask_user 选择题卡片：AI 发起的问题 + 选项按钮，用户点击作答。
// 点击后按钮锁定并高亮所选项；超时（后端 ask_answered value=null）整体置灰。
// 刷新后由事件日志重放恢复，已答状态随 ask_answered 事件回放。
import { computed } from 'vue'
import { useChat } from '../composables/useChat'

const props = defineProps({
  item: { type: Object, required: true }, // {askId, question, options, answered}
})

const chat = useChat()

const pending = computed(() => props.item.answered === undefined)
const timedOut = computed(() => props.item.answered === null)

function pick(option) {
  if (!pending.value) return
  chat.answerAsk(props.item.askId, option)
}
</script>

<template>
  <div class="ask-card" :class="{ resolved: !pending, timeout: timedOut }">
    <div class="ask-q">
      <span class="ask-icon">?</span>
      <span class="ask-text">{{ item.question }}</span>
    </div>
    <div class="ask-options">
      <button
        v-for="opt in item.options"
        :key="opt"
        type="button"
        class="ask-opt"
        :class="{ picked: item.answered === opt }"
        :disabled="!pending"
        @click="pick(opt)"
      >{{ opt }}</button>
    </div>
    <p v-if="timedOut" class="ask-note">用户未在时限内选择，AI 已按暂缓处理</p>
  </div>
</template>

<style scoped>
.ask-card {
  border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  background: color-mix(in srgb, var(--accent) 7%, transparent);
  border-radius: var(--radius);
  padding: 12px 16px;
  max-width: 720px;
}
.ask-card.resolved { opacity: .82; }
.ask-card.timeout { opacity: .6; }

.ask-q {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}
.ask-icon {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--accent);
  color: var(--accent-ink);
  font-weight: 700;
  font-size: 13px;
  display: grid;
  place-items: center;
  align-self: center;
}
.ask-text { font-size: 14px; color: var(--ink); line-height: 1.5; }

.ask-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-left: 32px;
}
.ask-opt {
  appearance: none;
  border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
  background: var(--panel);
  color: var(--ink);
  border-radius: 999px;
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: border-color .15s, background .15s, transform .1s;
  white-space: nowrap;
}
.ask-opt:hover:not(:disabled) { border-color: var(--accent); background: var(--accent-soft); }
.ask-opt:active:not(:disabled) { transform: scale(.97); }
.ask-opt.picked {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-ink);
}
.ask-opt:disabled { cursor: default; }

.ask-note {
  margin: 8px 0 0;
  padding-left: 32px;
  font-size: 12px;
  color: var(--faint);
}
</style>
