import { motion } from 'framer-motion';
import { Sparkles, Code2, Calculator, Search, Layers, Bug } from 'lucide-react';

interface HeroSectionProps {
  onSuggestionClick: (text: string) => void;
}

const SUGGESTIONS: { label: string; prompt: string; icon: React.ReactNode }[] = [
  { label: 'Explain RAG', prompt: 'Explain how retrieval-augmented generation works, simply.', icon: <Sparkles size={14} /> },
  { label: 'Write Python', prompt: 'Write a Python function that returns the n-th Fibonacci number.', icon: <Code2 size={14} /> },
  { label: 'Do math', prompt: 'Calculate (123 + 456) * 7 and explain the steps.', icon: <Calculator size={14} /> },
  { label: 'Search code', prompt: "Search the workspace for 'TODO' comments.", icon: <Search size={14} /> },
  { label: 'System design', prompt: 'Design a URL shortener service with high availability.', icon: <Layers size={14} /> },
  { label: 'Debug help', prompt: 'What are common causes of memory leaks in Python web apps?', icon: <Bug size={14} /> },
];

const containerVariants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.07, delayChildren: 0.3 },
  },
};

const pillVariants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
};

export default function HeroSection({ onSuggestionClick }: HeroSectionProps) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6 }}
      className="flex flex-col items-center justify-center text-center px-4 py-16 select-none"
    >
      {/* Floating icon */}
      <div className="text-6xl animate-float mb-4">🤖</div>

      {/* Title */}
      <h2 className="gradient-text text-3xl font-bold tracking-tight mb-2">
        What can I help you with?
      </h2>

      {/* Subtitle */}
      <p className="text-[var(--color-text-secondary)] text-sm max-w-md">
        Ask anything — I route your question to the best model for the job.
      </p>

      {/* Divider */}
      <div className="w-16 h-[3px] rounded-full bg-gradient-to-r from-transparent via-[var(--color-accent)] to-transparent my-6" />

      {/* Quick start label */}
      <p className="text-[var(--color-text-muted)] text-[0.7rem] uppercase tracking-[0.14em] font-semibold mb-4">
        Quick start
      </p>

      {/* Suggestion pills */}
      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
        className="flex flex-wrap justify-center gap-2.5 max-w-lg"
      >
        {SUGGESTIONS.map((s) => (
          <motion.button
            key={s.label}
            variants={pillVariants}
            whileHover={{ y: -2, boxShadow: '0 4px 20px rgba(108, 99, 255, 0.25)' }}
            whileTap={{ scale: 0.97 }}
            onClick={() => onSuggestionClick(s.prompt)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-medium
              border border-[var(--color-accent)]/25 bg-[var(--color-accent)]/[0.06]
              text-[var(--color-text-primary)]
              hover:bg-[var(--color-accent)]/[0.15] hover:border-[var(--color-accent)]/50
              transition-colors duration-200 cursor-pointer"
          >
            <span className="text-[var(--color-accent-light)]">{s.icon}</span>
            {s.label}
          </motion.button>
        ))}
      </motion.div>
    </motion.div>
  );
}
