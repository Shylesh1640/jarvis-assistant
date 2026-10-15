import type { Toggles } from '../types';
import { Brain, Eye, Link2, Bug, ListTodo, Sparkles } from 'lucide-react';

const STRATEGIES = ['auto', 'cot', 'tot', 'self_consistency', 'reflexion', 'fast_and_slow'];
const STYLES = ['default', 'concise', 'detailed', 'code', 'teaching', 'architecture', 'research'];

interface ToggleToolbarProps {
  toggles: Toggles;
  onChange: (t: Toggles) => void;
}

function Toggle({ label, icon: Icon, active, onToggle }: {
  label: string;
  icon: typeof Brain;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 border ${
        active
          ? 'bg-[var(--color-accent-dim)] border-[var(--color-border-hover)] text-[var(--color-accent-light)]'
          : 'bg-transparent border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-secondary)]'
      }`}
    >
      <Icon size={13} />
      {label}
    </button>
  );
}

function Select({ value, options, onChange }: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-xs rounded-lg px-2 py-1.5 outline-none focus:border-[var(--color-border-hover)] transition-colors cursor-pointer"
    >
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}

export default function ToggleToolbar({ toggles, onChange }: ToggleToolbarProps) {
  const set = (k: keyof Toggles, v: unknown) => onChange({ ...toggles, [k]: v });

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2 max-w-[800px] mx-auto">
      <Toggle label="Reasoning" icon={Brain} active={toggles.show_reasoning} onToggle={() => set('show_reasoning', !toggles.show_reasoning)} />
      <Toggle label="Deep Think" icon={Sparkles} active={toggles.deep_thinking} onToggle={() => set('deep_thinking', !toggles.deep_thinking)} />
      <Toggle label="Show Chain" icon={Link2} active={toggles.show_reasoning_chain} onToggle={() => set('show_reasoning_chain', !toggles.show_reasoning_chain)} />
      <Toggle label="Debug" icon={Bug} active={toggles.debug} onToggle={() => set('debug', !toggles.debug)} />
      <Toggle label="Background" icon={ListTodo} active={toggles.background_task} onToggle={() => set('background_task', !toggles.background_task)} />
      <Select value={toggles.reasoning_strategy} options={STRATEGIES} onChange={(v) => set('reasoning_strategy', v)} />
      <Select value={toggles.answer_style} options={STYLES} onChange={(v) => set('answer_style', v)} />
    </div>
  );
}
