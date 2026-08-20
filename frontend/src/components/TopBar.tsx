import {
  CheckCircle2,
  Download,
  FlaskConical,
  ListFilter,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  RotateCw,
  Save,
  ScanLine,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import type { RefObject } from 'react';
import type { ScoresheetDocument, ValidationReport } from '../types';

interface TopBarProps {
  document: ScoresheetDocument;
  validation: ValidationReport | null;
  saveState: 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error';
  canUndo: boolean;
  canRedo: boolean;
  recognitionMode: string;
  recognitionState: 'idle' | 'starting' | 'running' | 'diff' | 'applied' | 'failed';
  onUpload: (file: File) => Promise<void>;
  onChooseGame: () => void;
  onRecognize: () => Promise<void>;
  onSynthetic: () => Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  onSave: () => Promise<void>;
  onValidate: () => Promise<unknown>;
  onConfirm: () => Promise<void>;
  sourceOpen: boolean;
  inspectorOpen: boolean;
  onToggleSource: () => void;
  onToggleInspector: () => void;
  uploadInputRef: RefObject<HTMLInputElement | null>;
}

const saveLabels = {
  idle: '未保存',
  dirty: '等待保存',
  saving: '正在保存',
  saved: '已保存',
  conflict: '保存冲突',
  error: '保存失败',
};

export function TopBar({
  document,
  validation,
  saveState,
  canUndo,
  canRedo,
  recognitionMode,
  recognitionState,
  onUpload,
  onChooseGame,
  onRecognize,
  onSynthetic,
  onUndo,
  onRedo,
  onSave,
  onValidate,
  onConfirm,
  sourceOpen,
  inspectorOpen,
  onToggleSource,
  onToggleInspector,
  uploadInputRef,
}: TopBarProps) {
  const persisted = document.id !== 'synthetic-preview';
  const recognitionActive = recognitionState === 'starting' || recognitionState === 'running';
  const canRecognize = persisted
    && Boolean(document.game_prior)
    && Boolean(document.source.original_url)
    && document.status !== 'confirmed'
    && !recognitionActive;
  const statusLabel = {
    draft: '草稿',
    needs_review: '待人工核对',
    validated: '已校验',
    confirmed: '已提交',
  }[document.status];

  return (
    <header className="topbar">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <span />
          <span />
        </div>
        <div>
          <strong>ScoresheetReader</strong>
          <span>语义记录表工作台</span>
        </div>
      </div>

      <div className="document-state">
        <span className={`state-dot ${document.status}`} />
        <div>
          <strong>{document.header.competition || '未命名比赛'}</strong>
          <span>v{document.revision} · {statusLabel}</span>
        </div>
      </div>

      <div className="topbar-actions">
        <div className="topbar-action-group panel-controls" aria-label="面板显示">
          <button className={sourceOpen ? 'icon-button is-active' : 'icon-button'} onClick={onToggleSource} title={sourceOpen ? '收起原图面板' : '展开原图面板'} aria-label={sourceOpen ? '收起原图面板' : '展开原图面板'}>
            {sourceOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>
          <button className={inspectorOpen ? 'icon-button is-active' : 'icon-button'} onClick={onToggleInspector} title={inspectorOpen ? '收起编辑面板' : '展开编辑面板'} aria-label={inspectorOpen ? '收起编辑面板' : '展开编辑面板'}>
            {inspectorOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
          </button>
        </div>
        <div className="topbar-status-cluster">
          <span className="offline-badge"><ShieldCheck size={14} /> 本机 · {recognitionMode === 'mock' ? 'Mock 识别' : 'Qwen 按需'}</span>
          <span className={`save-indicator ${saveState}`}><Save size={14} /> {saveLabels[saveState]}</span>
        </div>
        <input
          ref={uploadInputRef}
          className="visually-hidden"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void onUpload(file);
            event.target.value = '';
          }}
        />
        <div className="topbar-action-group">
          <button className="topbar-button" onClick={onChooseGame}>
            <ListFilter size={15} /> 选择比赛
          </button>
          <button className="topbar-button" onClick={() => uploadInputRef.current?.click()} title="创建不带比赛先验的空白草稿">
            <Upload size={15} /> 直接上传
          </button>
          <button className="topbar-button" onClick={() => void onSynthetic()}>
            <FlaskConical size={15} /> 合成样表
          </button>
        </div>
        <div className="topbar-action-group recognition-workflow">
          <button
            className="topbar-button recognition-button"
            onClick={() => void onRecognize()}
            disabled={!canRecognize}
            title={!document.game_prior ? '请先选择比赛并上传照片' : undefined}
          >
            <ScanLine size={15} className={recognitionActive ? 'pulse-icon' : undefined} />
            {recognitionActive ? '识别中…' : document.recognition ? '重新识别' : '整图识别'}
          </button>
        </div>
        <div className="topbar-action-group">
          <button className="icon-button" onClick={onUndo} disabled={!canUndo} title="撤销 Ctrl+Z" aria-label="撤销">
            <RotateCcw size={16} />
          </button>
          <button className="icon-button" onClick={onRedo} disabled={!canRedo} title="重做 Ctrl+Shift+Z" aria-label="重做">
            <RotateCw size={16} />
          </button>
        </div>
        <div className="topbar-action-group primary-workflow">
          <button
            className="topbar-button"
            onClick={() => void onSave()}
            disabled={!persisted || saveState === 'saving' || saveState === 'saved'}
            title="立即保存当前草稿（Ctrl+S）"
          >
            <Save size={15} /> 保存草稿
          </button>
          <button className="topbar-button" onClick={() => void onValidate()}>
            <CheckCircle2 size={15} /> 校验
            {validation?.issues.length ? <b className="issue-count">{validation.issues.length}</b> : null}
          </button>
          {persisted ? (
            <a className="topbar-button" href={`/api/v1/documents/${document.id}/render.pdf`} target="_blank" rel="noreferrer">
              <Download size={15} /> 导出 PDF
            </a>
          ) : (
            <button className="topbar-button" disabled title="先点击“合成样表”保存为本地文档">
              <Download size={15} /> 导出 PDF
            </button>
          )}
          <button className="confirm-button" onClick={() => void onConfirm()} disabled={!persisted || document.status === 'confirmed'}>
            <ShieldCheck size={15} /> {document.status === 'confirmed' ? '已提交' : '提交记录表'}
          </button>
        </div>
      </div>
    </header>
  );
}
