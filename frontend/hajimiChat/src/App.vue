<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { marked } from 'marked'

const chatMessages = ref([
  {
    sender: 'ai',
    content: '✨ 嗨！我是你的AI助手，有什么我可以帮你的吗？无论是技术问题、创意灵感还是日常闲聊，我都乐意倾听～'
  }
])

const userInput = ref('')
const isWaitingForResponse = ref(false)
const textareaHeight = ref('48px')

const API_URL = '/api/chat'

function autoResizeTextarea() {
  const textarea = document.getElementById('userInput')
  if (textarea) {
    textarea.style.height = 'auto'
    const newHeight = Math.min(textarea.scrollHeight, 128)
    textarea.style.height = newHeight + 'px'
  }
}

function scrollToBottom() {
  const chatContainer = document.getElementById('chatMessages')
  if (chatContainer) {
    chatContainer.scrollTo({
      top: chatContainer.scrollHeight,
      behavior: 'smooth'
    })
  }
}

function escapeHtml(str) {
  if (!str) return ''
  return str.replace(/[&<>]/g, function(m) {
    if (m === '&') return '&amp;'
    if (m === '<') return '&lt;'
    if (m === '>') return '&gt;'
    return m
  }).replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, function(c) {
    return c
  })
}

async function sendMessageToAI(message) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ message: message }),
  })

  if (!response.ok) {
    throw new Error(`请求失败 (${response.status})`)
  }

  if (!response.body) {
    throw new Error('流式响应不可用')
  }

  return response.body
}

function formatTraceMessage(trace) {
  if (!trace || !trace.type) return ''

  if (trace.type === 'tool_call') {
    return `⏳ 正在调用工具：${trace.name || 'unknown'}`
  }

  if (trace.type === 'tool_result') {
    const status = trace.status === 'error' ? '❌' : '✅'
    return `${status} 工具完成：${trace.name || 'unknown'}`
  }

  return ''
}

function upsertToolStatus(aiMessage, evt, status) {
  if (!aiMessage.toolStates) aiMessage.toolStates = []
  const callId = evt.call_id || `${evt.name || 'unknown'}_${Date.now()}`
  const idx = aiMessage.toolStates.findIndex(t => t.callId === callId)
  if (idx >= 0) {
    aiMessage.toolStates[idx].status = status
    aiMessage.toolStates[idx].name = evt.name || aiMessage.toolStates[idx].name
    return
  }
  aiMessage.toolStates.push({
    callId,
    name: evt.name || 'unknown',
    status,
  })
}

function ensureStep(aiMessage, stepId) {
  if (!aiMessage.thinkingSteps) aiMessage.thinkingSteps = []
  let step = aiMessage.thinkingSteps.find(s => s.stepId === stepId)
  if (!step) {
    step = { stepId, content: '', done: false, kind: 'thinking', startedAt: Date.now() }
    aiMessage.thinkingSteps.push(step)
  }
  return step
}

function formatToolStepText(evt) {
  const name = evt.name || 'unknown'
  const args = evt.args && typeof evt.args === 'object' ? evt.args : {}
  const terminalTools = new Set(['run_command', 'run_powershell', 'run_shell_command'])
  if (terminalTools.has(name)) {
    const cmd = args.command ?? args.cmd ?? ''
    if (cmd !== '') {
      return `终端运行：${cmd}`
    }
    return '终端运行：（无命令参数）'
  }
  return `调用工具：${name}`
}

function upsertToolThinkingStep(aiMessage, evt, done = false) {
  const sid = `tool_${evt.call_id || evt.name || Date.now()}`
  const existed = !!(aiMessage.thinkingSteps && aiMessage.thinkingSteps.some(s => s.stepId === sid))
  const step = ensureStep(aiMessage, sid)
  // tool_end 事件通常不带 args，避免覆盖 tool_start 已写好的「终端运行：…」
  if (!existed || !done) {
    step.content = formatToolStepText(evt)
  }
  step.done = done
  step.kind = 'tool'
  return step
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function readStreamEvents(stream, onEvent) {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const text = line.trim()
      if (!text) continue
      if (!text.startsWith('data:')) continue
      try {
        const evt = JSON.parse(text.slice(5).trim())
        await onEvent(evt)
      } catch (e) {
        console.warn('无法解析流事件:', text, e)
      }
    }
  }

  if (buffer.trim()) {
    const tail = buffer.trim()
    if (!tail.startsWith('data:')) return
    try {
      await onEvent(JSON.parse(tail.slice(5).trim()))
    } catch (e) {
      console.warn('无法解析末尾流事件:', buffer, e)
    }
  }
}

