import { useState, useRef, useEffect } from 'react';
import { Send, Paperclip, Loader2 } from 'lucide-react';
import { motion } from 'framer-motion';

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  loading?: boolean;
  placeholder?: string;
}

export default function ChatInput({ onSend, disabled, loading, placeholder }: ChatInputProps) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = 'auto';
      ref.current.style.height = Math.min(ref.current.scrollHeight, 160) + 'px';
    }
  }, [text]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || loading) return;
    onSend(trimmed);
    setText('');
    if (ref.current) ref.current.style.height = 'auto';
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      className="px-4 pb-4 pt-2"
    >
      <div className="relative max-w-[800px] mx-auto">
        <div className="flex items-end gap-2 glass rounded-2xl p-2 transition-all duration-200 focus-within:border-[var(--color-border-hover)] focus-within:shadow-[0_0_16px_rgba(108,99,255,0.15)]">
          <textarea
            ref={ref}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder || 'Ask Jarvis something...'}
            disabled={disabled || loading}
            rows={1}
            className="flex-1 bg-transparent text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] resize-none outline-none px-3 py-2 text-[0.92rem] leading-relaxed max-h-[160px]"
          />
          <button
            onClick={submit}
            disabled={!text.trim() || disabled || loading}
            className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-[#6c63ff] to-[#4f46e5] text-white transition-all duration-200 hover:shadow-[0_4px_20px_rgba(108,99,255,0.4)] hover:-translate-y-0.5 disabled:opacity-30 disabled:hover:shadow-none disabled:hover:translate-y-0 shrink-0"
          >
            {loading ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Send size={18} />
            )}
          </button>
        </div>
        <p className="text-center text-[0.7rem] text-[var(--color-text-muted)] mt-2">
          Jarvis routes your message to the best model automatically
        </p>
      </div>
    </motion.div>
  );
}
