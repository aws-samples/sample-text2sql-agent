import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { AppBar, Toolbar, Typography, Box, Button } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import SessionSidebar from '../components/SessionSidebar';
import ChatInterface from '../components/ChatInterface';
import * as api from '../api';

interface Props {
  signOut?: () => void;
  user: any;
}

export default function ChatPage({ signOut, user }: Props) {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<any[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>();
  const [currentMessages, setCurrentMessages] = useState<any[]>([]);

  useEffect(() => {
    if (!agentId) { navigate('/'); }
  }, [agentId, navigate]);

  const loadSessions = useCallback(async () => {
    if (!agentId) return;
    try {
      const data = await api.fetchSessions(agentId);
      setSessions(data.sessions ?? []);
    } catch (e) {
      console.error(e);
    }
  }, [agentId]);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  useEffect(() => {
    if (!currentSessionId) { setCurrentMessages([]); return; }
    api.fetchSessionDetail(currentSessionId).then(d => setCurrentMessages(d.messages ?? [])).catch(console.error);
  }, [currentSessionId]);

  if (!agentId) return null;

  return (
    <>
      <AppBar position="static">
        <Toolbar sx={{ justifyContent: 'space-between' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Button color="inherit" startIcon={<ArrowBackIcon />} onClick={() => navigate('/')}>
              Agent 一覧
            </Button>
            <Typography variant="h6">Agent</Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Typography variant="body2">{user?.username}</Typography>
            <Button color="inherit" onClick={signOut}>Sign Out</Button>
          </Box>
        </Toolbar>
      </AppBar>
      <Box sx={{ display: 'flex', height: 'calc(100vh - 64px)' }}>
        <SessionSidebar
          sessions={sessions}
          currentSessionId={currentSessionId}
          onSelect={setCurrentSessionId}
          onNew={() => setCurrentSessionId(undefined)}
          onDelete={async (id) => { await api.deleteSession(id); if (currentSessionId === id) setCurrentSessionId(undefined); loadSessions(); }}
          onRefresh={loadSessions}
        />
        <ChatInterface
          key={currentSessionId ?? '__new__'}
          agentId={agentId}
          sessionId={currentSessionId}
          initialMessages={currentMessages}
          onSessionCreated={() => {}}
          onStreamComplete={(sessionId?: string) => {
            if (sessionId) setCurrentSessionId(sessionId);
            loadSessions();
          }}
        />
      </Box>
    </>
  );
}
