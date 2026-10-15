import { useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Bot,
  Cpu,
  Database,
  Upload,
  RefreshCw,
  Download,
  Trash2,
  ChevronDown,
  FileText,
  Zap,
  Brain,
  Lightbulb,
} from 'lucide-react';
import { api } from '../api';
import type { HealthData, ModelsConfig, RuntimeSnap, DocEntry } from '../types';

interface SidebarProps {
  health: HealthData | null;
  models: ModelsConfig | null;
  onClearChat: () => void;
  onExport: () => void;
  hasMessages: boolean;
  onRefresh: () => void;
}

/* ------------------------------------------------------------------ */
/* Collapsible section                                                 */
/* ------------------------------------------------------------------ */

function Section({
  title,
  icon,
  children,
  defaultOpen = false,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="mb-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-2 text-[0.7rem] font-bold uppercase tracking-widest text-text-secondary hover:text-text-primary transition-colors"
      >
        <span className="opacity-60">{icon}</span>
        <span className="flex-1 text-left">{title}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
          <ChevronDown size={13} />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 text-xs leading-relaxed">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Status dot                                                          */
/* ------------------------------------------------------------------ */

function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      className="inline-block w-[7px] h-[7px] rounded-full mr-1.5 relative -top-px"
      style={{
        background: online ? '#22c55e' : '#ef4444',
        boxShadow: online ? '0 0 6px #22c55e88' : '0 0 6px #ef444488',
        animation: online ? 'pulse-dot 2s ease-in-out infinite' : 'none',
      }}
    />
  );
}

/* ------------------------------------------------------------------ */
/* Sidebar                                                             */
/* ------------------------------------------------------------------ */

