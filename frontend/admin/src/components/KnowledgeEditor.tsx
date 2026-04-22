import { useState, useEffect } from 'react';
import {
  Box, TextField, Button, Alert, CircularProgress,
  Typography, IconButton, Paper, Stack, Divider,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import * as api from '../api';

interface KnowledgeEntry {
  name: string;
  description: string;
  instructions: string;
}

/** SKILL.md 形式の文字列を KnowledgeEntry にパースする */
function parseKnowledgeContent(raw: string): KnowledgeEntry {
  const trimmed = raw.trim();
  const match = trimmed.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
  if (!match) return { name: '', description: '', instructions: trimmed };

  const frontmatter = match[1];
  const instructions = match[2].trim();

  const nameMatch = frontmatter.match(/^name:\s*(.+)$/m);
  const descMatch = frontmatter.match(/^description:\s*(.+)$/m);

  return {
    name: nameMatch?.[1]?.trim() ?? '',
    description: descMatch?.[1]?.trim() ?? '',
    instructions,
  };
}

/** KnowledgeEntry を SKILL.md 形式の文字列に変換する */
function toKnowledgeContent(entry: KnowledgeEntry): string {
  return `---\nname: ${entry.name}\ndescription: ${entry.description}\n---\n${entry.instructions}`;
}

const EMPTY_ENTRY: KnowledgeEntry = { name: '', description: '', instructions: '' };

interface KnowledgeEditorProps {
  agentId: string;
  onBack: () => void;
}

export default function KnowledgeEditor({ agentId, onBack }: KnowledgeEditorProps) {
  const [items, setItems] = useState<KnowledgeEntry[]>([]);
  const [agentName, setAgentName] = useState('');
  const [agentNameSaved, setAgentNameSaved] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [systemPromptSaved, setSystemPromptSaved] = useState('');
  const [loading, setLoading] = useState(false);
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [savingName, setSavingName] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([api.getAgent(agentId), api.getSystemPrompt(agentId)])
      .then(([agentData, promptData]) => {
        const raw: string[] = agentData.skills ?? [];
        setItems(raw.length > 0 ? raw.map(parseKnowledgeContent) : []);
        setAgentName(agentData.agent_name ?? '');
        setAgentNameSaved(agentData.agent_name ?? '');
        const sp = promptData.system_prompt ?? '';
        setSystemPrompt(sp);
        setSystemPromptSaved(sp);
      })
      .catch(e => setMessage({ type: 'error', text: e.message }))
      .finally(() => setLoading(false));
  }, [agentId]);

  const updateField = (idx: number, field: keyof KnowledgeEntry, value: string) => {
    setItems(prev => prev.map((s, i) => (i === idx ? { ...s, [field]: value } : s)));
  };

  const addItem = () => setItems(prev => [...prev, { ...EMPTY_ENTRY }]);

  const removeItem = (idx: number) => setItems(prev => prev.filter((_, i) => i !== idx));

  const handleSavePrompt = async () => {
    setSavingPrompt(true);
    setMessage(null);
    try {
      await api.updateSystemPrompt(agentId, systemPrompt);
      setSystemPromptSaved(systemPrompt);
      setMessage({ type: 'success', text: 'System Prompt を保存しました' });
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message });
    } finally {
      setSavingPrompt(false);
    }
  };

  const handleSaveAgentName = async () => {
    setSavingName(true);
    setMessage(null);
    try {
      await api.updateAgent(agentId, { agent_name: agentName });
      setAgentNameSaved(agentName);
      setMessage({ type: 'success', text: 'Agent 名を保存しました' });
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message });
    } finally {
      setSavingName(false);
    }
  };

  const handleSave = async () => {
    const invalid = items.some(s => !s.name.trim() || !s.description.trim() || !s.instructions.trim());
    if (invalid) {
      setMessage({ type: 'error', text: 'すべての Knowledge に name, description, instructions を入力してください' });
      return;
    }

    setLoading(true);
    setMessage(null);
    try {
      const arr = items.map(toKnowledgeContent);
      await api.updateKnowledge(agentId, arr);
      setMessage({ type: 'success', text: '保存しました' });
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message });
    } finally {
      setLoading(false);
    }
  };

  if (loading && items.length === 0) return <CircularProgress />;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 800 }}>
      <Button onClick={onBack} sx={{ alignSelf: 'flex-start' }}>← Agent 一覧に戻る</Button>

      {message && <Alert severity={message.type} onClose={() => setMessage(null)}>{message.text}</Alert>}

      {/* ---- Agent Name ---- */}
      <Typography variant="h6">Agent 名</Typography>
      <TextField
        fullWidth
        value={agentName}
        onChange={e => setAgentName(e.target.value)}
        placeholder="Agent 名を入力..."
      />
      <Button
        variant="contained"
        onClick={handleSaveAgentName}
        disabled={savingName || agentName === agentNameSaved}
      >
        {savingName ? <CircularProgress size={20} /> : 'Agent 名を保存'}
      </Button>

      <Divider sx={{ my: 2 }} />

      {/* ---- System Prompt ---- */}
      <Typography variant="h6">System Prompt</Typography>
      <Typography variant="body2" color="text.secondary">
        Agent に与える System Prompt を編集できます。
      </Typography>
      <TextField
        multiline
        minRows={6}
        fullWidth
        value={systemPrompt}
        onChange={e => setSystemPrompt(e.target.value)}
        placeholder="System Prompt を入力..."
      />
      <Button
        variant="contained"
        onClick={handleSavePrompt}
        disabled={savingPrompt || systemPrompt === systemPromptSaved}
      >
        {savingPrompt ? <CircularProgress size={20} /> : 'System Prompt を保存'}
      </Button>

      <Divider sx={{ my: 2 }} />

      {/* ---- Knowledge ---- */}
      <Typography variant="h6">Knowledge</Typography>
      <Typography variant="body2" color="text.secondary">
        Agent に追加の知識を与えます。name（英数字・ハイフン）と description は必須です。
      </Typography>

      {items.map((item, idx) => (
        <Paper key={idx} variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={2}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Typography variant="subtitle2">Knowledge {idx + 1}</Typography>
              <IconButton size="small" color="error" onClick={() => removeItem(idx)} aria-label={`Knowledge ${idx + 1} を削除`}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Box>
            <TextField
              label="name"
              size="small"
              fullWidth
              required
              value={item.name}
              onChange={e => updateField(idx, 'name', e.target.value)}
              placeholder="例: sql-best-practices"
              helperText="英小文字・数字・ハイフン（1〜64文字）"
            />
            <TextField
              label="description"
              size="small"
              fullWidth
              required
              value={item.description}
              onChange={e => updateField(idx, 'description', e.target.value)}
              placeholder="例: SQLクエリのベストプラクティスをレビューする"
            />
            <TextField
              label="instructions"
              multiline
              minRows={3}
              fullWidth
              required
              value={item.instructions}
              onChange={e => updateField(idx, 'instructions', e.target.value)}
              placeholder="エージェントへの指示をMarkdownで記述..."
            />
          </Stack>
        </Paper>
      ))}

      <Button variant="outlined" startIcon={<AddIcon />} onClick={addItem}>
        Knowledge を追加
      </Button>

      <Button variant="contained" onClick={handleSave} disabled={loading}>
        Knowledge を保存
      </Button>
    </Box>
  );
}
