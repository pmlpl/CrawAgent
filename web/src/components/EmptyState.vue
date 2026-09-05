<script setup>
import heroVideo from '../assets/brand-spider.mp4'

const emit = defineEmits(['suggest'])

// 能力标签：与后端实际装配的工具/机制一一对应（见 README 工具列表）
const capabilities = [
  '静态页直取',
  '浏览器动态渲染',
  '正文 / 列表抽取',
  '社媒 · 视频解析',
  '脚本失败兜底',
  '站点档案',
  '入库与文件留存',
  'MCP 扩展',
]

const suggestions = [
  '抓取这篇文章的正文并总结',
  '列出这个目录页的全部章节链接',
  '下载这个抖音链接的视频',
  '提取这本微信读书的章节正文',
  '这个页面需要登录，写脚本帮我爬下来',
  '我以前抓过哪些资源？',
]
</script>

<template>
  <section class="empty">
    <div class="hero">
      <video :src="heroVideo" autoplay loop muted playsinline disablepictureinpicture class="hero-gif" />
    </div>
    <h1>给 Agent 一个 <em>目标</em>，<br>从抓取到落库，它自主跑完。</h1>
    <p class="lede">20+ 工具由 LangGraph 自主编排：会选路、会兜底，执行过程全程可见。</p>
    <ul class="caps">
      <li v-for="c in capabilities" :key="c">{{ c }}</li>
    </ul>
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
  max-width: 660px;
}
.hero {
  position: relative;
  width: 140px;
  margin: 0 auto 18px;
  display: grid;
  place-items: center;
}
.hero-gif {
  width: 100%;
  height: auto;
  display: block;
  border-radius: 16px;
  image-rendering: pixelated;
}
.hero::before {
  content: "";
  position: absolute;
  inset: -26px;
  border-radius: 50%;
  background: radial-gradient(circle,
    color-mix(in srgb, var(--accent) 16%, transparent) 0%,
    transparent 68%);
  pointer-events: none;
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
.lede {
  color: var(--dim);
  margin: 12px 0 16px;
  font-size: 15px;
}
.caps {
  list-style: none;
  margin: 0 0 26px;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.caps li {
  font-size: 12px;
  line-height: 1;
  color: var(--dim);
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 999px;
  padding: 6px 11px;
  letter-spacing: .02em;
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
