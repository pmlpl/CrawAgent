<script setup>
import WebCanvas from './WebCanvas.vue'

const emit = defineEmits(['suggest'])

const suggestions = [
  '抓取这篇文章的正文并总结',
  '列出这个小说目录页的全部章节链接',
  '提取这个 B 站链接的视频信息',
  '我以前抓过哪些资源？',
]
</script>

<template>
  <section class="empty">
    <WebCanvas />
    <h1>给 Spider 一个 <em>URL</em>，<br>它为你织一张信息的网。</h1>
    <p>静态页直取 · 动态页浏览器渲染 · 列表与正文提取 · 社媒下载 · 入库留存</p>
    <div class="suggests">
      <button
        v-for="s in suggestions"
        :key="s"
        class="chip"
        type="button"
        @click="emit('suggest', s)"
      >{{ s }}</button>
    </div>
  </section>
</template>

<style scoped>
.empty {
  margin: auto;
  text-align: center;
  padding: 12px 8px 28px;
  max-width: 620px;
}
.empty h1 {
  font-family: var(--font-display);
  font-size: clamp(26px, 5vw, 38px);
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: .01em;
}
.empty h1 em {
  font-style: normal;
  color: var(--accent);
}
.empty p {
  color: var(--dim);
  margin: 10px 0 22px;
  font-size: 15.5px;
}
.suggests {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
}
.chip {
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--dim);
  border-radius: 999px;
  min-height: 44px;
  padding: 8px 16px;
  font-size: 13.5px;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all .2s;
}
.chip:hover {
  color: var(--accent);
  border-color: var(--accent);
  transform: translateY(-1px);
}
</style>
