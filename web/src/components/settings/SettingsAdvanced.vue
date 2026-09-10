<script setup>
// 「高级」页签：抓取参数（超时/节流）、产物目录（可改存放位置 + 打开）、保留清理策略。
// 值都存 .env：抓取参数保存即生效；目录保存即解析绝对路径并创建，下一次抓取立即生效（无需重启）；
// 清理在每次后端启动时执行，本页只编辑策略数值。
// 每张卡有自己的保存提示（saveTip 本地化，避免跨卡串显）。
import { ref } from 'vue'
import { useSettings } from '../../composables/useSettings'

const { state, load, saveSettingsFields, openEcoFolder } = useSettings()

const crawlTip = ref(null)    // {ok, msg}
const retainTip = ref(null)
const dirTip = ref(null)
const langTip = ref(null)
const savingCrawl = ref(false)
const savingRetain = ref(false)
const savingDirs = ref(false)
const savingLang = ref(false)

// 空 input ↔ null（不清理/不设上限）互转
const toNumOrNull = (v, kind = Number) => (v === '' || v === null || v === undefined ? null : kind(v))

function _toTip(res, okMsg) {
  return res.ok ? { ok: true, msg: okMsg } : { ok: false, msg: '保存失败：' + (res.error || '未知错误') }
}

async function saveCrawl() {
  if (savingCrawl.value) return
  savingCrawl.value = true
  try {
    const res = await saveSettingsFields({
      request_timeout: toNumOrNull(state.adv.timeout),
      request_delay: toNumOrNull(state.adv.delay),
    })
    crawlTip.value = _toTip(res, '✓ 抓取参数已保存并生效')
  } finally {
    savingCrawl.value = false
  }
}

async function saveRetention() {
  if (savingRetain.value) return
  savingRetain.value = true
  try {
    const res = await saveSettingsFields({
      logs_retention_days: toNumOrNull(state.adv.logsDays, parseInt),
      output_retention_days: toNumOrNull(state.adv.outputDays, parseInt),
      downloads_retention_days: toNumOrNull(state.adv.downloadsDays, parseInt),
      output_max_size_gb: toNumOrNull(state.adv.outputCapGb),
      downloads_max_size_gb: toNumOrNull(state.adv.downloadsCapGb),
    })
    retainTip.value = _toTip(res, '✓ 保留策略已保存（下次启动清理时应用）')
  } finally {
    savingRetain.value = false
  }
}

async function saveDirs() {
  if (savingDirs.value) return
  savingDirs.value = true
  try {
    const res = await saveSettingsFields({
      output_dir: state.adv.outputDir.trim(),
      downloads_dir: state.adv.downloadsDir.trim(),
    })
    dirTip.value = _toTip(res, '✓ 目录已保存并生效（下一次抓取即用新位置）')
    if (res.ok) await load() // 回读快照，把可能填的相对路径规范成服务端解析后的绝对路径
  } finally {
    savingDirs.value = false
  }
}

async function saveLangsmith() {
  if (savingLang.value) return
  // 前端校验：勾追踪但 key 空时拦下（后端守门兜底，早报优于静默无效）
  if (state.langsmith.tracing && !state.langsmith.apiKey.trim()) {
    langTip.value = { ok: false, msg: '启用追踪需先填 API Key' }
    return
  }
  savingLang.value = true
  try {
    const res = await saveSettingsFields({
      langsmith_api_key: state.langsmith.apiKey,
      langsmith_project: state.langsmith.project,
      langsmith_tracing: state.langsmith.tracing,
    })
    langTip.value = _toTip(res, '✓ LangSmith 配置已保存，重启 CrawAgent 后追踪生效')
    if (res.ok) await load() // 回读脱敏 key 回显
  } finally {
    savingLang.value = false
  }
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
        <button type="button" class="btn-primary" :disabled="savingCrawl" @click="saveCrawl">
          <span v-if="savingCrawl" class="spin" />保存
        </button>
      </div>
      <div v-if="crawlTip" class="result" :class="crawlTip.ok ? 'ok' : 'err'" style="margin-top: 8px">{{ crawlTip.msg }}</div>
    </div>
  </section>

  <section class="card">
    <h2 class="card-title">产物目录</h2>
    <div class="field" style="margin-bottom: 14px">
      <div class="list-head">
        <span class="label">文本产物（md / txt / json）存放位置</span>
        <button type="button" class="btn-ghost btn-sm" @click="openEcoFolder('output')">打开目录</button>
      </div>
      <input v-model="state.adv.outputDir" placeholder="项目根/output" spellcheck="false" autocomplete="off" />
    </div>
    <div class="field" style="margin-bottom: 14px">
      <div class="list-head">
        <span class="label">媒体下载（图片 / 视频 / 音频）存放位置</span>
        <button type="button" class="btn-ghost btn-sm" @click="openEcoFolder('downloads')">打开目录</button>
      </div>
      <input v-model="state.adv.downloadsDir" placeholder="项目根/downloads" spellcheck="false" autocomplete="off" />
    </div>
    <div class="actions" style="justify-content: flex-start">
      <button type="button" class="btn-primary" :disabled="savingDirs" @click="saveDirs">
        <span v-if="savingDirs" class="spin" />保存目录
      </button>
    </div>
    <div v-if="dirTip" class="result" :class="dirTip.ok ? 'ok' : 'err'" style="margin-top: 8px">{{ dirTip.msg }}</div>
    <p class="hint">可填绝对路径或相对项目根的路径；保存即写入 .env、自动创建目录，下一次抓取立即生效（无需重启）。「打开目录」始终跟随最新设置。</p>
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
      <button type="button" class="btn-primary" :disabled="savingRetain" @click="saveRetention">
        <span v-if="savingRetain" class="spin" />保存
      </button>
    </div>
    <div v-if="retainTip" class="result" :class="retainTip.ok ? 'ok' : 'err'" style="margin-top: 8px">{{ retainTip.msg }}</div>
  </section>

  <section class="card">
    <h2 class="card-title">LangSmith 追踪</h2>
    <p class="hint" style="margin-bottom: 12px">LangChain 链路追踪（可选调试）。配置写入 .env，但 langchain 运行时在进程启动时读取 env，因此保存后需重启 <code>crawagent start</code> 才生效。</p>
    <div class="field" style="max-width: 360px">
      <label class="field">
        <span class="label">API Key</span>
        <input type="password" v-model="state.langsmith.apiKey" placeholder="未配置" spellcheck="false" autocomplete="off" />
        <span class="hint">已配置时回显脱敏掩码（****xxxx）；含 * 视为掩码不回写。留空 = 不改。</span>
      </label>
      <label class="field">
        <span class="label">项目名</span>
        <input type="text" v-model="state.langsmith.project" placeholder="crawagent" spellcheck="false" autocomplete="off" />
        <span class="hint">LangSmith 项目名，默认 crawagent</span>
      </label>
      <label class="field" style="flex-direction: row; align-items: center; gap: 8px">
        <input type="checkbox" v-model="state.langsmith.tracing" style="width: auto" />
        <span class="label" style="margin: 0">启用链路追踪</span>
      </label>
      <div class="actions" style="justify-content: flex-start">
        <button type="button" class="btn-primary" :disabled="savingLang" @click="saveLangsmith">
          <span v-if="savingLang" class="spin" />保存
        </button>
      </div>
      <div v-if="langTip" class="result" :class="langTip.ok ? 'ok' : 'err'" style="margin-top: 8px">{{ langTip.msg }}</div>
    </div>
  </section>
</template>

<style scoped>
.adv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 4px;
}
</style>
