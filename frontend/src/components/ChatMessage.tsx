import { useState } from 'react';
import { motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import {
  User,
  Bot,
  ThumbsUp,
  ThumbsDown,
  Wrench,
  BookOpen,
  ChevronDown,
  ChevronUp,
  Brain,
  Bug,
  Copy,
  Check,
} from 'lucide-react';
import type { Message } from '../types';

interface ChatMessageProps {
  message: Message;
  index: number;
  debug: boolean;
  onFeedback?: (index: number, score: 'good' | 'bad') => void;
}

const PATH_COLORS: Record<string, string> = {
  general: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  coding: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  complex: 'bg-violet-500/20 text-violet-400 border-violet-500/30',
  background: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
};

function Badge({ text, className }: { text: string; className?: string }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-[0.7rem] font-semibold rounded-md border ${className ?? 'bg-white/5 text-[var(--color-text-secondary)] border-white/10'}`}
    >
      {text}
    </span>
  );
}

function Collapsible({
  label,
  icon,
  children,
}: {
  label: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 rounded-lg border border-[var(--color-border)] bg-[rgba(10,10,20,0.4)]">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-semibold text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        {icon}
        {label}
        <span className="ml-auto">
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>
      {open && <div className="px-3 pb-3 text-xs text-[var(--color-text-secondary)]">{children}</div>}
    </div>
  );
}

export default function ChatMessage({ message, index, debug, onFeedback }: ChatMessageProps) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''} mb-4`}
    >
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
          isUser
            ? 'bg-gradient-to-br from-violet-600/30 to-indigo-600/30 border border-violet-500/20'
            : 'bg-gradient-to-br from-[#6c63ff]/30 to-purple-600/30 border border-[#6c63ff]/20'
        }`}
      >
        {isUser ? <User size={16} className="text-violet-300" /> : <Bot size={16} className="text-[#a5a0ff]" />}
      </div>

      {/* Bubble */}
      <div
        className={`relative max-w-[75%] rounded-2xl px-4 py-3 glass glass-hover transition-all duration-200 ${
          isUser
            ? 'bg-gradient-to-br from-[rgba(30,27,46,0.7)] to-[rgba(25,23,38,0.5)]'
            : 'bg-gradient-to-br from-[rgba(20,22,40,0.7)] to-[rgba(15,18,35,0.5)]'
        }`}
      >
        {/* Content */}
        <div className="markdown-body text-sm leading-relaxed">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>

        {/* Assistant metadata */}
        {!isUser && message.path && (
          <div className="mt-3 space-y-2">
            {/* Badges row */}
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge
                text={message.path}
                className={PATH_COLORS[message.path] ?? PATH_COLORS.general}
              />
              {message.model && <Badge text={message.model} />}
              {message.deep_thinking_used && (
                <Badge
                  text={`${message.reasoning_strategy ?? 'deep'} · ${message.reasoning_steps ?? 0} steps`}
                  className="bg-purple-500/15 text-purple-400 border-purple-500/25"
                />
              )}
            </div>

            {/* Tools used */}
            {message.tools_used && message.tools_used.length > 0 && (
              <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                <Wrench size={12} />
                Tools: {message.tools_used.join(', ')}
              </p>
            )}

            {/* Sources */}
            {message.sources && message.sources.length > 0 && (
              <Collapsible label={`Sources (${message.sources.length})`} icon={<BookOpen size={13} />}>
                {message.sources.map((s, i) => (
                  <div key={i} className="mb-2 last:mb-0">
                    <p className="font-medium text-[var(--color-text-primary)]">
                      {i + 1}. {s.source}
                      {s.chunk_id && <span className="text-[var(--color-text-muted)]"> — {s.chunk_id}</span>}
                    </p>
                    {s.doc && (
                      <p className="mt-0.5 text-[var(--color-text-muted)] line-clamp-3">{s.doc}</p>
                    )}
                  </div>
                ))}
              </Collapsible>
            )}

            {/* Reasoning chain */}
            {message.reasoning_chain && message.reasoning_chain.length > 0 && (
              <Collapsible label="Reasoning chain" icon={<Brain size={13} />}>
                {message.reasoning_chain.map((step) => (
                  <div key={step.step_number} className="mb-3 last:mb-0">
                    <p className="font-medium text-[var(--color-text-primary)]">
                      Step {step.step_number}: {step.sub_problem}
                    </p>
                    {step.analysis && <p className="mt-0.5">Analysis: {step.analysis}</p>}
                    {step.conclusion && <p className="mt-0.5">Conclusion: {step.conclusion}</p>}
                  </div>
                ))}
              </Collapsible>
            )}

            {/* Debug */}
            {debug && (
              <Collapsible label="Debug info" icon={<Bug size={13} />}>
                <p>Path: <code className="text-xs">{message.path}</code></p>
                <p>Model: <code className="text-xs">{message.model ?? '-'}</code></p>
                <p>Tools: {message.tools_used?.join(', ') || '-'}</p>
                {message.total_tokens != null && message.total_tokens > 0 && (
                  <p>Tokens: {message.total_tokens} (reasoning: {message.tokens_used_reasoning ?? 0}, answer: {message.tokens_used_answer ?? 0})</p>
                )}
                {message.total_latency_ms != null && message.total_latency_ms > 0 && (
                  <p>Latency: {message.total_latency_ms}ms</p>
                )}
                <p className="mt-1 font-medium">Retrieved context:</p>
                <pre className="mt-1 rounded-md bg-black/30 p-2 text-[0.7rem] whitespace-pre-wrap break-all">
                  {message.retrieved_context || '(none)'}
                </pre>
              </Collapsible>
            )}

            {/* Actions row: copy + feedback */}
            <div className="flex items-center gap-1 pt-1">
              <button
                onClick={handleCopy}
                className="p-1.5 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-white/5 transition-colors"
                title="Copy answer"
              >
                {copied ? <Check size={14} className="text-[var(--color-success)]" /> : <Copy size={14} />}
              </button>

              {onFeedback && !message._feedback_sent && (
                <>
                  <button
                    onClick={() => onFeedback(index, 'good')}
                    className="p-1.5 rounded-md text-[var(--color-text-muted)] hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                    title="Good answer"
                  >
                    <ThumbsUp size={14} />
                  </button>
                  <button
                    onClick={() => onFeedback(index, 'bad')}
                    className="p-1.5 rounded-md text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    title="Bad answer"
                  >
                    <ThumbsDown size={14} />
                  </button>
                </>
              )}
              {message._feedback_sent && (
                <span className="flex items-center gap-1 text-xs text-emerald-400">
                  <Check size={12} /> Thanks for the feedback
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </motion.div>
  );
}
