import type {
  HealthData,
  ModelsConfig,
  RuntimeSnap,
  TraceEntry,
  DocEntry,
  TaskData,
} from './types';

const BASE = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';

async function json<T>(url: string, init?: RequestInit): Promise<T | null> {
  try {
    const r = await fetch(url, init);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export const api = {
  base: BASE,

  health: () => json<HealthData>(`${BASE}/health`),

  models: () => json<ModelsConfig>(`${BASE}/models`),

  runtime: () => json<RuntimeSnap>(`${BASE}/runtime`),

  traces: async (): Promise<TraceEntry[]> => {
    const d = await json<{ traces: TraceEntry[] }>(`${BASE}/traces/recent`);
    return d?.traces ?? [];
  },

  docCount: async (): Promise<number> => {
    const d = await json<{ count: number }>(`${BASE}/documents/count`);
    return d?.count ?? 0;
  },

  docs: async (): Promise<DocEntry[]> => {
    const d = await json<{ documents: DocEntry[] }>(`${BASE}/documents`);
    return d?.documents ?? [];
  },

  deleteDoc: async (source: string): Promise<boolean> => {
    try {
      const r = await fetch(`${BASE}/documents/${source}?confirm=1`, { method: 'DELETE' });
      return r.ok;
    } catch {
      return false;
    }
  },

  uploadDocs: async (files: File[]): Promise<{ files: string[]; chunks: number } | null> => {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f));
    try {
      const r = await fetch(`${BASE}/documents/upload`, { method: 'POST', body: fd });
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  },

  reindex: async () => {
    try {
      const r = await fetch(`${BASE}/documents/reindex`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: null }),
      });
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  },

  sessionToken: async (sessionId: string): Promise<string | null> => {
    const d = await json<{ session_token: string }>(`${BASE}/sessions/${sessionId}/token`);
    return d?.session_token ?? null;
  },

  chat: async (payload: Record<string, unknown>) => {
    const r = await fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw { status: r.status, body };
    }
    return await r.json();
  },

  createTask: async (description: string, sessionId: string, token: string | null) => {
    const r = await fetch(`${BASE}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, session_id: sessionId, session_token: token }),
    });
    if (!r.ok) return null;
    return await r.json();
  },

  pollTask: (taskId: string) => json<TaskData>(`${BASE}/tasks/${taskId}`),

  taskAction: async (taskId: string, action: string) => {
    const r = await fetch(`${BASE}/tasks/${taskId}/${action}`, { method: 'POST' });
    if (!r.ok) return null;
    return await r.json();
  },

  feedback: async (payload: Record<string, unknown>) => {
    try {
      const r = await fetch(`${BASE}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) return false;
      const d = await r.json();
      return !!d?.stored;
    } catch {
      return false;
    }
  },

  performance: async () => {
    return json<Record<string, unknown>>(`${BASE}/performance/summary`);
  },

  abTests: async () => {
    const d = await json<{ active_tests: unknown[] }>(`${BASE}/ab-testing/active`);
    return d?.active_tests ?? [];
  },
};
