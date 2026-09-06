<script setup>
// 「生态扩展」页签：MCP 服务（开关/增删/状态）+ 技能 + 插件（P2-9 生态面板归位）
// 技能与插件为只读展示 + 目录直达；MCP 配置保存即清缓存免重启生效
import { useSettings } from '../../composables/useSettings'

const { state, saveMcp, startAA, toggleServer, removeServer, addMcpServer, openEcoFolder } = useSettings()

// ---------- MCP server 状态文案与配色 ----------
function mcpStatusFor(srv) {
  return state.ecoMcpStatus.find(x => x.name === srv.name) || null
}
function mcpStatusText(srv) {
  if (srv.disabled) return '已停用'
  const st = mcpStatusFor(srv)
  if (!st) return '—'
  if (st.tools != null) return `✓ ${st.tools} 工具`
  if (st.running) return srv.transport === 'stdio' ? '命令就绪 · 调用时自动拉起' : '运行中'
  if (st.running === false) return srv.transport === 'stdio' ? '命令未找到' : '未运行'
  return '—'
}
function mcpStatusClass(srv) {
  if (srv.disabled) return 'off'
  const st = mcpStatusFor(srv)
  if (st?.tools != null) return 'ok'
  if (st?.running) return 'mid'
  return 'warn'
}
</script>

