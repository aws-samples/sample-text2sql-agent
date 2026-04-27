import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Box, Typography, RadioGroup, FormControlLabel, Radio,
  Button, TextField, Alert, CircularProgress, Backdrop,
  List, ListItem, ListItemIcon, ListItemText,
  Stepper, Step, StepLabel, Chip,
} from '@mui/material';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import SchemaEditor, { DbSchema } from './SchemaEditor';
import * as api from '../api';

const STEPS = ['CSV 指定', 'AI 分析', '確認・編集', 'テーブル構築'];

export default function UploadAndBuild() {
  const [activeStep, setActiveStep] = useState(0);
  const [mode, setMode] = useState<'upload' | 's3'>('upload');

  // Mode A state
  const [files, setFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadedFiles, setUploadedFiles] = useState<Set<string>>(new Set());

  // Mode B state
  const [s3Prefix, setS3Prefix] = useState('');
  const [listedFiles, setListedFiles] = useState<string[] | null>(null);
  const [listedManifests, setListedManifests] = useState<string[] | null>(null);

  // Shared
  const [prefix, setPrefix] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState('');

  // Step 2: Analyze
  const [analyzeResult, setAnalyzeResult] = useState<{ system_prompt: string; db_schema: any } | null>(null);

  // Step 3: Edit
  const [editSystemPrompt, setEditSystemPrompt] = useState('');
  const [editDbSchema, setEditDbSchema] = useState<DbSchema>({ tables: [] });
  const [editAgentName, setEditAgentName] = useState('Default Agent');

  // Step 4: Apply
  const [applyResult, setApplyResult] = useState<{ status: string; tables_created: string[]; errors?: string[]; load_error_details?: api.LoadErrorDetail[] } | null>(null);
  const applyingRef = useRef(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Mode A: Local file upload ----
  const handleFileSelect = () => fileInputRef.current?.click();

  const handleFilesChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(e.target.files ?? []);
    setFiles(chosen);
    setError(null);
  };

  const handleUpload = async () => {
    if (files.length === 0) return;
    setLoading(true);
    setLoadingMessage('CSV をアップロード中...');
    setError(null);
    setUploadedFiles(new Set());
    try {
      const filenames = files.map(f => f.name);
      const { prefix: p, urls } = await api.getPresignedUrls(filenames);
      await Promise.all(files.map(async (file) => {
        const res = await fetch(urls[file.name], {
          method: 'PUT',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: file,
        });
        if (!res.ok) throw new Error(`Upload failed: ${file.name} (${res.status})`);
        setUploadedFiles(prev => new Set(prev).add(file.name));
      }));
      setPrefix(p);
      setActiveStep(1);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  // ---- Mode B: Existing S3 prefix ----
  const handleListCsv = async () => {
    setLoading(true);
    setLoadingMessage('ファイル一覧を取得中...');
    setError(null);
    setListedFiles(null);
    setListedManifests(null);
    try {
      const inputPrefix = s3Prefix.trim();
      const { files: csvFiles, manifests } = await api.listCsv(inputPrefix);
      if (csvFiles.length === 0 && manifests.length === 0) {
        setError('指定された prefix 配下に CSV / manifest ファイルが見つかりません');
        return;
      }
      setListedFiles(csvFiles);
      setListedManifests(manifests);
      // ユーザー入力をそのまま保持（バックエンドの正規化結果は捨てる）
      setPrefix(inputPrefix);
      setActiveStep(1);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  // ---- Step 2: Analyze ----
  const handleAnalyze = async () => {
    if (prefix === null) return;
    setLoading(true);
    setLoadingMessage('CSV を AI 分析中です。しばらくお待ちください...');
    setError(null);
    setAnalyzeResult(null);
    setApplyResult(null);
    try {
      const result = await api.analyze(prefix, (progress) => {
        if (progress.step === 'analyze_csv') {
          setLoadingMessage(`AI 分析中... (${progress.current}/${progress.total}) ${progress.file}`);
        } else if (progress.step === 'generate_prompt') {
          setLoadingMessage('システムプロンプトを生成中...');
        }
      });
      setAnalyzeResult(result);
      setEditSystemPrompt(result.system_prompt);
      setEditDbSchema(result.db_schema);
      setActiveStep(2);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  // cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  // ---- Step 4: Apply (async with polling) ----
  const handleApply = useCallback(async () => {
    if (applyingRef.current || prefix === null) return;
    applyingRef.current = true;
    setLoading(true);
    setLoadingMessage('データベース構築とデータロード中...');
    setError(null);
    setApplyResult(null);

    try {
      const { execution_id } = await api.apply(prefix, editSystemPrompt, editDbSchema, editAgentName);

      pollingRef.current = setInterval(async () => {
        try {
          const result = await api.getApplyStatus(execution_id);
          if (result.status !== 'running') {
            if (pollingRef.current) clearInterval(pollingRef.current);
            pollingRef.current = null;
            setApplyResult({
              status: result.status,
              tables_created: result.tables_created ?? [],
              errors: result.errors,
              load_error_details: result.load_error_details,
            });
            setActiveStep(result.status === 'completed' ? 4 : 3);
            setLoading(false);
            setLoadingMessage('');
            applyingRef.current = false;
          }
        } catch (e: any) {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setError(e.message);
          setLoading(false);
          setLoadingMessage('');
          applyingRef.current = false;
        }
      }, 3000);
    } catch (e: any) {
      setError(e.message);
      setLoading(false);
      setLoadingMessage('');
      applyingRef.current = false;
    }
  }, [prefix, editSystemPrompt, editDbSchema, editAgentName]);

  // ---- Step summaries for completed steps ----
  const step1Summary = prefix !== null
    ? mode === 'upload'
      ? `${files.length} ファイルアップロード済み (${prefix})`
      : `S3: ${prefix} (${listedFiles?.length ?? 0} CSV`
        + (listedManifests && listedManifests.length > 0 ? `, ${listedManifests.length} manifest` : '')
        + ')'
    : null;

  const step2Summary = analyzeResult
    ? `${analyzeResult.db_schema.tables?.length ?? 0} テーブル検出`
    : null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Full-screen loading overlay */}
      <Backdrop open={loading} sx={{ color: '#fff', zIndex: (theme) => theme.zIndex.modal + 1, flexDirection: 'column', gap: 2 }}>
        <CircularProgress color="inherit" />
        <Typography variant="h6">{loadingMessage}</Typography>
      </Backdrop>

      {/* Stepper */}
      <Stepper activeStep={activeStep} alternativeLabel>
        {STEPS.map((label) => (
          <Step key={label}>
            <StepLabel>{label}</StepLabel>
          </Step>
        ))}
      </Stepper>

      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {/* Step 1: CSV 指定 */}
      {activeStep === 0 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <RadioGroup row value={mode} onChange={(_, v) => { setMode(v as any); setError(null); }}>
            <FormControlLabel value="upload" control={<Radio />} label="ローカルファイルアップロード" />
            <FormControlLabel value="s3" control={<Radio />} label="既存 S3 パス指定" />
          </RadioGroup>

          {mode === 'upload' && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <input ref={fileInputRef} type="file" accept=".csv" multiple hidden onChange={handleFilesChosen} />
              <Button variant="outlined" onClick={handleFileSelect}>CSV ファイルを選択</Button>
              <Typography variant="caption" color="text.secondary">
                manifest を使った大量 CSV の一括ロードには、このローカルアップロードではなく「既存 S3 パス指定」を利用してください（ローカルアップロードではディレクトリ構造と URL が保持されず manifest が機能しません）。
              </Typography>
              {files.length > 0 && (
                <List dense>
                  {files.map(f => (
                    <ListItem key={f.name}>
                      <ListItemIcon><InsertDriveFileIcon /></ListItemIcon>
                      <ListItemText
                        primary={f.name}
                        secondary={`${(f.size / 1024).toFixed(1)} KB`}
                      />
                      {uploadedFiles.has(f.name) && <CheckCircleIcon color="success" sx={{ ml: 1 }} />}
                    </ListItem>
                  ))}
                </List>
              )}
              <Button variant="contained" onClick={handleUpload} disabled={files.length === 0}>
                アップロード
              </Button>
            </Box>
          )}

          {mode === 's3' && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="S3 Prefix" placeholder="バケット内のパスのみ入力（例: testdata/）"
                value={s3Prefix} onChange={e => setS3Prefix(e.target.value)}
                helperText="CSV バケット内のフォルダパスを入力してください。バケット名や s3:// は不要です。サブディレクトリ配下の CSV も自動で再帰的に列挙されます。同プレフィックス配下に <任意名>.manifest を置くと、そのテーブルは COPY 1 回にまとめられます。"
              />
              <Button variant="contained" onClick={handleListCsv} disabled={!s3Prefix.trim()}>
                ファイル一覧取得
              </Button>
              {listedManifests && listedManifests.length > 0 && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mt: 1 }}>
                    Manifest ファイル ({listedManifests.length})
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    各 manifest は 1 テーブルとして扱われ、entries に列挙された CSV を COPY 1 回でまとめてロードします。
                  </Typography>
                  <List dense>
                    {listedManifests.map(f => (
                      <ListItem key={`m-${f}`}>
                        <ListItemIcon><InsertDriveFileIcon /></ListItemIcon>
                        <ListItemText
                          primary={
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <span>{f}</span>
                              <Chip label="MANIFEST" size="small" color="primary" variant="outlined" />
                            </Box>
                          }
                        />
                      </ListItem>
                    ))}
                  </List>
                </Box>
              )}
              {listedFiles && listedFiles.length > 0 && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mt: 1 }}>
                    CSV ファイル ({listedFiles.length})
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    manifest に含まれている CSV は除外されます。残った CSV はヘッダが一致するものごとにテーブル化されます。
                  </Typography>
                  <List dense>
                    {listedFiles.map(f => (
                      <ListItem key={`c-${f}`}>
                        <ListItemIcon><InsertDriveFileIcon /></ListItemIcon>
                        <ListItemText primary={f} />
                      </ListItem>
                    ))}
                  </List>
                </Box>
              )}
            </Box>
          )}
        </Box>
      )}

      {/* Step 1 summary (when past step 1) */}
      {activeStep >= 1 && step1Summary && (
        <Alert severity="success" icon={<CheckCircleIcon />}>{step1Summary}</Alert>
      )}

      {/* Step 2: AI 分析 */}
      {activeStep === 1 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Button variant="contained" color="secondary" onClick={handleAnalyze} size="large">
            AI 分析を開始
          </Button>
        </Box>
      )}

      {/* Step 2 summary (when past step 2) */}
      {activeStep >= 2 && step2Summary && (
        <Alert severity="success" icon={<CheckCircleIcon />}>{step2Summary}</Alert>
      )}

      {/* Step 3: 確認・編集 + Step 4 ボタン */}
      {activeStep >= 2 && activeStep < 4 && !applyResult && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Alert severity="warning">
            テーブル構築を実行すると、既存の全 Agent が削除され、新しい Agent が 1 つ作成されます。
          </Alert>
          <TextField
            label="Agent 名"
            value={editAgentName}
            onChange={e => setEditAgentName(e.target.value)}
            helperText="構築後に作成される Agent の名前"
          />
          <SchemaEditor
            systemPrompt={editSystemPrompt}
            onSystemPromptChange={setEditSystemPrompt}
            dbSchema={editDbSchema}
            onDbSchemaChange={setEditDbSchema}
          />
          <Button variant="contained" color="warning" size="large" onClick={handleApply} sx={{ mt: 2 }}>
            確定（テーブル作成）
          </Button>
        </Box>
      )}

      {/* Step 4: 結果 */}
      {applyResult && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Alert severity={applyResult.status === 'completed' ? 'success' : 'error'}>
            {applyResult.status === 'completed' ? '確定完了' : 'エラーが発生しました'}
            {applyResult.tables_created.length > 0 && ` — 作成テーブル: ${applyResult.tables_created.join(', ')}`}
          </Alert>
          {applyResult.errors && applyResult.errors.length > 0 && (
            <Alert severity="error">
              {applyResult.errors.map((e, i) => <div key={i}>{e}</div>)}
            </Alert>
          )}
          {applyResult.load_error_details && applyResult.load_error_details.length > 0 && (() => {
            // 重複排除（column_name + line_number）
            const seen = new Set<string>();
            const unique = applyResult.load_error_details.filter(d => {
              const key = `${d.column_name}:${d.line_number}`;
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            });
            return (
              <Alert severity="warning">
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  CSV データの読み込みに失敗した箇所があります。データ型の不一致や値の形式が正しくない可能性があります。
                  CSV データまたはスキーマ定義を確認してください。
                </Typography>
                {unique.map((d, i) => (
                  <div key={i}>
                    {d.line_number} 行目, カラム「{d.column_name}」({d.column_type})
                  </div>
                ))}
              </Alert>
            );
          })()}
        </Box>
      )}
    </Box>
  );
}