async function handleSend() {
  if (isWaitingForResponse.value) return
  
  let rawMessage = userInput.value.trim()
  if (!rawMessage) return
  
  userInput.value = ''
  autoResizeTextarea()
  
  isWaitingForResponse.value = true
  
  chatMessages.value.push({
    sender: 'user',
    content: rawMessage
  })
  
  await nextTick()
  scrollToBottom()
  
  let aiMessageIndex = -1
  try {
    aiMessageIndex = chatMessages.value.push({
      sender: 'ai',
      content: '',
      isStreaming: true,
      toolStates: [],
      thinkingSteps: [],
    }) - 1

    const getAiMessage = () => chatMessages.value[aiMessageIndex]
    const stream = await sendMessageToAI(rawMessage)
    await readStreamEvents(stream, async (evt) => {
      if (!evt || !evt.type) return
      const aiMessage = getAiMessage()
      if (!aiMessage) return

      if (evt.type === 'tool_start') {
        upsertToolStatus(aiMessage, evt, 'running')
        upsertToolThinkingStep(aiMessage, evt, false)
        await nextTick()
        return
      }

      if (evt.type === 'tool_end') {
        const toolStepId = `tool_${evt.call_id || evt.name || ''}`
        const toolStep = aiMessage.thinkingSteps?.find(s => s.stepId === toolStepId)
        const elapsed = toolStep ? Date.now() - (toolStep.startedAt || Date.now()) : 0
        const minPendingMs = 450
        if (elapsed < minPendingMs) {
          await sleep(minPendingMs - elapsed)
        }
        upsertToolStatus(aiMessage, evt, 'done')
        upsertToolThinkingStep(aiMessage, evt, true)
        await nextTick()
        return
      }

      if (evt.type === 'answer_chunk') {
        aiMessage.content += evt.content || ''
        await nextTick()
        return
      }

      if (evt.type === 'step_start') {
        ensureStep(aiMessage, evt.step_id || `step_${Date.now()}`)
        await nextTick()
        return
      }

      if (evt.type === 'step_chunk') {
        const step = ensureStep(aiMessage, evt.step_id || `step_${Date.now()}`)
        step.content += evt.content || ''
        await nextTick()
        return
      }

      if (evt.type === 'step_done') {
        const step = ensureStep(aiMessage, evt.step_id || `step_${Date.now()}`)
        step.done = true
        await nextTick()
        return
      }

      if (evt.type === 'error') {
        aiMessage.content += `\n⚠️ 后端执行出错：${evt.message || '未知错误'}`
        aiMessage.isStreaming = false
        await nextTick()
        return
      }

      if (evt.type === 'done') {
        aiMessage.isStreaming = false
        await nextTick()
      }
    })

    const aiMessage = getAiMessage()
    if (aiMessage && !aiMessage.content.trim()) {
      aiMessage.content = '（空响应）'
    }
    if (aiMessage) {
      aiMessage.isStreaming = false
    }
  } catch (error) {
    const errorMsg = error.message || '服务暂时不可用，请稍后再试。'
    const aiMessage = aiMessageIndex >= 0 ? chatMessages.value[aiMessageIndex] : null
    if (aiMessage) {
      aiMessage.content = `⚠️ 抱歉，发生错误: ${errorMsg}`
      aiMessage.isStreaming = false
    } else {
      chatMessages.value.push({
        sender: 'ai',
        content: `⚠️ 抱歉，发生错误: ${errorMsg}`
      })
    }
    console.error('发送失败:', error)
  } finally {
    isWaitingForResponse.value = false
    await nextTick()
    scrollToBottom()
    document.getElementById('userInput')?.focus()
  }
}

function handleKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (!isWaitingForResponse.value && userInput.value.trim()) {
      handleSend()
    }
  }
}

watch(chatMessages, () => {
  nextTick(() => {
    scrollToBottom()
  })
}, { deep: true })

async function checkBackendHealth() {
  try {
    const testResponse = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: 'ping' }),
    })
    if (!testResponse.ok) {
      console.warn('后端服务似乎未就绪，请确保 Flask 服务运行在 5000 端口')
    } else {
      console.log('后端连接正常')
    }
  } catch (err) {
    console.warn('无法连接到后端服务，请确认后端已启动并可通过 Vite 代理访问 /api/chat')
  }
}

onMounted(() => {
  document.getElementById('userInput')?.focus()
  autoResizeTextarea()
  setTimeout(checkBackendHealth, 500)
})
</script>

