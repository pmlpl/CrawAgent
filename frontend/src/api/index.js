import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 300000, // 5分钟，Agent 任务可能很久
})

// 响应拦截器
api.interceptors.response.use(
  response => response.data,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// ===== Harness API（新架构核心） =====
export const harnessApi = {
  // 创建会话
  createSession(name = '') {
    return api.post('/harness/sessions', null, { params: { name } })
  },
  // 获取会话列表
  listSessions(limit = 20) {
    return api.get('/harness/sessions', { params: { limit } })
  },
  // 发送消息（Agent Loop）
  prompt(sessionId, message, lane = 'main', options = {}) {
    const payload = { session_id: sessionId, message, lane }
    // 透传 max_pages/max_depth（用户指定时覆盖 LLM 默认规划）
    if (options.max_pages != null) payload.max_pages = options.max_pages
    if (options.max_depth != null) payload.max_depth = options.max_depth
    return api.post('/harness/prompt', payload)
  },
  // 快速爬取
  quickCrawl(url, instruction = '') {
    return api.post('/harness/quick-crawl', { url, instruction })
  },
  // 获取会话对话历史
  getHistory(sessionId, limit = 50) {
    return api.get(`/harness/sessions/${sessionId}/entries`, { params: { limit } })
  },
  // 获取会话累计 token 用量（含缓存命中率与费用）
  getUsage(sessionId) {
    return api.get(`/harness/sessions/${sessionId}/usage`)
  },
  // 获取 lanes
  getLanes(sessionId) {
    return api.get(`/harness/sessions/${sessionId}/lanes`)
  },
  // 停止会话
  stop(sessionId) {
    return api.post(`/harness/sessions/${sessionId}/stop`)
  },
  // 删除会话
  deleteSession(sessionId) {
    return api.delete(`/harness/sessions/${sessionId}`)
  },
}

export const crawlApi = {
  direct(data) {
    return api.post('/crawl/direct', data)
  },
  getResults(params) {
    return api.get('/crawl/results', { params })
  },
}

export const systemApi = {
  health() {
    return api.get('/health')
  },
  stats() {
    return api.get('/stats')
  },
  getSettings() {
    return api.get('/settings')
  },
  updateSettings(data) {
    return api.put('/settings', data)
  },
}

export default api
