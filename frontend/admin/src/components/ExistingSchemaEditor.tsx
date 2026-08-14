import {
  Box, Typography, TextField, Accordion, AccordionSummary, AccordionDetails,
  Table, TableHead, TableBody, TableRow, TableCell, Divider,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

export interface ExistingColumn {
  name: string;
  type: string;
  description: string;
}

export interface ExistingTableSchema {
  table_name: string;
  description: string;
  columns: ExistingColumn[];
}

export interface ExistingDbSchema {
  tables: ExistingTableSchema[];
}

interface ExistingSchemaEditorProps {
  systemPrompt: string;
  onSystemPromptChange: (v: string) => void;
  dbSchema: ExistingDbSchema;
  onDbSchemaChange: (v: ExistingDbSchema) => void;
}

/**
 * existingRedshift モード用のスキーマエディタ。
 * SchemaEditor と異なり CSV 由来のフィールド (s3_keys / csv_options) を持たない。
 * カラム名・型は既存テーブルの実定義なので編集不可とし、説明のみ編集できる。
 */
export default function ExistingSchemaEditor({
  systemPrompt, onSystemPromptChange, dbSchema, onDbSchemaChange,
}: ExistingSchemaEditorProps) {

  const updateTable = (tableIdx: number, patch: Partial<ExistingTableSchema>) => {
    const tables = dbSchema.tables.map((t, i) => i === tableIdx ? { ...t, ...patch } : t);
    onDbSchemaChange({ tables });
  };

  const updateColumn = (tableIdx: number, colIdx: number, patch: Partial<ExistingColumn>) => {
    const table = dbSchema.tables[tableIdx];
    const columns = table.columns.map((c, i) => i === colIdx ? { ...c, ...patch } : c);
    updateTable(tableIdx, { columns });
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <TextField
        label="System Prompt"
        multiline minRows={6} fullWidth
        value={systemPrompt}
        onChange={e => onSystemPromptChange(e.target.value)}
      />

      <Divider />

      {dbSchema.tables.map((table, ti) => (
        <Accordion key={ti} defaultExpanded>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography>{table.table_name || `テーブル ${ti + 1}`}</Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="説明" size="small" value={table.description}
                onChange={e => updateTable(ti, { description: e.target.value })} fullWidth
              />

              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell width="20%">カラム名</TableCell>
                    <TableCell width="20%">型</TableCell>
                    <TableCell width="60%">説明</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {table.columns.map((col, ci) => (
                    <TableRow key={ci}>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{col.name}</Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{col.type}</Typography>
                      </TableCell>
                      <TableCell>
                        <TextField size="small" variant="standard" value={col.description}
                          onChange={e => updateColumn(ti, ci, { description: e.target.value })} fullWidth />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          </AccordionDetails>
        </Accordion>
      ))}
    </Box>
  );
}