<template>
  <div class="bg-gradient-to-br from-slate-50 via-blue-50/20 to-indigo-50/30 min-h-screen font-sans antialiased">
    <div class="max-w-5xl mx-auto px-4 py-5 md:py-8 h-screen flex flex-col">
      <!-- 头部区域 -->
      <div class="backdrop-blur-sm bg-white/70 rounded-2xl shadow-sm border border-white/50 px-6 py-4 mb-5 flex justify-between items-center flex-wrap gap-3">
        <div class="flex items-center gap-3">
          <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-blue-500 to-indigo-600 shadow-md flex items-center justify-center overflow-hidden">
            <img src="/hajimi.jpg" alt="AI助手" class="w-full h-full object-cover">
          </div>
          <div>
            <h1 class="text-xl font-semibold text-slate-800 tracking-tight">哈基米RAG</h1>
            <p class="text-xs text-slate-500 flex items-center gap-1">
              <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-sm"></span> 
              在线 · 随时为您解答
            </p>
          </div>
        </div>
        <div class="flex gap-2 text-sm text-slate-500 bg-slate-100/80 px-3 py-1.5 rounded-full">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>您的本地AI助手</span>
        </div>
      </div>

      <!-- 聊天消息容器 -->
      <div id="chatMessages" class="flex-1 overflow-y-auto custom-scroll space-y-5 pb-4 pr-1 scroll-smooth">
        <div 
          v-for="(message, index) in chatMessages" 
          :key="index"
          :class="[
            'flex items-start gap-2.5 fade-in-up',
            message.sender === 'user' ? 'justify-end' : ''
          ]"
        >
          <div v-if="message.sender === 'ai'" class="w-8 h-8 rounded-full bg-gradient-to-r from-indigo-500 to-blue-500 flex-shrink-0 flex items-center justify-center shadow-sm overflow-hidden">
            <img src="/hajimi.jpg" alt="AI助手" class="w-full h-full object-cover">
          </div>

          <div
            :class="[
              'flex flex-col gap-1',
              message.sender === 'user'
                ? 'min-w-0 max-w-[85%] sm:max-w-[75%] items-end self-end'
                : 'min-w-0 max-w-full items-start'
            ]"
          >
            <div
              v-if="message.sender === 'ai' && ((message.thinkingSteps && message.thinkingSteps.length) || message.isStreaming)"
              class="flex flex-col gap-1 items-start text-[12px] w-full"
            >
              <div
                v-if="(!message.thinkingSteps || !message.thinkingSteps.length) && message.isStreaming"
                class="thinking-step-pill px-2 py-1 rounded bg-slate-50 text-slate-600 border border-slate-200"
              >
                <span class="mr-1">🧠</span>思考中...
              </div>
              <div
                v-for="step in message.thinkingSteps"
                :key="step.stepId"
                :class="[
                  'thinking-step-pill px-2 py-1 rounded border',
                  step.kind === 'tool'
                    ? (step.done
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : 'bg-amber-50 text-amber-700 border-amber-200')
                    : 'bg-slate-50 text-slate-600 border-slate-200'
                ]"
              >
                <span class="mr-1">{{ step.kind === 'tool' ? (step.done ? '✅' : '⏳') : '🧠' }}</span>{{ step.content || '思考中...' }}
              </div>
            </div>

            <div 
              :class="[
                'message-bubble px-4 py-2.5',
                message.sender === 'user' 
                  ? 'message-bubble-user bg-gradient-to-br from-blue-500 to-blue-600 text-white rounded-2xl rounded-tr-md shadow-sm'
                  : 'message-bubble-ai bg-white border border-slate-100 shadow-sm rounded-2xl rounded-tl-md'
              ]"
              v-html="(message.sender === 'ai' || message.sender === 'tool') ? marked.parse(message.content) : escapeHtml(message.content)"
            ></div>
          </div>
          
          <div v-if="message.sender === 'user'" class="w-8 h-8 rounded-full bg-slate-200 flex-shrink-0 flex items-center justify-center shadow-inner">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="#475569" class="w-4 h-4">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
            </svg>
          </div>
        </div>

      </div>

      <!-- 底部输入区 -->
      <div class="mt-5 bg-white/80 backdrop-blur-md rounded-2xl shadow-lg border border-slate-200/80 p-2 transition-all focus-within:ring-2 focus-within:ring-blue-300/50">
        <div class="flex items-end gap-2">
          <div class="flex-1">
            <textarea 
              id="userInput"
              v-model="userInput"
              rows="1"
              placeholder="输入消息..."
              class="w-full resize-none bg-transparent border-0 focus:ring-0 focus:outline-none text-slate-700 placeholder:text-slate-400 py-3 px-3 max-h-32 min-h-[48px] text-sm leading-relaxed"
              style="overflow-y: auto;"
              @input="autoResizeTextarea"
              @keydown="handleKeyDown"
            ></textarea>
          </div>
          <button 
            id="sendBtn"
            @click="handleSend"
            :disabled="isWaitingForResponse || !userInput.trim()"
            class="h-10 w-10 rounded-xl bg-gradient-to-r from-blue-500 to-indigo-600 text-white flex items-center justify-center shadow-md transition-all hover:scale-105 active:scale-95 disabled:opacity-50 disabled:hover:scale-100 disabled:cursor-not-allowed"
          >
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5 -translate-x-px">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
            </svg>
          </button>
        </div>
        <div class="flex justify-between items-center px-2 pb-1 text-[10px] text-slate-400">
          <span>Enter 发送 · Shift+Enter 换行</span>
          <span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span> AI 已连接</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style>
