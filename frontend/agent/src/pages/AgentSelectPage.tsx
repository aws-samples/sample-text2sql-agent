import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Box, Button, Card, CardContent,
  CircularProgress, Alert,
} from '@mui/material';
import * as api from '../api';

interface Props {
  signOut?: () => void;
  user: any;
}

export default function AgentSelectPage({ signOut, user }: Props) {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.fetchAgents()
      .then(data => setAgents(data.agents ?? []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <AppBar position="static">
        <Toolbar sx={{ justifyContent: 'space-between' }}>
          <Typography variant="h6">Agent</Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Typography variant="body2">{user?.username}</Typography>
            <Button color="inherit" onClick={signOut}>Sign Out</Button>
          </Box>
        </Toolbar>
      </AppBar>
      <Box sx={{ p: 3, maxWidth: 800, mx: 'auto' }}>
        <Typography variant="h5" sx={{ mb: 3 }}>Agent を選択</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {loading ? <CircularProgress /> : agents.length === 0 ? (
          <Typography color="text.secondary">利用可能な Agent がありません。管理者にお問い合わせください。</Typography>
        ) : (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {agents.map(agent => (
              <Card key={agent.agent_id} variant="outlined"
                sx={{ cursor: 'pointer', '&:hover': { bgcolor: 'action.hover' } }}
                onClick={() => navigate(`/agent/${agent.agent_id}`)}>
                <CardContent>
                  <Typography variant="h6">{agent.agent_name || '(名前なし)'}</Typography>
                </CardContent>
              </Card>
            ))}
          </Box>
        )}
      </Box>
    </>
  );
}