<template>
  <section class="card">
    <h2 class="card-title">MCP 服务</h2>

    <div class="field">
      <div class="bg-row">
        <span class="label">自动启动 MCP 服务</span>
        <button
          type="button"
          class="btn-ghost"
          :disabled="!state.mcpConfigured"
          :style="state.mcpAutostart ? 'color: var(--accent); border-color: var(--accent)' : ''"
          @click="state.mcpAutostart = !state.mcpAutostart"
        >
          {{ state.mcpAutostart ? 'ON' : 'OFF' }}
        </button>
      </div>
      <p class="hint" v-if="!state.mcpConfigured">
        未配置 MCP_SERVERS（.env），开关无效。配置示例：
        [{"name":"anything","transport":"streamable_http","url":"http://127.0.0.1:23816/mcp","headers":{"Authorization":"Bearer xxx"}}]
      </p>
      <p class="hint" v-else-if="state.aaBuiltinFound">
        打开后：Agent 检测到 anything-analyzer 未运行时，会自动在后台启动 Electron 应用（pnpm dev），无需手动开终端
      </p>
      <p class="hint" v-else>
        打开后：Agent 检测到 MCP 服务未运行时，会执行下方自定义命令拉起服务
      </p>

      <!-- 内置 anything-analyzer 状态展示 -->
      <div v-if="state.mcpConfigured && state.aaBuiltinFound" class="builtin-hint">
        <span class="builtin-dot">✓</span>
        <span>已内置 anything-analyzer 启动支持</span>
        <span class="builtin-path">{{ state.aaBuiltinPath }}</span>
      </div>
      <div v-else-if="state.mcpConfigured && !state.aaBuiltinFound && state.aaBuiltinPort === 23816" class="builtin-hint warn">
        <span class="builtin-dot">!</span>
        <span>没找到 anything-analyzer 项目路径</span>
        <span class="builtin-path">请在 .env 里加 ANYTHING_ANALYZER_PATH=你的项目路径</span>
      </div>

      <!-- 启动脚本提示 -->
      <div v-if="state.mcpConfigured" class="builtin-hint" style="margin-top:10px">
        <span class="builtin-dot">ⓘ</span>
        <span>首次启动请双击运行：</span>
        <code class="mono">crawagent\scripts\start_anything_analyzer.bat</code>
        <span>（或拖到浏览器窗口外手动双击）</span>
      </div>
      <button
        v-if="state.mcpConfigured"
        type="button"
        class="btn-primary"
        :disabled="state.saving"
        style="margin-top: 8px"
        @click="startAA"
      >
        <span v-if="state.saving" class="spin" />📂 打开启动脚本文件夹
      </button>
      <p class="hint" v-if="state.saveTip" style="margin-top: 6px">{{ state.saveTip }}</p>

      <!-- 高级模式：自定义命令 -->
      <div v-if="state.mcpConfigured" class="field">
        <button type="button" class="btn-ghost ghost-link" @click="state.advancedMode = !state.advancedMode">
          {{ state.advancedMode ? '收起自定义命令 ▲' : '高级：自定义启动命令 ▼' }}
        </button>
        <textarea
          v-if="state.advancedMode"
          v-model="state.mcpStartCommand"
          rows="2"
          class="mcp-cmd"
          placeholder="留空 = 用内置 anything-analyzer 启动；填入 = 用你自己的命令"
        />
      </div>

      <button
        v-if="state.mcpConfigured"
        type="button"
        class="btn-ghost"
        :disabled="state.saving"
        @click="saveMcp"
      >
        {{ state.saving ? '启动中…' : '保存并启动' }}
      </button>
      <p class="hint" v-if="state.saveTip">{{ state.saveTip }}</p>

      <!-- ===== MCP 服务列表：开关 / 增删 / 状态 ===== -->
      <div class="field" style="margin-top:16px">
        <div class="list-head">
          <span class="label">已配置的 MCP 服务（{{ state.ecoMcpServers.length }}）</span>
          <button type="button" class="btn-ghost" style="height:32px" @click="state.showMcpForm = !state.showMcpForm">
            {{ state.showMcpForm ? '收起表单' : '添加服务' }}
          </button>
        </div>

        <!-- 添加表单 -->
        <form v-if="state.showMcpForm" class="form mcp-add-form" @submit.prevent="addMcpServer">
          <label class="field">
            <span class="label">名称（唯一标识）</span>
            <input v-model="state.mcpForm.name" placeholder="如 firecrawl" autocomplete="off" spellcheck="false" />
          </label>
          <label class="field">
            <span class="label">传输方式</span>
            <select v-model="state.mcpForm.transport">
              <option value="streamable_http">streamable_http（长驻 HTTP 服务，推荐）</option>
              <option value="sse">sse（HTTP 事件流）</option>
              <option value="stdio">stdio（子进程，每次工具调用重启一次）</option>
            </select>
          </label>
          <label v-if="state.mcpForm.transport !== 'stdio'" class="field">
            <span class="label">服务 URL</span>
            <input v-model="state.mcpForm.url" placeholder="http://127.0.0.1:端口/mcp" autocomplete="off" spellcheck="false" />
          </label>
          <template v-else>
            <label class="field">
              <span class="label">启动命令</span>
              <input v-model="state.mcpForm.command" placeholder="完整命令路径，如 {project_root}/.venv/Scripts/python.exe" autocomplete="off" spellcheck="false" />
            </label>
            <label class="field">
              <span class="label">命令参数（空格分隔）</span>
              <input v-model="state.mcpForm.args" placeholder="如 -m mcp_server_fetch" autocomplete="off" spellcheck="false" />
            </label>
          </template>
          <div class="actions" style="justify-content:flex-start">
            <button type="submit" class="btn-primary" style="height:34px" :disabled="state.saving || !state.mcpForm.name">
              <span v-if="state.saving" class="spin" />保存并接入
            </button>
          </div>
        </form>

        <table v-if="state.ecoMcpServers.length" class="model-table" style="margin-top:8px">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>端点</th>
              <th>状态</th>
              <th class="th-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in state.ecoMcpServers" :key="s.name" :style="s.disabled ? 'opacity:.45' : ''">
              <td class="mono">{{ s.name }}</td>
              <td>{{ s.transport === 'stdio' ? 'stdio' : 'http' }}</td>
              <td class="mono endpoint-cell">{{ s.transport === 'stdio' ? s.command : s.url }}</td>
              <td><span class="srv-status" :class="mcpStatusClass(s)">{{ mcpStatusText(s) }}</span></td>
              <td class="row-actions">
                <button type="button" class="btn-ghost" style="height:28px; padding:0 10px; font-size:12px" @click="toggleServer(s)">
                  {{ s.disabled ? '启用' : '停用' }}
                </button>
                <button type="button" class="icon-btn danger" aria-label="移除" title="移除" @click="removeServer(s)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="hint">停用 = 不装载该服务的工具，下一轮对话生效，无需重启后端。鉴权 token 只显示掩码，保存时自动沿用原值。</p>
      </div>
    </div>
  </section>

  <section class="card">
    <h2 class="card-title">技能（SKILL.md）</h2>
    <div class="list-head" style="margin-bottom:8px">
      <span class="label">已加载 {{ state.ecoSkills.length }} 个技能 · Agent 按任务自动调用（read_skill）</span>
      <button type="button" class="btn-ghost" style="height:32px" @click="openEcoFolder('skills')">打开 skills 目录</button>
    </div>
    <ul class="eco-list">
      <li v-for="sk in state.ecoSkills" :key="sk.name" class="eco-item">
        <span class="mono eco-name">{{ sk.name }}</span>
        <i class="badge">{{ sk.source === 'plugin' ? '插件' : '内置' }}</i>
        <span class="eco-desc">{{ sk.description || '（无描述）' }}</span>
      </li>
    </ul>
    <p class="hint">
      自己加技能：在 skills/&lt;技能名&gt;/SKILL.md 写 frontmatter（name + description 一句话）和正文攻略，
      重启后端生效；外部技能目录在 .env 的 SKILLS_DIRS 追加（分号分隔）。索引只占 system prompt 一行，正文按需加载。
    </p>
  </section>

  <section class="card">
    <h2 class="card-title">插件（plugins/）</h2>
    <div class="list-head" style="margin-bottom:8px">
      <span class="label">已安装 {{ state.ecoPlugins.length }} 个插件 · 工具与技能随插件自动生效</span>
      <button type="button" class="btn-ghost" style="height:32px" @click="openEcoFolder('plugins')">打开 plugins 目录</button>
    </div>
    <ul v-if="state.ecoPlugins.length" class="eco-list">
      <li v-for="p in state.ecoPlugins" :key="p.name" class="eco-item">
        <span class="mono eco-name">{{ p.name }}<template v-if="p.version"> v{{ p.version }}</template></span>
        <i class="badge" :class="{ 'badge-warn': !p.valid }">{{ p.valid ? '有效' : '无效' }}</i>
        <span class="eco-desc">{{ p.error || p.description || '（无描述）' }}</span>
      </li>
    </ul>
    <p v-else class="hint">plugins/ 目录为空。</p>
    <p class="hint">
      自己加插件：把含 plugin.json 的插件目录放进 plugins/（或命令行
      <code class="mono">crawagent add-plugin &lt;本地路径 | git URL&gt;</code> 一键接入）；
      从零写一个：<code class="mono">crawagent add-tool &lt;名称&gt;</code> 生成脚手架；
      查看：<code class="mono">crawagent plugins</code>。装完重启后端。
    </p>
  </section>
</template>