/* 自定义滚动条 */
.custom-scroll::-webkit-scrollbar {
  width: 5px;
}
.custom-scroll::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 10px;
}
.custom-scroll::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 10px;
}
.custom-scroll::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* 平滑出现动画 */
.fade-in-up {
  animation: fadeUp 0.25s ease-out forwards;
}
@keyframes fadeUp {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 打字闪烁光标 */
.typing-cursor::after {
  content: '▋';
  display: inline-block;
  animation: blink 1s step-end infinite;
  margin-left: 2px;
  color: #3b82f6;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* 背景微光渐变 */
.bg-radial-glow {
  background: radial-gradient(circle at 10% 20%, rgba(59,130,246,0.03) 0%, rgba(0,0,0,0) 70%);
}

/* 思考 / 工具条：宽度随内容，最长不超过会话列 */
.thinking-step-pill {
  box-sizing: border-box;
  width: fit-content;
  max-width: 100%;
  word-wrap: break-word;
  overflow-wrap: break-word;
}

/* AI 气泡：占满可用列宽的一部分 */
.message-bubble-ai {
  max-width: 85%;
  word-wrap: break-word;
  overflow-wrap: break-word;
}
@media (min-width: 640px) {
  .message-bubble-ai {
    max-width: 75%;
  }
}

/* 用户气泡：按文字自然宽度扩展，避免被 85% 父宽 + break-word 拆成一字一行 */
.message-bubble-user {
  box-sizing: border-box;
  width: fit-content;
  max-width: 100%;
  white-space: pre-wrap;
  word-break: keep-all;
  overflow-wrap: anywhere;
  line-break: loose;
}

/* 简单的loading点动画 */
.dot-pulse {
  display: flex;
  align-items: center;
  gap: 4px;
}
.dot-pulse span {
  width: 6px;
  height: 6px;
  background-color: #94a3b8;
  border-radius: 50%;
  display: inline-block;
  animation: pulse 1.4s infinite ease-in-out both;
}
.dot-pulse span:nth-child(1) { animation-delay: -0.32s; }
.dot-pulse span:nth-child(2) { animation-delay: -0.16s; }
@keyframes pulse {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
  40% { transform: scale(1); opacity: 1; }
}

/* Markdown渲染样式 */
.message-bubble h1, .message-bubble h2, .message-bubble h3, .message-bubble h4, .message-bubble h5, .message-bubble h6 {
  margin: 12px 0 8px 0;
  font-weight: 600;
  line-height: 1.3;
}
.message-bubble h1 { font-size: 1.5rem; color: #1e293b; }
.message-bubble h2 { font-size: 1.25rem; color: #1e293b; }
.message-bubble h3 { font-size: 1.1rem; color: #334155; }
.message-bubble h4, .message-bubble h5, .message-bubble h6 { font-size: 1rem; color: #475569; }

.message-bubble p {
  margin: 8px 0;
  line-height: 1.6;
}

.message-bubble ul, .message-bubble ol {
  margin: 12px 0;
  padding-left: 24px;
}

.message-bubble li {
  margin: 4px 0;
  line-height: 1.5;
}

.message-bubble a {
  color: #3b82f6;
  text-decoration: none;
  font-weight: 500;
}

.message-bubble a:hover {
  text-decoration: underline;
}

.message-bubble code {
  background-color: #f1f5f9;
  padding: 2px 4px;
  border-radius: 4px;
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.9em;
  color: #dc2626;
}

.message-bubble pre {
  background-color: #0f172a;
  color: #f8fafc;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 12px 0;
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.85em;
  line-height: 1.4;
}

.message-bubble blockquote {
  border-left: 3px solid #3b82f6;
  padding-left: 16px;
  margin: 12px 0;
  color: #64748b;
  font-style: italic;
}

.message-bubble hr {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 16px 0;
}
</style>
