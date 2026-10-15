import { useState, useEffect, useRef, useCallback } from 'react';
import { AnimatePresence } from 'framer-motion';
import { v4 as uuidv4 } from 'uuid';
import type { Message, Toggles, HealthData, ModelsConfig } from './types';
import { api } from './api';
import Sidebar from './components/Sidebar';
import HeroSection from './components/HeroSection';
import ChatMessage from './components/ChatMessage';
import ChatInput from './components/ChatInput';
import ToggleToolbar from './components/ToggleToolbar';
import TypingIndicator from './components/TypingIndicator';
import ApprovalCard from './components/ApprovalCard';

const SESSION_ID = localStorage.getItem('jarvis_session_id') || (() => {
  const id = uuidv4();
  localStorage.setItem('jarvis_session_id', id);
  return id;
})();

function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [toggles, setToggles] = useState<Toggles>({
    show_reasoning: false,
    answer_style: 'default',
    background_task: false,
    debug: false,
    deep_thinking: false,
    show_reasoning_chain: false,
    reasoning_strategy: 'auto',
  });
  const [approval, setApproval] = useState<{
    action: string;
    toolCalls: { name: string; args?: Record<string, unknown> }[];
    approvalId?: string;
    expiresAt?: string;
  } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollToBottom = () => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });

  useEffect(() => { scrollToBottom(); }, [messages, loading]);

  const refresh = useCallback(async () => {
    const [h, m, t] = await Promise.all([
      api.health(),
      api.models(),
      api.sessionToken(SESSION_ID),
    ]);
    setHealth(h);
    setModels(m);
    setToken(t);
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 30000);
    return () => clearInterval(iv);
  }, [refresh]);

  const sendMessage = async (text: string, opts?: { approved?: boolean; selectedText?: string }) => {
    if (loading) return;

    const userMsg: Message = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const payload: Record<string, unknown> = {
        session_id: SESSION_ID,
        session_token: token,
        message: text,
        history: messages.map((m) => ({ role: m.role, content: m.content })),
        approved: opts?.approved || false,
        show_reasoning: toggles.show_reasoning,
        answer_style: toggles.answer_style !== 'default' ? toggles.answer_style : null,
        deep_thinking: toggles.deep_thinking,
        show_reasoning_chain: toggles.show_reasoning_chain,
        reasoning_strategy: toggles.reasoning_strategy !== 'auto' ? toggles.reasoning_strategy : null,
        selected_text: opts?.selectedText || null,
      };

      const data = await api.chat(payload);
      const assistantMsg: Message = {
        role: 'assistant',
        content: data.response,
        path: (data.path_used || '').toLowerCase() || 'unknown',
        model: data.model_used,
        tools_used: data.tools_used || [],
        sources: data.sources || [],
        retrieved_context: data.retrieved_context,
        approval_required: !!data.approval_required,
        deep_thinking_used: !!data.deep_thinking_used,
        reasoning_strategy: data.reasoning_strategy,
        reasoning_steps: data.reasoning_steps || 0,
        reasoning_chain: data.reasoning_chain,
        tokens_used_reasoning: data.tokens_used_reasoning || 0,
        tokens_used_answer: data.tokens_used_answer || 0,
        total_tokens: data.total_tokens || 0,
        latency_ms_reasoning: data.latency_ms_reasoning || 0,
        latency_ms_answer: data.latency_ms_answer || 0,
        total_latency_ms: data.total_latency_ms || 0,
      };

      setMessages((prev) => [...prev, assistantMsg]);

      if (data.approval_required) {
        setApproval({
          action: data.pending_action || 'perform an action',
          toolCalls: data.pending_tool_calls || [],
          approvalId: data.approval_id,
          expiresAt: data.approval_expires_at,
        });
      } else {
        setApproval(null);
      }
    } catch (err: unknown) {
      const e = err as { status?: number; body?: { error?: string; message?: string; suggested_action?: string } };
      let errorMsg = 'Failed to reach the backend. Check that the server is running.';
      if (e?.body) {
        const { error: errType, message: msg, suggested_action } = e.body;
        errorMsg = `**${errType || 'Error'}** — ${msg || 'Unknown error'}`;
        if (suggested_action) errorMsg += `\n\n> **Suggested:** ${suggested_action}`;
      }
      setMessages((prev) => [...prev, { role: 'assistant', content: errorMsg }]);
    } finally {
      setLoading(false);
    }
  };

  const handleFeedback = async (index: number, score: 'good' | 'bad') => {
    const msg = messages[index];
    const prevQ = index > 0 && messages[index - 1]?.role === 'user' ? messages[index - 1].content : '';
    const ok = await api.feedback({
      session_id: SESSION_ID,
      session_token: token,
      question: prevQ,
      answer: msg.content,
      score,
      model_used: msg.model,
    });
    if (ok) {
      setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, _feedback_sent: true } : m)));
    }
  };

  const handleApprove = async () => {
    setApproval(null);
    await sendMessage('', { approved: true });
  };

  const handleDeny = () => {
    setApproval(null);
    setMessages((prev) => [...prev, { role: 'assistant', content: 'Action cancelled by user.' }]);
  };

  const exportConversation = () => {
    const lines = ['# Jarvis Conversation Export\n'];
    for (const m of messages) {
      const heading = m.role === 'user' ? 'User' : 'Assistant';
      lines.push(`## ${heading}\n`, `${m.content}\n`);
      if (m.role === 'assistant' && (m.path || m.model)) {
        const parts = [];
        if (m.path) parts.push(`path: \`${m.path}\``);
        if (m.model) parts.push(`model: \`${m.model}\``);
        if (m.tools_used?.length) parts.push(`tools: ${m.tools_used.join(', ')}`);
        lines.push(`> ${parts.join(' | ')}\n`);
      }
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'jarvis_conversation.md';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <AnimatePresence>
        {sidebarOpen && (
          <Sidebar
            health={health}
            models={models}
            onClearChat={() => { setMessages([]); setApproval(null); }}
            onExport={exportConversation}
            hasMessages={messages.length > 0}
            onRefresh={refresh}
          />
        )}
      </AnimatePresence>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0 h-full">
        {/* Header */}
        <header className="flex items-center gap-3 px-5 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]/50 backdrop-blur-sm shrink-0">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-2 rounded-lg border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-secondary)] transition-all"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <div>
            <h1 className="text-lg font-bold gradient-text">Chat with Jarvis</h1>
            <p className="text-xs text-[var(--color-text-muted)]">General, coding, and complex tasks — routed to the right model</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {health?.status === 'ok' && (
              <span className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-success)] shadow-[0_0_6px_var(--color-success)] animate-[pulse-dot_2s_ease-in-out_infinite]" />
                Online
              </span>
            )}
          </div>
        </header>

        {/* Toggles */}
        <ToggleToolbar toggles={toggles} onChange={setToggles} />

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <HeroSection onSuggestionClick={(text) => sendMessage(text)} />
          ) : (
            <div className="py-4">
              {messages.map((msg, i) => (
                <ChatMessage
                  key={i}
                  message={msg}
                  index={i}
                  debug={toggles.debug}
                  onFeedback={handleFeedback}
                />
              ))}
            </div>
          )}

          <AnimatePresence>
            {loading && <TypingIndicator />}
          </AnimatePresence>

          {approval && (
            <ApprovalCard
              action={approval.action}
              toolCalls={approval.toolCalls}
              approvalId={approval.approvalId}
              expiresAt={approval.expiresAt}
              onApprove={handleApprove}
              onDeny={handleDeny}
            />
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <ChatInput
          onSend={(text) => sendMessage(text)}
          loading={loading}
          disabled={!!approval}
        />
      </div>
    </div>
  );
}

export default App;