export default function Sidebar({
  health,
  models,
  onClearChat,
  onExport,
  hasMessages,
  onRefresh,
}: SidebarProps) {
  const [runtime, setRuntime] = useState<RuntimeSnap | null>(null);
  const [docs, setDocs] = useState<DocEntry[]>([]);
  const [docCount, setDocCount] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const backendOnline = health?.status === 'ok';
  const ollamaOnline = !!health?.ollama_reachable;

  const refreshRuntime = async () => {
    const snap = await api.runtime();
    setRuntime(snap);
  };

  const refreshDocs = async () => {
    const [d, c] = await Promise.all([api.docs(), api.docCount()]);
    setDocs(d);
    setDocCount(c);
  };

  const handleUpload = async () => {
    const files = fileRef.current?.files;
    if (!files?.length) return;
    setUploading(true);
    await api.uploadDocs(Array.from(files));
    setUploading(false);
    if (fileRef.current) fileRef.current.value = '';
    refreshDocs();
  };

  const handleDelete = async (source: string) => {
    await api.deleteDoc(source);
    refreshDocs();
  };

  return (
    <aside
      className="flex flex-col h-full border-r border-border overflow-y-auto"
      style={{
        width: 280,
        minWidth: 280,
        background: 'linear-gradient(180deg, #0d0d18 0%, #111120 100%)',
      }}
    >
      {/* Brand */}
      <div className="text-center pt-5 pb-4 animate-fadeIn">
        <div className="text-4xl animate-float inline-block">🤖</div>
        <h3 className="gradient-text text-sm font-bold tracking-widest uppercase mt-2 mb-0.5">
          Jarvis Assistant
        </h3>
        <p className="text-[0.68rem] text-text-muted tracking-wide">
          Local-first hybrid AI · Ollama
        </p>
        <div
          className="mx-auto mt-3 rounded-full"
          style={{
            width: 40,
            height: 2,
            background: 'linear-gradient(90deg, transparent, #6c63ff, transparent)',
          }}
        />
      </div>

      {/* Connection status */}
      <div className="px-4 pb-3 animate-slideIn">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[0.68rem] text-text-muted">{api.base}</span>
          <button
            onClick={onRefresh}
            className="p-1 rounded hover:bg-accent-dim transition-colors text-text-secondary hover:text-text-primary"
            title="Refresh"
          >
            <RefreshCw size={12} />
          </button>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center">
            <StatusDot online={backendOnline} />
            <span className={backendOnline ? 'text-text-primary' : 'text-error'}>
              Backend {backendOnline ? 'online' : 'offline'}
            </span>
          </span>
          <span className="flex items-center">
            <StatusDot online={ollamaOnline} />
            <span className={ollamaOnline ? 'text-text-primary' : 'text-error'}>
              Ollama {ollamaOnline ? 'connected' : 'offline'}
            </span>
          </span>
        </div>
      </div>

      <div className="mx-4 mb-2 h-px bg-border" />

      {/* Scrollable sections */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {/* Model configuration */}
        <Section title="Models" icon={<Brain size={13} />} defaultOpen>
          {models ? (
            <>
              <p className="text-text-muted mb-1 text-[0.65rem] uppercase tracking-wider font-semibold">
                Local — Ollama
              </p>
              {(['general', 'coding', 'strong_local'] as const).map((key) => {
                const m = models[key];
                return m ? (
                  <div key={key} className="flex items-center justify-between mb-1">
                    <span className="text-text-secondary capitalize">{key.replace('_', ' ')}</span>
                    <code className="text-[0.7rem] px-1.5 py-0.5 rounded bg-accent-dim text-accent-light">
                      {m.model}
                    </code>
                  </div>
                ) : null;
              })}
              <p className="text-text-muted mt-2 mb-1 text-[0.65rem] uppercase tracking-wider font-semibold">
                Complex — OpenRouter
              </p>
              {models.complex?.configured ? (
                models.complex.models.map((m, i) => (
                  <code
                    key={i}
                    className="block text-[0.7rem] px-1.5 py-0.5 rounded bg-accent-dim text-accent-light mb-0.5 truncate"
                  >
                    {m}
                  </code>
                ))
              ) : (
                <span className="text-text-muted">Not configured</span>
              )}
            </>
          ) : (
            <span className="text-text-muted">Could not load model info</span>
          )}
        </Section>

        {/* GPU Runtime */}
        <Section title="GPU Runtime" icon={<Cpu size={13} />}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-text-muted text-[0.65rem]">Runtime diagnostics</span>
            <button
              onClick={refreshRuntime}
              className="flex items-center gap-1 text-[0.65rem] text-accent hover:text-accent-light transition-colors"
            >
              <RefreshCw size={10} /> Refresh
            </button>
          </div>
          {runtime ? (
            <>
              <div className="flex items-center gap-2 mb-1.5">
                <StatusDot online={!!runtime.ollama_reachable} />
                <span>Ollama {runtime.ollama_reachable ? 'online' : 'offline'}</span>
              </div>
              <div className="mb-1">
                <span className="text-text-muted">Model: </span>
                <code className="text-[0.7rem] px-1 rounded bg-accent-dim text-accent-light">
                  {runtime.model || 'none'}
                </code>
              </div>
              {runtime.processor && (
                <Badge
                  text={runtime.processor}
                  color={
                    runtime.processor === '100% GPU'
                      ? 'green'
                      : runtime.processor.includes('Partial')
                        ? 'orange'
                        : 'red'
                  }
                />
              )}
              {runtime.gpu_name && (
                <div className="text-text-muted mt-1">GPU: {runtime.gpu_name}</div>
              )}
              {runtime.vram_total_mb != null && runtime.vram_used_mb != null && (
                <div className="text-text-muted">
                  VRAM: {runtime.vram_used_mb} / {runtime.vram_total_mb} MB
                </div>
              )}
              {runtime.context && (
                <div className="text-text-muted mt-1 text-[0.65rem]">
                  ctx={runtime.context.num_ctx} · history≤{runtime.context.history_max_turns} turns
                </div>
              )}
              {runtime.warnings?.slice(0, 3).map((w, i) => (
                <div key={i} className="text-warning text-[0.65rem] mt-0.5">
                  ⚠ {w}
                </div>
              ))}
              {runtime.recommendations?.slice(0, 3).map((r, i) => (
                <div key={i} className="text-accent-light text-[0.65rem] mt-0.5">
                  💡 {r}
                </div>
              ))}
            </>
          ) : (
            <span className="text-text-muted">
              Click Refresh to load runtime info
            </span>
          )}
        </Section>

        {/* RAG Store */}
        <Section title="RAG Store" icon={<Database size={13} />}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-text-muted text-[0.65rem]">Document index</span>
            <button
              onClick={refreshDocs}
              className="flex items-center gap-1 text-[0.65rem] text-accent hover:text-accent-light transition-colors"
            >
              <RefreshCw size={10} /> Refresh
            </button>
          </div>
          {docCount !== null && (
            <div className="glass rounded-lg px-3 py-2 text-center mb-2">
              <div className="text-xl font-bold text-accent-light">{docCount}</div>
              <div className="text-[0.65rem] text-text-muted uppercase tracking-wider">
                indexed chunks
              </div>
            </div>
          )}
          {docs.length > 0 && (
            <div className="space-y-1 mb-2 max-h-28 overflow-y-auto">
              {docs.map((d) => (
                <div
                  key={d.source}
                  className="flex items-center justify-between gap-1 text-[0.65rem] glass rounded px-2 py-1"
                >
                  <span className="truncate flex-1 text-text-secondary">{d.source}</span>
                  <span className="text-text-muted shrink-0">{d.chunk_count}ch</span>
                  <button
                    onClick={() => handleDelete(d.source)}
                    className="text-error/60 hover:text-error transition-colors shrink-0"
                    title="Delete"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Section>

        {/* Upload */}
        <Section title="Upload Documents" icon={<Upload size={13} />}>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.markdown,.rst"
            multiple
            className="block w-full text-[0.65rem] text-text-muted mb-2 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-[0.65rem] file:bg-accent-dim file:text-accent-light file:cursor-pointer hover:file:bg-accent/20 transition-all"
          />
          <button
            onClick={handleUpload}
            disabled={uploading}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-[0.7rem] font-semibold bg-gradient-to-r from-accent to-accent-light text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {uploading ? (
              <RefreshCw size={12} className="animate-spin" />
            ) : (
              <Upload size={12} />
            )}
            {uploading ? 'Ingesting…' : 'Ingest files'}
          </button>
        </Section>

        {/* Tips */}
        <Section title="Tips & Shortcuts" icon={<Lightbulb size={13} />}>
          <ul className="space-y-1 text-text-secondary list-none p-0">
            <li className="flex gap-1.5 items-start">
              <Zap size={11} className="text-accent mt-0.5 shrink-0" />
              <span><strong className="text-text-primary">Coding</strong> questions auto-route to the coding model.</span>
            </li>
            <li className="flex gap-1.5 items-start">
              <Brain size={11} className="text-accent mt-0.5 shrink-0" />
              <span><strong className="text-text-primary">Complex</strong> prompts route to the cloud chain.</span>
            </li>
            <li className="flex gap-1.5 items-start">
              <FileText size={11} className="text-accent mt-0.5 shrink-0" />
              <span><strong className="text-text-primary">Select text</strong> from a reply and ask about it.</span>
            </li>
            <li className="flex gap-1.5 items-start">
              <Bot size={11} className="text-accent mt-0.5 shrink-0" />
              <span><strong className="text-text-primary">Background</strong> mode runs heavy prompts as async tasks.</span>
            </li>
          </ul>
        </Section>
      </div>

      {/* Bottom bar */}
      <div className="p-3 border-t border-border">
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={onClearChat}
            className="flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium glass glass-hover transition-all hover:border-error/30 hover:text-error"
          >
            <Trash2 size={13} />
            Clear
          </button>
          <button
            onClick={onExport}
            disabled={!hasMessages}
            className="flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium glass glass-hover transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Download size={13} />
            Export
          </button>
        </div>
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ */
/* Badge helper                                                        */
/* ------------------------------------------------------------------ */

function Badge({ text, color }: { text: string; color: 'green' | 'orange' | 'red' | 'gray' }) {
  const colors = {
    green: 'bg-success/15 text-success border-success/20',
    orange: 'bg-warning/15 text-warning border-warning/20',
    red: 'bg-error/15 text-error border-error/20',
    gray: 'bg-text-muted/15 text-text-secondary border-text-muted/20',
  };
  return (
    <span className={`inline-block text-[0.65rem] font-semibold px-2 py-0.5 rounded-full border ${colors[color]}`}>
      {text}
    </span>
  );
}
