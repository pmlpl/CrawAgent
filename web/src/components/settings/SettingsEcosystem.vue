<script setup>
// 「生态扩展」页签：MCP 服务（开关/增删/状态）+ 技能 + 插件（P2-9 生态面板归位）
// 自动启动由 Agent 对话链路内置拉起（anything-analyzer），开关点击即保存，无历史脚手架
import { useSettings } from '../../composables/useSettings'

const { state, toggleAutostart, toggleServer, removeServer, addMcpServer, openEcoFolder } = useSettings()

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
          class="btn-ghost btn-sm"
          :disabled="!state.mcpConfigured || state.saving"
          :style="state.mcpAutostart ? 'color: var(--accent); border-color: var(--accent)' : ''"
          @click="toggleAutostart"
        >
          <span v-if="state.saving" class="spin" />{{ state.mcpAutostart ? 'ON' : 'OFF' }}
        </button>
      </div>
      <p class="hint" v-if="!state.mcpConfigured">
        还没有配置任何 MCP 服务：先在下方「添加服务」，这个开关才会生效
      </p>
      <p class="hint" v-else>
        开启后：对话时检测到 MCP 服务未运行会自动拉起（内置 anything-analyzer 支持），无需手动开终端
      </p>
      <div v-if="state.saveTip" class="result" :class="state.saveTipOk === false ? 'err' : 'ok'" style="margin-top: 8px">{{ state.saveTip }}</div>

      <!-- ===== MCP 服务列表：开关 / 增删 / 状态 ===== -->
      <div class="field" style="margin-top:16px">
        <div class="list-head">
          <span class="label">已配置的 MCP 服务（{{ state.ecoMcpServers.length }}）</span>
          <button type="button" class="btn-ghost btn-sm" @click="state.showMcpForm = !state.showMcpForm">
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
            <button type="submit" class="btn-primary btn-sm" :disabled="state.saving || !state.mcpForm.name">
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
                <button type="button" class="btn-ghost btn-sm" @click="toggleServer(s)">
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
      <button type="button" class="btn-ghost btn-sm" @click="openEcoFolder('skills')">打开 skills 目录</button>
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
      <button type="button" class="btn-ghost btn-sm" @click="openEcoFolder('plugins')">打开 plugins 目录</button>
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
