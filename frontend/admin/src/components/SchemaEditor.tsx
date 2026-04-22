import {
  Box, Typography, TextField, Accordion, AccordionSummary, AccordionDetails,
  Table, TableHead, TableBody, TableRow, TableCell, Divider, Chip,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

export interface CsvOptions {
  delimiter: string;
  quote_char: string;
  null_as: string;
}

export interface Column {
  name: string;
  type: string;
  description: string;
}

export interface TableSchema {
  table_name: string;
  description: string;
  s3_keys: string[];
  csv_options: CsvOptions;
  columns: Column[];
}

export interface DbSchema {
  tables: TableSchema[];
}

interface SchemaEditorProps {
  systemPrompt: string;
  onSystemPromptChange: (v: string) => void;
  dbSchema: DbSchema;
  onDbSchemaChange: (v: DbSchema) => void;
}

export default function SchemaEditor({ systemPrompt, onSystemPromptChange, dbSchema, onDbSchemaChange }: SchemaEditorProps) {

  const updateTable = (tableIdx: number, patch: Partial<TableSchema>) => {
    const tables = dbSchema.tables.map((t, i) => i === tableIdx ? { ...t, ...patch } : t);
    onDbSchemaChange({ tables });
  };

  const updateCsvOption = (tableIdx: number, field: keyof CsvOptions, value: string) => {
    const table = dbSchema.tables[tableIdx];
    updateTable(tableIdx, { csv_options: { ...table.csv_options, [field]: value } });
  };

  const updateColumn = (tableIdx: number, colIdx: number, patch: Partial<Column>) => {
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
              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField label="テーブル名" size="small" value={table.table_name}
                  onChange={e => updateTable(ti, { table_name: e.target.value })} sx={{ flex: 1 }} />
                <TextField label="説明" size="small" value={table.description}
                  onChange={e => updateTable(ti, { description: e.target.value })} sx={{ flex: 2 }} />
              </Box>

              <Typography variant="body2" color="text.secondary">
                S3: {table.s3_keys.length} ファイル
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {table.s3_keys.map((key, ki) => (
                  <Chip key={ki} label={key.split('/').pop() ?? key} size="small" variant="outlined" />
                ))}
              </Box>

              <Box sx={{ display: 'flex', gap: 1 }}>
                <TextField label="delimiter" size="small" value={table.csv_options.delimiter}
                  onChange={e => updateCsvOption(ti, 'delimiter', e.target.value)} sx={{ flex: 1 }} />
                <TextField label="quote_char" size="small" value={table.csv_options.quote_char}
                  onChange={e => updateCsvOption(ti, 'quote_char', e.target.value)} sx={{ flex: 1 }} />
                <TextField label="null_as" size="small" value={table.csv_options.null_as}
                  onChange={e => updateCsvOption(ti, 'null_as', e.target.value)} sx={{ flex: 1 }} />
              </Box>

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
                        <TextField size="small" variant="standard" value={col.name}
                          onChange={e => updateColumn(ti, ci, { name: e.target.value })} fullWidth />
                      </TableCell>
                      <TableCell>
                        <TextField size="small" variant="standard" value={col.type}
                          onChange={e => updateColumn(ti, ci, { type: e.target.value })} fullWidth />
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
