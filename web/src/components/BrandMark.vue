<script setup>
// CrawAgent 品牌标识 BrandMark — 像素数码蜘蛛
// 8-bit 像素蜘蛛悬挂在蛛丝上，周围是淡蛛网角标；呼应「爬虫 / 吐丝结网」。
// 全部颜色取 CSS 变量，自动适配明/暗主题；animated 时蜘蛛沿丝微浮 + 发光脉冲。
defineProps({
  size: { type: [Number, String], default: 34 },
  animated: { type: Boolean, default: false },
})
</script>

<template>
  <svg
    class="brand-mark"
    :class="{ 'is-animated': animated }"
    :width="size"
    :height="size"
    viewBox="0 0 48 48"
    fill="none"
    role="img"
    aria-label="CrawAgent"
  >
    <!-- 蛛网放射虚线 -->
    <g class="bm-web" stroke="var(--accent)" stroke-width="1" stroke-dasharray="1.5 5.5" opacity=".3">
      <path d="M24 24 L4 4" />
      <path d="M24 24 L44 4" />
      <path d="M24 24 L4 44" />
      <path d="M24 24 L44 44" />
    </g>

    <!-- 像素蜘蛛主体（丝 + 头 + 身体 + 八腿 + 高光） -->
    <g class="bm-spider" transform="translate(0 6)">
      <!-- 悬挂丝 -->
      <rect x="18" y="3" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="3" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="6" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="6" width="3" height="3" fill="var(--accent)"/>
      <!-- 头 -->
      <rect x="15" y="9" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="9" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="9" width="3" height="3" fill="var(--accent)"/>
      <rect x="24" y="9" width="3" height="3" fill="var(--accent)"/>
      <rect x="15" y="12" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="12" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="12" width="3" height="3" fill="var(--accent)"/>
      <rect x="24" y="12" width="3" height="3" fill="var(--accent)"/>
      <!-- 腿对 + 身体 -->
      <rect x="3" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="6" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="15" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="24" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="36" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="39" y="15" width="3" height="3" fill="var(--accent)"/>
      <rect x="0" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="6" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="15" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="24" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="33" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="39" y="18" width="3" height="3" fill="var(--accent)"/>
      <rect x="3" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="6" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="12" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="15" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="24" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="27" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="33" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="36" y="21" width="3" height="3" fill="var(--accent)"/>
      <rect x="0" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="6" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="15" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="24" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="33" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="39" y="24" width="3" height="3" fill="var(--accent)"/>
      <rect x="3" y="27" width="3" height="3" fill="var(--accent)"/>
      <rect x="6" y="27" width="3" height="3" fill="var(--accent)"/>
      <rect x="18" y="27" width="3" height="3" fill="var(--accent)"/>
      <rect x="21" y="27" width="3" height="3" fill="var(--accent)"/>
      <rect x="36" y="27" width="3" height="3" fill="var(--accent)"/>
      <rect x="39" y="27" width="3" height="3" fill="var(--accent)"/>
      <!-- 高光像素 -->
      <rect x="12" y="21" width="3" height="3" fill="var(--accent-strong)"/>
      <rect x="24" y="12" width="3" height="3" fill="var(--accent-strong)"/>
    </g>

    <!-- 四角淡蛛网角标 -->
    <g class="bm-corner">
      <rect x="0" y="0" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="3" y="0" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="0" y="3" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="45" y="0" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="42" y="0" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="45" y="3" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="0" y="45" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="3" y="45" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="0" y="42" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="45" y="45" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="42" y="45" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
      <rect x="45" y="42" width="3" height="3" fill="var(--accent)" opacity="0.4"/>
    </g>
  </svg>
</template>

<style scoped>
.brand-mark {
  display: block;
  flex: none;
}
.is-animated .bm-spider {
  transform-origin: 24px 27px;
  animation: bm-float 3.2s ease-in-out infinite;
}
.is-animated {
  animation: bm-breathe 3.6s ease-in-out infinite;
}
@keyframes bm-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(1.6px); }
}
@keyframes bm-breathe {
  0%, 100% { filter: drop-shadow(0 0 0 transparent); }
  50% { filter: drop-shadow(0 0 10px color-mix(in srgb, var(--accent) 45%, transparent)); }
}
@media (prefers-reduced-motion: reduce) {
  .is-animated .bm-spider,
  .is-animated { animation: none; }
}
</style>
