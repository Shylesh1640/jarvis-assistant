export interface Message {
  role: 'user' | 'assistant';
  content: string;
  path?: string;
  model?: string;
  tools_used?: string[];
  sources?: Source[];
  retrieved_context?: string;
  approval_required?: boolean;
  deep_thinking_used?: boolean;
  reasoning_strategy?: string;
  reasoning_steps?: number;
  reasoning_chain?: ReasoningStep[];
  tokens_used_reasoning?: number;
  tokens_used_answer?: number;
  total_tokens?: number;
  latency_ms_reasoning?: number;
  latency_ms_answer?: number;
  total_latency_ms?: number;
  _feedback_sent?: boolean;
}

export interface Source {
  source: string;
  chunk_id?: string;
  doc?: string;
}

export interface ReasoningStep {
  step_number: number;
  sub_problem: string;
  analysis: string;
  conclusion: string;
}

export interface HealthData {
  status: string;
  ollama_reachable: boolean;
}

export interface ModelsConfig {
  general?: ModelInfo;
  coding?: ModelInfo;
  strong_local?: ModelInfo;
  complex?: { configured: boolean; models: string[] };
}

export interface ModelInfo {
  model: string;
  [key: string]: unknown;
}

export interface RuntimeSnap {
  ollama_reachable?: boolean;
  model?: string;
  processor?: string;
  gpu_name?: string;
  vram_total_mb?: number;
  vram_used_mb?: number;
  context?: { num_ctx?: number; history_max_turns?: number; context_token_budget?: number };
  parallel?: { num_parallel?: number; max_loaded_models?: number };
  warnings?: string[];
  recommendations?: string[];
  runtime?: Record<string, unknown>;
  docker?: Record<string, unknown>;
  wsl?: Record<string, unknown>;
  gpu_policy?: Record<string, unknown>;
}

export interface TraceEntry {
  request_id: string;
  intent?: string;
  path_used?: string;
  selected_model?: string;
  duration_ms?: number;
  error?: string;
  approval_status?: string;
  gpu_policy?: string;
  processor_split?: string;
  estimated_cost_usd?: number;
  cloud_used?: boolean;
}

export interface DocEntry {
  source: string;
  chunk_count: number;
}

export interface TaskData {
  id: string;
  status: string;
  description?: string;
  result?: string;
  error?: string;
  pending_action?: string;
  pending_tool_calls?: ToolCall[];
  stage?: string;
}

export interface ToolCall {
  name: string;
  args?: Record<string, unknown>;
}

export interface Toggles {
  show_reasoning: boolean;
  answer_style: string;
  background_task: boolean;
  debug: boolean;
  deep_thinking: boolean;
  show_reasoning_chain: boolean;
  reasoning_strategy: string;
}
