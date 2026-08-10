import { useState, useEffect, useMemo } from 'react';
import {
  Box, Typography, Button, TextField, Alert, CircularProgress, Backdrop,
  Stepper, Step, StepLabel, Checkbox, Chip,
  Table, TableHead, TableBody, TableRow, TableCell,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ExistingSchemaEditor, { ExistingDbSchema } from './ExistingSchemaEditor';
import * as api from '../api';

const STEPS = ['テーブル選択', 'AI 分析', '確認・編集', 'Agent 作成'];

/**
 * existingRedshift モード用: 既存 Redshift テーブルからスキーマを自動生成し、
 * Agent が使うテーブルを選択して Agent を作成するフロー。
 *
 * 注意: ここでの選択はエージェントのプロンプトに載せるテーブルを絞る
 * ソフトな制御であり、DB レベルのアクセス制御は読み取り専用ユーザーの
 * GRANT (リポジトリ外で管理) が担う。
 */
export default function ExistingTablesBuild() {
  const [activeStep, setActiveStep] = useState(0);

  // Step 1: テーブル選択
  const [tables, setTables] = useState<api.RedshiftTable[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Step 2-3: 生成結果と編集
  const [editSystemPrompt, setEditSystemPrompt] = useState('');
  const [editDbSchema, setEditDbSchema] = useState<ExistingDbSchema>({ tables: [] });
  const [editAgentName, setEditAgentName] = useState('Redshift Agent');
  const [warnings, setWarnings] = useState<string[]>([]);

  // Step 4: 保存結果
  const [applyResult, setApplyResult] = useState<{ agent_id: string } | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState('');

  const tableKey = (t: api.RedshiftTable) => `${t.schema}.${t.name}`;

  useEffect(() => {
    (async () => {
      setLoading(true);
      setLoadingMessage('テーブル一覧を取得中...');
      try {
        const { tables: listed } = await api.listRedshiftTables();
        setTables(listed);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
        setLoadingMessage('');
      }
    })();
  }, []);

  const toggleSelect = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectedTables = useMemo(
    () => (tables ?? []).filter(t => selected.has(tableKey(t))),
    [tables, selected],
  );

  // ---- Step 2: AI 分析 ----
  const handleGenerate = async () => {
    setLoading(true);
    setLoadingMessage('既存テーブルを AI 分析中です。しばらくお待ちください...');
    setError(null);
    setWarnings([]);
    try {
      const result = await api.generateSchemaFromRedshift(
        selectedTables.map(t => ({ schema_name: t.schema, table_name: t.name })),
        (progress) => {
          if (progress.step === 'describe_table') {
            setLoadingMessage(`AI 分析中... (${progress.current}/${progress.total}) ${progress.file}`);
          } else if (progress.step === 'generate_prompt') {
            setLoadingMessage('システムプロンプトを生成中...');
          }
        },
      );
      setEditSystemPrompt(result.system_prompt);
      setEditDbSchema(result.db_schema);
      setWarnings(result.warnings ?? []);
      setActiveStep(2);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  // ---- Step 4: Agent 作成 ----
  const handleApply = async () => {
    setLoading(true);
    setLoadingMessage('Agent を作成中...');
    setError(null);
    try {
      const result = await api.applyExistingSchema(editAgentName, editSystemPrompt, editDbSchema);
      setApplyResult({ agent_id: result.agent_id });
      setActiveStep(4);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Backdrop open={loading} sx={{ color: '#fff', zIndex: (theme) => theme.zIndex.modal + 1, flexDirection: 'column', gap: 2 }}>
        <CircularProgress color="inherit" />
        <Typography variant="h6">{loadingMessage}</Typography>
      </Backdrop>

      <Stepper activeStep={activeStep} alternativeLabel>
        {STEPS.map((label) => (
          <Step key={label}>
            <StepLabel>{label}</StepLabel>
          </Step>
        ))}
      </Stepper>

      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {/* Step 1: テーブル選択 */}
      {activeStep === 0 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Typography variant="body2" color="text.secondary">
            Agent に使わせるテーブルを選択してください。選択したテーブルのスキーマ情報が
            AI により分析され、Agent のプロンプトに組み込まれます。
          </Typography>
          {tables && tables.length === 0 && (
            <Alert severity="warning">
              アクセス可能なテーブルが見つかりません。読み取り専用ユーザーに対象テーブルへの
              SELECT 権限 (GRANT) が付与されているか確認してください。
            </Alert>
          )}
          {tables && tables.length > 0 && (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox" />
                  <TableCell>スキーマ</TableCell>
                  <TableCell>テーブル名</TableCell>
                  <TableCell>種別</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {tables.map(t => {
                  const key = tableKey(t);
                  return (
                    <TableRow key={key} hover onClick={() => toggleSelect(key)} sx={{ cursor: 'pointer' }}>
                      <TableCell padding="checkbox">
                        <Checkbox checked={selected.has(key)} />
                      </TableCell>
                      <TableCell>{t.schema}</TableCell>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{t.name}</TableCell>
                      <TableCell>
                        <Chip label={t.type} size="small" variant="outlined"
                          color={t.type === 'VIEW' ? 'secondary' : 'primary'} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
          <Button
            variant="contained" disabled={selected.size === 0}
            onClick={() => setActiveStep(1)}
          >
            選択したテーブルで次へ ({selected.size})
          </Button>
        </Box>
      )}

      {/* Step 1 summary */}
      {activeStep >= 1 && (
        <Alert severity="success" icon={<CheckCircleIcon />}>
          {selectedTables.map(t => tableKey(t)).join(', ')}
        </Alert>
      )}

      {/* Step 2: AI 分析 */}
      {activeStep === 1 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Typography variant="body2" color="text.secondary">
            選択したテーブルのカラム定義とサンプルデータ (先頭20行) を AI が分析し、
            テーブル・カラムの説明とシステムプロンプトを生成します。
          </Typography>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <Button variant="outlined" onClick={() => setActiveStep(0)}>戻る</Button>
            <Button variant="contained" color="secondary" onClick={handleGenerate} size="large">
              AI 分析を開始
            </Button>
          </Box>
        </Box>
      )}

      {warnings.length > 0 && (
        <Alert severity="warning">
          {warnings.map((w, i) => <div key={i}>{w}</div>)}
        </Alert>
      )}

      {/* Step 3: 確認・編集 */}
      {activeStep >= 2 && !applyResult && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Agent 名"
            value={editAgentName}
            onChange={e => setEditAgentName(e.target.value)}
            helperText="作成される Agent の名前"
          />
          <ExistingSchemaEditor
            systemPrompt={editSystemPrompt}
            onSystemPromptChange={setEditSystemPrompt}
            dbSchema={editDbSchema}
            onDbSchemaChange={setEditDbSchema}
          />
          <Button variant="contained" size="large" onClick={handleApply} sx={{ mt: 2 }}
            disabled={!editAgentName.trim()}>
            Agent を作成
          </Button>
        </Box>
      )}

      {/* Step 4: 結果 */}
      {applyResult && (
        <Alert severity="success">
          Agent を作成しました。Agent UI からチャットを開始できます。
          プロンプトやナレッジは「Prompt & Knowledge」タブから編集できます。
        </Alert>
      )}
    </Box>
  );
}
