import { fetchAuthSession } from 'aws-amplify/auth';

const API_ENDPOINT = import.meta.env.VITE_APP_API_ENDPOINT;

async function getAuthHeaders(): Promise<Record<string, string>> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString() ?? '';
  return { Authorization: token, 'Content-Type': 'application/json' };
}

export async function fetchAgents() {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}agents`, { headers });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  return res.json();
}

export async function fetchSessions(agentId?: string) {
  const headers = await getAuthHeaders();
  const url = agentId ? `${API_ENDPOINT}sessions?agent_id=${agentId}` : `${API_ENDPOINT}sessions`;
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`Failed to fetch sessions: ${res.status}`);
  return res.json();
}

export async function fetchSessionDetail(sessionId: string) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}sessions/${sessionId}`, { headers });
  if (!res.ok) throw new Error(`Failed to fetch session: ${res.status}`);
  return res.json();
}

export async function deleteSession(sessionId: string) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}sessions/${sessionId}`, { method: 'DELETE', headers });
  if (!res.ok) throw new Error(`Failed to delete session: ${res.status}`);
  return res.json();
}

export interface ChartSpec {
  type: 'bar' | 'line' | 'pie';
  title?: string;
  xKey: string;
  yKeys: string[];
  data: Record<string, unknown>[];
}

export interface ChatCallbacks {
  onToken: (content: string) => void;
  onToolUse: (tool: string, input?: string) => void;
  onChart: (spec: ChartSpec) => void;
  onError: (error: string) => void;
  onDone: () => void;
}

export async function streamChat(sessionId: string, message: string, agentId: string, callbacks: ChatCallbacks) {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_ENDPOINT}chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ session_id: sessionId, message, agent_id: agentId }),
  });
  if (!res.ok) throw new Error(`Chat request failed: ${res.status}`);
  if (!res.body) throw new Error('No response body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data: ')) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.type === 'token') callbacks.onToken(data.content);
        else if (data.type === 'tool_use') callbacks.onToolUse(data.tool, data.input);
        else if (data.type === 'chart') callbacks.onChart(data.spec);
        else if (data.type === 'error') callbacks.onError(data.content);
        else if (data.type === 'done') callbacks.onDone();
      } catch { /* ignore parse errors */ }
    }
  }
}
