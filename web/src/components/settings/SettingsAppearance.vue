<script setup>
// 「界面外观」页签：启动浏览器选择 + 聊天背景图（服务端 data/ 持久化 [ADR-0001]）+ 遮罩浓度
import { ref } from 'vue'
import { useSettings } from '../../composables/useSettings'
import { useBg } from '../../composables/useBg'

const { state, saveStartBrowser } = useSettings()

// 浏览器选项（start 自动弹窗用；存 .env 的 START_BROWSER）
const BROWSER_OPTIONS = [
  { value: '', label: '系统默认' },
  { value: 'chrome', label: '谷歌 Chrome' },
  { value: 'msedge', label: '微软 Edge' },
  { value: 'firefox', label: '火狐 Firefox' },
]

// ---------- 背景图 ----------
const { bgImage, bgOpacity, setBg, setBgOpacity } = useBg()
const bgFile = ref(null)
const bgUploading = ref(false)
const bgClearing = ref(false)
const bgError = ref('') // 上传/压缩/保存失败的显式提示（失败必须报出来，不许静默吞掉）

// 压缩参数：最长边 1920px + JPEG q0.82 → 产物 ~200-400KB，控制上传请求体与磁盘占用
const BG_MAX_DIM = 1920
const BG_QUALITY = 0.82

function compressImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      try {
        const scale = Math.min(1, BG_MAX_DIM / Math.max(img.naturalWidth, img.naturalHeight))
        const w = Math.max(1, Math.round(img.naturalWidth * scale))
        const h = Math.max(1, Math.round(img.naturalHeight * scale))
        const canvas = document.createElement('canvas')
        canvas.width = w
        canvas.height = h
        const ctx = canvas.getContext('2d')
        ctx.fillStyle = '#ffffff' // 透明底垫白，避免 JPEG 转码后透明区变黑
        ctx.fillRect(0, 0, w, h)
        ctx.drawImage(img, 0, 0, w, h)
        resolve(canvas.toDataURL('image/jpeg', BG_QUALITY))
      } catch (e) {
        reject(e)
      }
    }
    img.onerror = () => reject(new Error('图片解码失败（文件可能已损坏）'))
    img.src = dataUrl
  })
}

function onOpacityInput(e) {
  setBgOpacity(e.target.value)
}

function _readAsDataURL(f) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.readAsDataURL(f)
  })
}

async function onBgPick(e) {
  const f = e.target.files?.[0]
  if (!f) return
  bgError.value = ''
  if (!f.type.startsWith('image/')) {
    bgError.value = `不支持的文件：${f.type || '浏览器无法识别该扩展名的类型'}。支持 jpg / png / webp / gif / bmp / avif 等常见图片格式`
    return
  }
  bgUploading.value = true
  try {
    const rawUrl = await _readAsDataURL(f)
    // 统一走 canvas 压缩：不限制来源体积，顺带把 webp/avif 等转码成通用 JPEG
    const compressed = await compressImage(rawUrl)
    const ok = await setBg(compressed)
    if (!ok) {
      bgError.value = '背景已应用，但保存到服务端失败，刷新后会丢失（请检查后端是否在运行）'
    }
  } catch (err) {
    bgError.value = '设置背景失败：' + (err?.message || err)
  } finally {
    bgUploading.value = false
    if (bgFile.value) bgFile.value.value = ''
  }
}

async function clearBg() {
  bgClearing.value = true
  try {
    await setBg('')
    bgError.value = ''
  } finally {
    bgClearing.value = false
  }
  if (bgFile.value) bgFile.value.value = ''
}
</script>

<template>
  <section class="card">
    <h2 class="card-title">界面</h2>

    <div class="field" style="margin-bottom:16px">
      <span class="label">启动时自动打开的浏览器</span>
      <select
        :value="state.startBrowser"
        style="max-width: 260px"
        @change="saveStartBrowser($event.target.value)"
      >
        <option v-for="b in BROWSER_OPTIONS" :key="b.value" :value="b.value">{{ b.label }}</option>
      </select>
      <p class="hint">选择 <code class="mono">crawagent start</code> 启动时自动弹出的浏览器（保存即生效，下次启动时应用）；选定的浏览器未安装时回退系统默认</p>
    </div>

    <div class="field">
      <span class="label">背景图</span>
      <p class="hint">上传一张图片作为聊天页背景板（保存到本机服务端 data/，localhost 与 127.0.0.1、不同浏览器均共享同一份）。大图自动压缩到 1920px 内再上传，jpg/png/webp/gif/bmp/avif 均可</p>
      <div class="bg-row">
        <input
          ref="bgFile"
          type="file"
          accept="image/*"
          class="file-input"
          @change="onBgPick"
        />
        <button v-if="bgImage" type="button" class="btn-ghost" :disabled="bgClearing" @click="clearBg">
          <span v-if="bgClearing" class="spin" />清除背景
        </button>
      </div>
      <div v-if="bgUploading" class="hint">处理中…</div>
      <div v-if="bgError" class="result err" style="margin-top:6px">{{ bgError }}</div>
      <div v-if="bgImage" class="bg-preview" :style="{ backgroundImage: `url(${bgImage})` }" />
      <div v-if="bgImage" class="bg-opacity-row">
        <span class="label">遮罩浓度 <b class="mono">{{ bgOpacity }}%</b></span>
        <input
          type="range"
          min="0"
          max="95"
          step="5"
          :value="bgOpacity"
          aria-label="遮罩浓度"
          @input="onOpacityInput"
        />
        <p class="hint">越高文字越清晰、背景越淡。开启背景后各卡片自动变毛玻璃（半透明 + 模糊），文字不透底、背景也看得见</p>
      </div>
    </div>
  </section>
</template>
