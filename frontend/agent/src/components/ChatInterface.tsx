import { useState, useRef, useEffect } from 'react';
import { Box, TextField, IconButton, Paper, Typography, CircularProgress, Tooltip, Chip, Button } from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import DownloadIcon from '@mui/icons-material/Download';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { streamChat } from '../api';
import type { ChartSpec } from '../api';
import ChartRenderer from './ChartRenderer';

interface ToolUseEntry {
  tool: string;
  sql?: string;
  description?: string;
  skillName?: string;
}

type CsvStatus = 'ready' | 'expired' | 'not_found' | 'objects_missing' | 'failed' | 'error' | 'pending';

interface CsvAttachment {
  file_id: string;
  url?: string;
  filename: string;
  expires_at?: number;
  status: CsvStatus;
  description?: string;
  errorMessage?: string;
}

interface Message {
  role: string;
  content: string;
  toolUses?: ToolUseEntry[];
  charts?: ChartSpec[];
  csvFiles?: CsvAttachment[];
}

interface Props {
  agentId: string;
  sessionId?: string;
  initialMessages: Message[];
  onSessionCreated: (id: string) => void;
  onStreamComplete?: (sessionId?: string) => void;
}

function csvStatusLabel(att: CsvAttachment): string {
  switch (att.status) {
    case 'expired':
      return `${att.filename || 'CSV'} (リンク失効)`;
    case 'not_found':
      return `${att.filename || 'CSV'} (ファイルが見つかりません)`;
    case 'objects_missing':
      return `${att.filename || 'CSV'} (S3 上のファイルが削除されています)`;
    case 'failed':
      return `CSV 作成失敗${att.errorMessage ? `: ${att.errorMessage}` : ''}`;
    case 'error':
      return `CSV 取得エラー${att.errorMessage ? `: ${att.errorMessage}` : ''}`;
    case 'pending':
      return `${att.filename || 'CSV'} (生成中)`;
    default:
      return att.filename || 'CSV';
  }
}

