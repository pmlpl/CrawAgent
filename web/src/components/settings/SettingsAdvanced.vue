<script setup>
// 「高级」页签：抓取参数（超时/节流）、产物目录（展示+打开）、保留清理策略。
// 值都存 .env，保存即生效（抓取参数）；目录本身走 .env 的 OUTPUT_DIR/DOWNLOADS_DIR 改（重启生效），
// 这里只展示与直达；清理在每次后端启动时执行，本页只编辑策略数值。
// 每张卡有自己的保存提示（saveTip 本地化，避免跨卡串显）。
import { ref } from 'vue'
import { useSettings } from '../../composables/useSettings'

const { state, saveSettingsFields, openEcoFolder } = useSettings()

const crawlTip = ref(null)    // {ok, msg}
const retainTip = ref(null)

// 空 input ↔ null（不清理/不设上限）互转
const toNumOrNull = (v, kind = Number) => (v === '' || v === null || v === undefined ? null : kind(v))

function _tip(res, okMsg) {
  return res.ok ? { ok: true, msg: okMsg } : { ok: false, msg: '保存失败：' + (res.error || '未知错误') }
}

async function saveCrawl() {
  state.saveTip = ''
  const res = await saveSettingsFields({
    request_timeout: toNumOrNull(state.adv.timeout),
    request_delay: toNumOrNull(state.adv.delay),
  })
  crawlTip.value = _tip(res, '✓ 抓取参数已保存并生效')
}

async function saveRetention() {
  state.saveTip = ''
  const res = await saveSettingsFields({
    logs_retention_days: toNumOrNull(state.adv.logsDays, parseInt),
    output_retention_days: toNumOrNull(state.adv.outputDays, parseInt),
    downloads_retention_days: toNumOrNull(state.adv.downloadsDays, parseInt),
    output_max_size_gb: toNumOrNull(state.adv.outputCapGb),
    downloads_max_size_gb: toNumOrNull(state.adv.downloadsCapGb),
  })
  retainTip.value = _tip(res, '✓ 保留策略已保存（下次启动清理时应用）')
}
</script>

<template>
  <section class="card">
    <h2 class="card-title">抓取行为</h2>
    <div class="field" style="max-width: 320px">
      <label class="field">
        <span class="label">抓取超时（秒）</span>
        <input type="number" min="5" max="300" step="1" v-model="state.adv.timeout" />
        <span class="hint">单次 HTTP 请求的最长等待时间（5-300）</span>
      </label>
      <label class="field">
        <span class="label">请求间隔（秒）</span>
        <input type="number" min="0" max="60" step="0.5" v-model="state.adv.delay" />
        <span class="hint">两次请求之间的强制间隔，防高频被封（0-60，默认 1）</span>
      </label>
      <div class="actions" style="justify-content: flex-start">
        <button type="button" class="btn-primary" :disabled="state.saving" @click="saveCrawl">
          <span v-if="state.saving" class="spin" />保存
        </button>
      </div>
      <div v-if="crawlTip" class="result" :class="crawlTip.ok ? 'ok' : 'err'" style="margin-top: 8px">{{ crawlTip.msg }}</div>
    </div>
  </section>

  <section class="card">
    <h2 class="card-title">产物目录</h2>
    <div class="field" style="margin-bottom: 14px">
      <div class="list-head">
        <span class="label">文本产物（md / txt / json）</span>
        <button type="button" class="btn-ghost" style="height: 32px" @click="openEcoFolder('output')">打开目录</button>
      </div>
      <p class="mono dir-path">{{ state.adv.outputDir || '（未获取）' }}</p>
    </div>
    <div class="field">
      <div class="list-head">
        <span class="label">媒体下载（图片 / 视频 / 音频）</span>
        <button type="button" class="btn-ghost" style="height: 32px" @click="openEcoFolder('downloads')">打开目录</button>
      </div>
      <p class="mono dir-path">{{ state.adv.downloadsDir || '（未获取）' }}</p>
    </div>
    <p class="hint">要改目录位置：编辑 .env 的 OUTPUT_DIR / DOWNLOADS_DIR 后重启后端。爬取产物请勿手动删改正在写入的文件。</p>
  </section>

  <section class="card">
    <h2 class="card-title">数据保留与清理</h2>
    <p class="hint" style="margin-bottom: 12px">每次启动后端时按此策略清理过期与超限产物；留空表示该维度不清理 / 不设上限。</p>
    <div class="adv-grid">
      <label class="field">
        <span class="label">日志保留（天）</span>
        <input type="number" min="1" max="3650" step="1" v-model="state.adv.logsDays" placeholder="不清理" />
      </label>
      <label class="field">
        <span class="label">产物保留（天）</span>
        <input type="number" min="1" max="3650" step="1" v-model="state.adv.outputDays" placeholder="不清理" />
      </label>
      <label class="field">
        <span class="label">下载保留（天）</span>
        <input type="number" min="1" max="3650" step="1" v-model="state.adv.downloadsDays" placeholder="不清理" />
      </label>
      <label class="field">
        <span class="label">产物容量上限（GB）</span>
        <input type="number" min="0.1" max="1024" step="0.1" v-model="state.adv.outputCapGb" placeholder="不限" />
      </label>
      <label class="field">
        <span class="label">下载容量上限（GB）</span>
        <input type="number" min="0.1" max="1024" step="0.1" v-model="state.adv.downloadsCapGb" placeholder="不限" />
      </label>
    </div>
    <div class="actions" style="justify-content: flex-start">
      <button type="button" class="btn-primary" :disabled="state.saving" @click="saveRetention">
        <span v-if="state.saving" class="spin" />保存
      </button>
    </div>
    <div v-if="retainTip" class="result" :class="retainTip.ok ? 'ok' : 'err'" style="margin-top: 8px">{{ retainTip.msg }}</div>
  </section>
</template>

<style scoped>
.dir-path {
  font-size: 12px;
  color: var(--dim);
  word-break: break-all;
  padding: 8px 12px;
  background: var(--panel-2);
  border-radius: 8px;
  border: 1px solid var(--line);
}
.adv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 4px;
}
</style>
