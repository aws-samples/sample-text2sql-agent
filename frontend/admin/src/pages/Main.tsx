import { useState } from 'react';
import { AppBar, Toolbar, Typography, Box, Button, Tab, Tabs } from '@mui/material';
import UploadAndBuild from '../components/UploadAndBuild';
import KnowledgeEditor from '../components/KnowledgeEditor';
import AgentListPage from '../components/AgentListPage';

interface MainProps {
  signOut?: () => void;
  user: any;
}

export default function Main({ signOut, user }: MainProps) {
  const [tab, setTab] = useState(0);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  return (
    <>
      <AppBar position="static">
        <Toolbar sx={{ justifyContent: 'space-between' }}>
          <Typography variant="h6">Admin</Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Typography variant="body2">{user?.username}</Typography>
            <Button color="inherit" onClick={signOut}>Sign Out</Button>
          </Box>
        </Toolbar>
      </AppBar>
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs value={tab} onChange={(_, v) => { setTab(v); setSelectedAgentId(null); }}>
          <Tab label="Upload & Build" />
          <Tab label="Prompt & Knowledge" />
        </Tabs>
      </Box>
      <Box sx={{ p: 3 }}>
        {tab === 0 && <UploadAndBuild />}
        {tab === 1 && !selectedAgentId && (
          <AgentListPage onSelectAgent={setSelectedAgentId} />
        )}
        {tab === 1 && selectedAgentId && (
          <KnowledgeEditor agentId={selectedAgentId} onBack={() => setSelectedAgentId(null)} />
        )}
      </Box>
    </>
  );
}