export default function ChatInterface({ agentId, sessionId, initialMessages, onSessionCreated, onStreamComplete }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (loading) return;
    const mapped = initialMessages.map((msg: any) => {
      if (msg.role !== 'assistant' || !msg.tool_uses?.length) return msg;
      const toolUses: ToolUseEntry[] = [];
      const charts: ChartSpec[] = [];
      const csvFiles: CsvAttachment[] = [];
      for (const t of msg.tool_uses) {
        if (t.tool === '_redshift_query') {
          let sql = '';
          let description = '';
          try {
            const parsed = JSON.parse(t.input ?? '{}');
            sql = parsed.sql_query ?? '';
            description = parsed.description ?? '';
          } catch (err) { console.error('[ChatInterface] _redshift_query input parse failed', err); }
          toolUses.push({ tool: t.tool, sql, description });
        } else if (t.tool === '_render_chart' && t.chart_spec) {
          charts.push(t.chart_spec as ChartSpec);
        } else if (t.tool === '_create_csv_file') {
          // バックエンドが付加した csv_status を元に CsvAttachment を構築
          const status: CsvStatus = (t.csv_status as CsvStatus) ?? 'pending';
          csvFiles.push({
            file_id: t.file_id ?? '',
            url: t.url,
            filename: t.filename ?? '',
            expires_at: t.expires_at,
            status,
            description: t.description ?? '',
            errorMessage: t.error_message,
          });
        } else if (t.tool === 'skills') {
          let skillName = '';
          try {
            const parsed = JSON.parse(t.input ?? '{}');
            skillName = parsed.skill_name ?? '';
          } catch (err) { console.error('[ChatInterface] skills input parse failed', err); }
          toolUses.push({ tool: t.tool, skillName });
        } else {
          toolUses.push({ tool: t.tool });
        }
      }
      return {
        ...msg,
        toolUses,
        ...(charts.length > 0 ? { charts } : {}),
        ...(csvFiles.length > 0 ? { csvFiles } : {}),
      };
    });
    setMessages(mapped);
  }, [initialMessages]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput('');

    const sid = sessionId ?? crypto.randomUUID();
    if (!sessionId) onSessionCreated(sid);

    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setLoading(true);

    let assistantContent = '';
    let toolUses: ToolUseEntry[] = [];
    let charts: ChartSpec[] = [];
    let csvFiles: CsvAttachment[] = [];
    setMessages(prev => [...prev, { role: 'assistant', content: '', toolUses: [], charts: [], csvFiles: [] }]);

    const updateLast = () => {
      setMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: 'assistant',
          content: assistantContent,
          toolUses: [...toolUses],
          charts: [...charts],
          csvFiles: [...csvFiles],
        };
        return updated;
      });
    };

    try {
      await streamChat(sid, userMsg, agentId, {
        onToken: (chunk) => {
          if (!mountedRef.current) return;
          assistantContent += chunk;
          updateLast();
        },
        onToolUse: (tool, input) => {
          if (!mountedRef.current) return;
          if (tool === '_redshift_query') {
            let sql = '';
            let description = '';
            try {
              const parsed = JSON.parse(input ?? '{}');
              sql = parsed.sql_query ?? '';
              description = parsed.description ?? '';
            } catch (err) { console.error('[ChatInterface] _redshift_query input parse failed', err); }
            toolUses = [...toolUses, { tool, sql, description }];
          } else if (tool === 'skills') {
            let skillName = '';
            try {
              const parsed = JSON.parse(input ?? '{}');
              skillName = parsed.skill_name ?? '';
            } catch (err) { console.error('[ChatInterface] skills input parse failed', err); }
            toolUses = [...toolUses, { tool, skillName }];
          } else if (tool === '_render_chart') {
            toolUses = [...toolUses, { tool }];
          } else if (tool === '_create_csv_file') {
            // 生成中チップを表示しておく (csv_url SSE が来たら ready に上書き)
            let description = '';
            try {
              const parsed = JSON.parse(input ?? '{}');
              description = parsed.description ?? '';
            } catch (err) { console.error('[ChatInterface] _create_csv_file input parse failed', err); }
            toolUses = [...toolUses, { tool, description }];
          } else {
            assistantContent += `\n\n🔧 *${tool}*\n\n`;
            toolUses = [...toolUses, { tool }];
          }
          updateLast();
        },
        onChart: (spec) => {
          if (!mountedRef.current) return;
          charts = [...charts, spec];
          updateLast();
        },
        onCsvUrl: (csv) => {
          if (!mountedRef.current) return;
          csvFiles = [...csvFiles, {
            file_id: csv.file_id,
            url: csv.url,
            filename: csv.filename,
            expires_at: csv.expires_at,
            status: 'ready',
            description: csv.description,
          }];
          updateLast();
        },
        onError: (err) => {
          if (!mountedRef.current) return;
          assistantContent += `\n\n❌ Error: ${err}`;
          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: 'assistant', content: assistantContent };
            return updated;
          });
        },
        onDone: () => {},
      });
    } catch (e: any) {
      if (!mountedRef.current) return;
      setMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: 'assistant', content: `Error: ${e.message}` };
        return updated;
      });
    } finally {
      if (!mountedRef.current) return;
      setLoading(false);
      onStreamComplete?.(sid);
    }
  };

  return (
    <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
        {messages.map((msg, i) => (
          <Box key={i} sx={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', mb: 1 }}>
            <Paper sx={{ p: 1.5, maxWidth: '75%', bgcolor: msg.role === 'user' ? 'primary.main' : 'grey.100', color: msg.role === 'user' ? 'white' : 'text.primary' }}>
              {msg.role === 'assistant' ? (
                <>
                  {msg.toolUses?.filter(t => t.tool === '_redshift_query').map((t, j) => (
                    <Tooltip key={j} title={<pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxWidth: 500, fontSize: 12 }}>{t.sql}</pre>} arrow placement="top">
                      <Chip label={t.description || 'SQL'} size="small" sx={{ mb: 0.5, cursor: 'pointer', display: 'flex', width: 'fit-content' }} />
                    </Tooltip>
                  ))}
                  {msg.toolUses?.filter(t => t.tool === 'skills' && t.skillName).map((t, j) => (
                    <Chip key={`knowledge-${j}`} label={`${t.skillName}のナレッジを活用します`} size="small" sx={{ mb: 0.5, display: 'flex', width: 'fit-content' }} />
                  ))}
                  {msg.toolUses?.filter(t => t.tool === '_render_chart').map((_, j) => (
                    <Chip key={`chart-chip-${j}`} label="グラフを描いています" size="small" sx={{ mb: 0.5, display: 'flex', width: 'fit-content' }} />
                  ))}
                  {msg.charts?.map((spec, j) => (
                    <ChartRenderer key={`chart-${j}`} spec={spec} />
                  ))}
                  {msg.csvFiles?.map((csv, j) => (
                    csv.status === 'ready' && csv.url ? (
                      <Button
                        key={`csv-${j}`}
                        variant="contained"
                        size="small"
                        href={csv.url}
                        download={csv.filename}
                        startIcon={<DownloadIcon />}
                        sx={{ mb: 0.5, display: 'flex', width: 'fit-content', textTransform: 'none' }}
                      >
                        {csv.filename} をダウンロード
                      </Button>
                    ) : (
                      <Tooltip key={`csv-${j}`} title={csv.description || ''} placement="top" arrow>
                        <Chip
                          label={csvStatusLabel(csv)}
                          size="small"
                          color="warning"
                          sx={{ mb: 0.5, display: 'flex', width: 'fit-content' }}
                        />
                      </Tooltip>
                    )
                  ))}
                  {msg.content
                    ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    : loading && i === messages.length - 1
                      ? <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5 }}><CircularProgress size={16} /><Typography variant="body2" color="text.secondary">考え中...</Typography></Box>
                      : <ReactMarkdown remarkPlugins={[remarkGfm]}>{'...'}</ReactMarkdown>
                  }
                </>
              ) : (
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{msg.content}</Typography>
              )}
            </Paper>
          </Box>
        ))}
        <div ref={bottomRef} />
      </Box>
      <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider', display: 'flex', gap: 1 }}>
        <TextField
          fullWidth size="small" placeholder="メッセージを入力..."
          value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); handleSend(); } }}
          disabled={loading} multiline maxRows={4}
        />
        <IconButton color="primary" onClick={handleSend} disabled={loading || !input.trim()}>
          {loading ? <CircularProgress size={24} /> : <SendIcon />}
        </IconButton>
      </Box>
    </Box>
  );
}
