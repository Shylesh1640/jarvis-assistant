import { Shield, Check, X, Clock } from 'lucide-react';
import { motion } from 'framer-motion';
import type { ToolCall } from '../types';

interface ApprovalCardProps {
  action: string;
  toolCalls: ToolCall[];
  approvalId?: string;
  expiresAt?: string;
  onApprove: () => void;
  onDeny: () => void;
}

export default function ApprovalCard({
  action,
  toolCalls,
  approvalId,
  expiresAt,
  onApprove,
  onDeny,
}: ApprovalCardProps) {
  const remaining = expiresAt ? Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000)) : null;
  const expired = remaining === 0;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="max-w-[800px] mx-auto my-3 px-4"
    >
      <div className="glass rounded-xl p-5 animate-glow">
        <div className="flex items-center gap-2 mb-3">
          <Shield size={18} className="text-[var(--color-warning)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Approval Required</h3>
        </div>

        <p className="text-sm text-[var(--color-text-secondary)] mb-3">
          Jarvis wants to: <code className="text-xs bg-[var(--color-accent-dim)] px-2 py-0.5 rounded">{action}</code>
        </p>

        {toolCalls.length > 0 && (
          <div className="mb-3 space-y-1.5">
            <p className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">Pending tool calls</p>
            {toolCalls.map((tc, i) => {
              const argStr = tc.args
                ? Object.entries(tc.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')
                : '';
              return (
                <pre key={i} className="text-xs bg-[rgba(8,8,18,0.6)] border border-[var(--color-border)] rounded-lg px-3 py-2 overflow-x-auto text-[var(--color-text-secondary)]">
                  {tc.name}({argStr})
                </pre>
              );
            })}
          </div>
        )}

        {approvalId && (
          <p className="text-[0.68rem] text-[var(--color-text-muted)] mb-3">
            ID: {approvalId.slice(0, 8)}…
          </p>
        )}

        {expired ? (
          <div className="flex items-center gap-2 text-[var(--color-error)] text-sm">
            <Clock size={14} />
            <span>Expired — re-ask Jarvis to try again.</span>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            {remaining !== null && (
              <span className="text-xs text-[var(--color-text-muted)] flex items-center gap-1">
                <Clock size={12} /> {remaining}s
              </span>
            )}
            <button
              onClick={onApprove}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-gradient-to-br from-[#6c63ff] to-[#4f46e5] text-white text-sm font-medium transition-all hover:shadow-[0_4px_20px_rgba(108,99,255,0.4)] hover:-translate-y-0.5"
            >
              <Check size={14} /> Approve
            </button>
            <button
              onClick={onDeny}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm font-medium transition-all hover:border-[var(--color-error)] hover:text-[var(--color-error)]"
            >
              <X size={14} /> Deny
            </button>
          </div>
        )}
      </div>
    </motion.div>
  );
}
