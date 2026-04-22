import { Box, List, ListItemButton, ListItemText, IconButton, Typography, Button } from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import RefreshIcon from '@mui/icons-material/Refresh';

interface Props {
  sessions: any[];
  currentSessionId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRefresh: () => void;
}

export default function SessionSidebar({ sessions, currentSessionId, onSelect, onNew, onDelete, onRefresh }: Props) {
  return (
    <Box sx={{ width: 280, borderRight: 1, borderColor: 'divider', display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Box sx={{ p: 1, display: 'flex', gap: 1 }}>
        <Button variant="outlined" startIcon={<AddIcon />} onClick={onNew} fullWidth size="small">New Chat</Button>
        <IconButton size="small" onClick={onRefresh}><RefreshIcon /></IconButton>
      </Box>
      <List sx={{ flex: 1, overflow: 'auto' }}>
        {sessions.map((s) => (
          <ListItemButton key={s.session_id} selected={s.session_id === currentSessionId} onClick={() => onSelect(s.session_id)}>
            <ListItemText primary={<Typography noWrap variant="body2">{s.title || 'New Chat'}</Typography>} />
            <IconButton size="small" onClick={(e) => { e.stopPropagation(); onDelete(s.session_id); }}><DeleteIcon fontSize="small" /></IconButton>
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
}
