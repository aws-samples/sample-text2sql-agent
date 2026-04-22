import { useState, useEffect } from 'react';
import {
  Box, Typography, Button, Card, CardContent, CardActions,
  CircularProgress, Alert, Dialog, DialogTitle, DialogContent,
  DialogActions, TextField, IconButton,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import * as api from '../api';

interface AgentListPageProps {
  onSelectAgent: (agentId: string) => void;
}

export default function AgentListPage({ onSelectAgent }: AgentListPageProps) {
  const [agents, setAgents] = useState<api.AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const fetchAgents = async () => {
    setLoading(true);
    try {
      const data = await api.listAgents();
      setAgents(data.agents);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchAgents(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.createAgent(newName.trim());
      setCreateOpen(false);
      setNewName('');
      await fetchAgents();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (agentId: string) => {
    try {
      await api.deleteAgent(agentId);
      setDeleteTarget(null);
      await fetchAgents();
    } catch (e: any) {
      setError(e.message);
    }
  };

  if (loading) return <CircularProgress />;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 800 }}>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography variant="h6">Agent 一覧</Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
          新規 Agent 作成
        </Button>
      </Box>

      {agents.length === 0 && (
        <Typography color="text.secondary">Agent がありません。「Upload & Build」でデータを構築するか、新規作成してください。</Typography>
      )}

      {agents.map(agent => (
        <Card key={agent.agent_id} variant="outlined" sx={{ cursor: 'pointer' }}
          onClick={() => onSelectAgent(agent.agent_id)}>
          <CardContent>
            <Typography variant="subtitle1">{agent.agent_name || '(名前なし)'}</Typography>
            <Typography variant="body2" color="text.secondary" noWrap>
              {agent.system_prompt || '(System Prompt 未設定)'}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              作成: {agent.created_at ? new Date(agent.created_at).toLocaleString() : '-'}
            </Typography>
          </CardContent>
          <CardActions>
            <IconButton size="small" color="error"
              onClick={(e) => { e.stopPropagation(); setDeleteTarget(agent.agent_id); }}
              aria-label={`${agent.agent_name} を削除`}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </CardActions>
        </Card>
      ))}

      {/* 新規作成ダイアログ */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)}>
        <DialogTitle>新規 Agent 作成</DialogTitle>
        <DialogContent>
          <TextField autoFocus margin="dense" label="Agent 名" fullWidth
            value={newName} onChange={e => setNewName(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>キャンセル</Button>
          <Button onClick={handleCreate} disabled={creating || !newName.trim()} variant="contained">
            {creating ? <CircularProgress size={20} /> : '作成'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* 削除確認ダイアログ */}
      <Dialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>Agent を削除しますか？</DialogTitle>
        <DialogContent>
          <Typography>この操作は取り消せません。</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>キャンセル</Button>
          <Button onClick={() => deleteTarget && handleDelete(deleteTarget)} color="error" variant="contained">
            削除
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
