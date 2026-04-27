import { fetchAuthSession } from 'aws-amplify/auth';

const API_ENDPOINT = import.meta.env.VITE_APP_API_ENDPOINT;

async function getAuthHeaders(): Promise<Record<string, string>> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString() ?? '';
  return { Authorization: token, 'Content-Type': 'application/json' };
}

/**
 * レスポンスが ok でない場合、FastAPI の detail メッセージを含む Error を投げる。
 * detail が取得できない場合はステータスコードのみ出力する。
 */
async function throwIfNotOk(res: Response): Promise<void> {
  if (res.ok) return;
  let detail: string | undefined;
  try {
    const body = await res.clone().json();
    if (body && typeof body.detail === 'string') {
      detail = body.detail;
    } else if (typeof body === 'string') {
      detail = body;
    }
  } catch {
    try {
      const text = await res.text();
      if (text) detail = text;
    } catch {
      // ignore
    }
  }
  throw new Error(detail ? `${res.status}: ${detail}` : `Failed: ${res.status}`);
}

// ---------------------------------------------------------------------------
// Agent CRUD
// ---------------------------------------------------------------------------
export interface AgentSummary {
  agent_id: string;
  agent_name: string;
  system_prompt: string;
  created_at: string;
  updated_at: string;
}

export async function listAgents(): Promise<{ agents: AgentSummary[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/agents`, { headers });
  await throwIfNotOk(res);
  return res.json();
}

export async function createAgent(agentName: string): Promise<{ agent_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/agents`, {
    method: 'POST', headers, body: JSON.stringify({ agent_name: agentName }),
  });
  await throwIfNotOk(res);
  return res.json();
}

export async function getAgent(agentId: string) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/agents/${agentId}`, { headers });
  await throwIfNotOk(res);
  return res.json();
}

export async function updateAgent(agentId: string, data: { agent_name?: string }) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/agents/${agentId}`, {
    method: 'PUT', headers, body: JSON.stringify(data),
  });
  await throwIfNotOk(res);
  return res.json();
}

export async function deleteAgent(agentId: string) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/agents/${agentId}`, {
    method: 'DELETE', headers,
  });
  await throwIfNotOk(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
export async function getConfig(agentId: string) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/config?agent_id=${agentId}`, { headers });
  await throwIfNotOk(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Presigned URLs / List CSV / Analyze
// ---------------------------------------------------------------------------
export async function getPresignedUrls(filenames: string[]): Promise<{ prefix: string; urls: Record<string, string> }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/presigned-urls`, { method: 'POST', headers, body: JSON.stringify({ filenames }) });
  await throwIfNotOk(res);
  return res.json();
}

export async function listCsv(prefix: string): Promise<{ prefix: string; files: string[]; manifests: string[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/list-csv`, { method: 'POST', headers, body: JSON.stringify({ prefix }) });
  await throwIfNotOk(res);
  return res.json();
}

export interface AnalyzeProgress {
  step: 'analyze_csv' | 'generate_prompt';
  current: number;
  total: number;
  file: string;
}

export async function analyze(
  prefix: string,
  onProgress?: (progress: AnalyzeProgress) => void,
): Promise<{ system_prompt: string; db_schema: any }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/analyze`, { method: 'POST', headers, body: JSON.stringify({ prefix }) });
  await throwIfNotOk(res);

  const reader = res.body?.getReader();
  if (!reader) throw new Error('ReadableStream not supported');

  const decoder = new TextDecoder();
  let buffer = '';
  let result: { system_prompt: string; db_schema: any } | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';

    for (const part of parts) {
      const lines = part.split('\n');
      let eventType = '';
      let data = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) eventType = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (!eventType || !data) continue;

      const parsed = JSON.parse(data);
      if (eventType === 'progress' && onProgress) {
        onProgress(parsed as AnalyzeProgress);
      } else if (eventType === 'result') {
        result = parsed;
      } else if (eventType === 'error') {
        throw new Error(parsed.message);
      }
    }
  }

  if (!result) throw new Error('分析結果を取得できませんでした');
  return result;
}

// ---------------------------------------------------------------------------
// Apply (with agent_name)
// ---------------------------------------------------------------------------
export async function apply(prefix: string, systemPrompt: string, dbSchema: any, agentName: string = 'Default Agent'): Promise<{ execution_id: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/apply`, {
    method: 'POST', headers,
    body: JSON.stringify({ prefix, system_prompt: systemPrompt, db_schema: dbSchema, agent_name: agentName }),
  });
  await throwIfNotOk(res);
  return res.json();
}

export interface LoadErrorDetail {
  column_name: string;
  column_type: string;
  line_number: number;
  error_message: string;
  file_name: string;
}

export async function getApplyStatus(executionId: string): Promise<{ status: string; tables_created?: string[]; errors?: string[]; load_error_details?: LoadErrorDetail[] }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/apply-status`, {
    method: 'POST', headers,
    body: JSON.stringify({ execution_id: executionId }),
  });
  await throwIfNotOk(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Knowledge (agent_id based)
// ---------------------------------------------------------------------------
export async function updateKnowledge(agentId: string, knowledge: string[]) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/knowledge`, {
    method: 'POST', headers,
    body: JSON.stringify({ agent_id: agentId, skills: knowledge }),
  });
  await throwIfNotOk(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// System Prompt (agent_id based)
// ---------------------------------------------------------------------------
export async function getSystemPrompt(agentId: string): Promise<{ system_prompt: string }> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/system-prompt?agent_id=${agentId}`, { headers });
  await throwIfNotOk(res);
  return res.json();
}

export async function updateSystemPrompt(agentId: string, systemPrompt: string) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}admin/system-prompt`, {
    method: 'PUT', headers,
    body: JSON.stringify({ agent_id: agentId, system_prompt: systemPrompt }),
  });
  await throwIfNotOk(res);
  return res.json();
}
