import { create } from 'zustand';
import { api } from './api';
import type {
  DocumentRevision,
  GameSummary,
  RecognitionDiff,
  RecognitionRun,
  ScoresheetDocument,
  TemplateDefinition,
  ValidationReport,
} from './types';
import { deepCloneDocument } from './types';
import { validateLocal } from './lib/validation';

const LAST_DOCUMENT_KEY = 'scoresheet-reader:last-document-id';
const LOCAL_DRAFT_KEY = 'scoresheet-reader:synthetic-draft';
const RECOGNITION_POLL_INTERVAL_MS = 500;
const RECOGNITION_POLL_LIMIT = 360;

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

async function loadRecognitionRun(
  document: ScoresheetDocument,
): Promise<RecognitionRun | null> {
  if (!document.recognition?.run_id) return null;
  try {
    return await api.recognition(document.recognition.run_id);
  } catch {
    return null;
  }
}

function rebaseSnapshot(document: ScoresheetDocument, revision: number): ScoresheetDocument {
  const snapshot = deepCloneDocument(document);
  snapshot.revision = revision;
  snapshot.schema_version = '1.4.0';
  return snapshot;
}

interface EditorState {
  document: ScoresheetDocument | null;
  serverRevision: number;
  template: TemplateDefinition | null;
  games: GameSummary[];
  gamesLoading: boolean;
  recognitionMode: string;
  validation: ValidationReport | null;
  recognitionRun: RecognitionRun | null;
  recognitionDiff: RecognitionDiff | null;
  recognitionState: 'idle' | 'starting' | 'running' | 'diff' | 'applied' | 'failed';
  revisions: DocumentRevision[];
  selectedField: string;
  past: ScoresheetDocument[];
  future: ScoresheetDocument[];
  dirty: boolean;
  saveState: 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error';
  loading: boolean;
  error: string;
  initialize: () => Promise<void>;
  loadSynthetic: () => Promise<void>;
  loadGames: () => Promise<void>;
  openDocument: (documentId: string) => Promise<void>;
  upload: (file: File) => Promise<void>;
  uploadForGame: (gameId: string, file: File) => Promise<void>;
  recognize: () => Promise<void>;
  applyRecognition: (regions: string[]) => Promise<void>;
  clearRecognitionDiff: () => void;
  selectField: (field: string) => void;
  mutate: (mutation: (draft: ScoresheetDocument) => void) => void;
  replaceDocument: (document: ScoresheetDocument, remember?: boolean) => void;
  undo: () => void;
  redo: () => void;
  save: (source?: 'human' | 'undo' | 'redo' | 'system') => Promise<void>;
  validate: () => Promise<ValidationReport | null>;
  confirm: () => Promise<void>;
  align: (rotation: 0 | 90 | 180 | 270, corners: number[][] | null) => Promise<void>;
  refreshRevisions: () => Promise<void>;
}

export const useEditorStore = create<EditorState>((set, get) => ({
  document: null,
  serverRevision: 0,
  template: null,
  games: [],
  gamesLoading: false,
  recognitionMode: 'on_demand',
  validation: null,
  recognitionRun: null,
  recognitionDiff: null,
  recognitionState: 'idle',
  revisions: [],
  selectedField: 'document',
  past: [],
  future: [],
  dirty: false,
  saveState: 'idle',
  loading: true,
  error: '',

  initialize: async () => {
    set({ loading: true, error: '' });
    try {
      const [template, games, health] = await Promise.all([
        api.template(),
        api.games().catch(() => [] as GameSummary[]),
        api.health().catch(() => ({ status: 'ok', recognition: 'on_demand', master_data: 'empty' })),
      ]);
      const lastId = localStorage.getItem(LAST_DOCUMENT_KEY);
      let document: ScoresheetDocument;
      if (lastId) {
        try {
          document = await api.document(lastId);
        } catch {
          localStorage.removeItem(LAST_DOCUMENT_KEY);
          document = await api.synthetic();
        }
      } else {
        const localDraft = localStorage.getItem(LOCAL_DRAFT_KEY);
        document = localDraft ? JSON.parse(localDraft) : await api.synthetic();
      }
      const recognitionRun = await loadRecognitionRun(document);
      set({
        template,
        games,
        recognitionMode: health.recognition,
        document,
        recognitionRun,
        recognitionState: document.recognition ? 'applied' : 'idle',
        serverRevision: document.revision,
        loading: false,
        saveState: 'saved',
      });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : '加载失败' });
    }
  },

  loadSynthetic: async () => {
    const document = await api.createSynthetic();
    localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
    localStorage.removeItem(LOCAL_DRAFT_KEY);
    set({
      document,
      serverRevision: document.revision,
      validation: null,
      recognitionRun: null,
      recognitionDiff: null,
      recognitionState: 'idle',
      revisions: [],
      selectedField: 'document',
      past: [],
      future: [],
      dirty: false,
      saveState: 'saved',
    });
    await get().refreshRevisions();
  },

  loadGames: async () => {
    set({ gamesLoading: true });
    try {
      set({ games: await api.games(), gamesLoading: false });
    } catch (error) {
      set({
        gamesLoading: false,
        error: error instanceof Error ? error.message : '比赛列表加载失败',
      });
    }
  },

  openDocument: async (documentId) => {
    set({ loading: true, error: '' });
    try {
      const document = await api.document(documentId);
      const recognitionRun = await loadRecognitionRun(document);
      localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
      localStorage.removeItem(LOCAL_DRAFT_KEY);
      set({
        document,
        serverRevision: document.revision,
        validation: null,
        recognitionRun,
        recognitionDiff: null,
        recognitionState: document.recognition ? 'applied' : 'idle',
        revisions: [],
        selectedField: 'document',
        past: [],
        future: [],
        dirty: false,
        saveState: 'saved',
        loading: false,
      });
      await get().refreshRevisions();
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : '打开记录表失败',
      });
      throw error;
    }
  },

  upload: async (file) => {
    set({ loading: true, error: '' });
    try {
      const document = await api.createDocument(file);
      localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
      set({
        document,
        serverRevision: document.revision,
        validation: null,
        recognitionRun: null,
        recognitionDiff: null,
        recognitionState: 'idle',
        revisions: [],
        selectedField: 'header',
        past: [],
        future: [],
        dirty: false,
        saveState: 'saved',
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : '上传失败' });
    }
  },

  uploadForGame: async (gameId, file) => {
    set({ loading: true, error: '' });
    try {
      const document = await api.createGameDocument(gameId, file);
      localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
      set({
        document,
        serverRevision: document.revision,
        validation: null,
        revisions: [],
        recognitionRun: null,
        recognitionDiff: null,
        recognitionState: 'idle',
        selectedField: 'header',
        past: [],
        future: [],
        dirty: false,
        saveState: 'saved',
        loading: false,
      });
      await Promise.all([get().refreshRevisions(), get().loadGames()]);
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : '上传失败' });
    }
  },

  selectField: (selectedField) => set({ selectedField }),

  mutate: (mutation) => {
    const { document: current, serverRevision } = get();
    if (!current) return;
    const previous = deepCloneDocument(current);
    const next = rebaseSnapshot(current, serverRevision);
    mutation(next);
    next.status = next.recognition ? 'needs_review' : 'draft';
    set((state) => ({
      document: next,
      past: [...state.past.slice(-49), previous],
      future: [],
      dirty: true,
      saveState: 'dirty',
      validation: null,
    }));
  },

  replaceDocument: (document, remember = false) => {
    if (remember) localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
    set({
      document,
      serverRevision: document.revision,
      dirty: false,
      saveState: 'saved',
    });
  },

  undo: () => {
    const { past, document, serverRevision } = get();
    if (!document || past.length === 0) return;
    const next = rebaseSnapshot(past[past.length - 1], serverRevision);
    set((state) => ({
      document: next,
      past: state.past.slice(0, -1),
      future: [deepCloneDocument(document), ...state.future.slice(0, 49)],
      dirty: true,
      saveState: 'dirty',
      validation: null,
    }));
  },

  redo: () => {
    const { future, document, serverRevision } = get();
    if (!document || future.length === 0) return;
    const next = rebaseSnapshot(future[0], serverRevision);
    set((state) => ({
      document: next,
      past: [...state.past.slice(-49), deepCloneDocument(document)],
      future: state.future.slice(1),
      dirty: true,
      saveState: 'dirty',
      validation: null,
    }));
  },

  save: async (source = 'human') => {
    const { document, serverRevision } = get();
    if (!document || !get().dirty) return;
    if (document.id === 'synthetic-preview') {
      localStorage.setItem(LOCAL_DRAFT_KEY, JSON.stringify(document));
      set({ dirty: false, saveState: 'saved' });
      return;
    }
    const candidate = rebaseSnapshot(document, serverRevision);
    set({ saveState: 'saving' });
    try {
      const saved = await api.save(candidate, serverRevision, source);
      set({
        document: saved,
        serverRevision: saved.revision,
        dirty: false,
        saveState: 'saved',
      });
    } catch (error) {
      const status = (error as Error & { status?: number }).status;
      set({
        saveState: status === 409 ? 'conflict' : 'error',
        error: error instanceof Error ? error.message : '保存失败',
      });
    }
  },

  validate: async () => {
    let document = get().document;
    if (!document) return null;
    if (document.id === 'synthetic-preview') {
      const report = validateLocal(document);
      set({ validation: report });
      return report;
    }
    if (get().dirty) {
      await get().save();
      if (get().dirty || ['conflict', 'error'].includes(get().saveState)) {
        set({ error: '草稿尚未成功保存，已停止校验和提交。' });
        return null;
      }
      document = get().document;
      if (!document) return null;
    }
    try {
      const report = await api.validate(document.id);
      set({ validation: report });
      return report;
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '校验失败' });
      return null;
    }
  },

  confirm: async () => {
    const report = await get().validate();
    const document = get().document;
    if (!report || !document || report.issues.some((issue) => issue.severity === 'error')) return;
    if (document.id === 'synthetic-preview') return;
    const warningCodes = report.issues
      .filter((issue) => issue.severity === 'warning')
      .map((issue) => issue.code);
    const confirmationMessage = warningCodes.length > 0
      ? `仍有 ${warningCodes.length} 类警告。确认已人工核对，并将当前记录表作为真实比赛数据提交吗？`
      : '确认将当前已保存并通过校验的记录表作为真实比赛数据提交吗？';
    if (!globalThis.confirm(confirmationMessage)) {
      return;
    }
    try {
      const confirmed = await api.confirm(document, get().serverRevision, warningCodes);
      set({
        document: confirmed,
        serverRevision: confirmed.revision,
        saveState: 'saved',
        dirty: false,
      });
      await Promise.all([get().refreshRevisions(), get().loadGames()]);
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '确认失败' });
    }
  },

  align: async (rotation, corners) => {
    const document = get().document;
    if (!document || document.id === 'synthetic-preview') return;
    try {
      const aligned = await api.align(document, get().serverRevision, rotation, corners);
      set({
        document: aligned,
        serverRevision: aligned.revision,
        saveState: 'saved',
        dirty: false,
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '图片校正失败' });
    }
  },

  refreshRevisions: async () => {
    const document = get().document;
    if (!document || document.id === 'synthetic-preview') return;
    try {
      set({ revisions: await api.revisions(document.id) });
    } catch {
      set({ revisions: [] });
    }
  },

  recognize: async () => {
    let document = get().document;
    if (!document || document.id === 'synthetic-preview') {
      set({ error: '请先从比赛列表选择比赛并上传记录表照片。' });
      return;
    }
    if (!document.game_prior) {
      set({ error: '该草稿没有比赛先验，请从比赛列表重新上传。' });
      return;
    }
    if (!document.source.original_url) {
      set({ error: '请先上传记录表照片。' });
      return;
    }
    if (get().dirty) await get().save();
    if (get().dirty || ['conflict', 'error'].includes(get().saveState)) return;

    document = get().document;
    if (!document) return;
    const beforeRecognition = deepCloneDocument(document);
    set({
      recognitionState: 'starting',
      recognitionRun: null,
      recognitionDiff: null,
      error: '',
    });
    try {
      let run = await api.createRecognition(document.id, get().serverRevision);
      set({ recognitionRun: run, recognitionState: 'running' });
      if (run.status !== 'succeeded' && run.status !== 'failed') {
        try {
          run = await api.streamRecognition(run.id, (update) => {
            set({ recognitionRun: update, recognitionState: 'running' });
          });
        } catch {
          for (let attempt = 0; attempt < RECOGNITION_POLL_LIMIT; attempt += 1) {
            if (run.status === 'succeeded' || run.status === 'failed') break;
            await wait(RECOGNITION_POLL_INTERVAL_MS);
            run = await api.recognition(run.id);
            set({ recognitionRun: run, recognitionState: 'running' });
          }
        }
      }
      if (run.status === 'failed') {
        set({
          recognitionRun: run,
          recognitionState: 'failed',
          error: run.error || '图像识别失败。',
        });
        return;
      }
      if (run.status !== 'succeeded') {
        set({ recognitionState: 'failed', error: '图像识别等待超时，请稍后重试。' });
        return;
      }
      if (run.auto_applied) {
        const recognized = await api.document(document.id);
        set((state) => ({
          document: recognized,
          serverRevision: recognized.revision,
          recognitionRun: run,
          recognitionDiff: null,
          recognitionState: 'applied',
          validation: null,
          past: [...state.past.slice(-49), beforeRecognition],
          future: [],
          dirty: false,
          saveState: 'saved',
        }));
        await Promise.all([get().refreshRevisions(), get().loadGames()]);
        return;
      }
      const diff = await api.recognitionDiff(run.id);
      set({
        recognitionRun: run,
        recognitionDiff: diff,
        recognitionState: 'diff',
      });
    } catch (error) {
      set({
        recognitionState: 'failed',
        error: error instanceof Error ? error.message : '图像识别失败。',
      });
    }
  },

  applyRecognition: async (regions) => {
    const { recognitionRun, document } = get();
    if (!recognitionRun || !document) return;
    if (regions.length === 0) {
      set({ error: '请至少选择一个需要应用的识别区域。' });
      return;
    }
    if (get().dirty) await get().save();
    if (get().dirty || ['conflict', 'error'].includes(get().saveState)) return;
    const beforeMerge = deepCloneDocument(get().document!);
    try {
      const merged = await api.applyRecognition(
        recognitionRun.id,
        get().serverRevision,
        regions,
      );
      set((state) => ({
        document: merged,
        serverRevision: merged.revision,
        recognitionDiff: null,
        recognitionState: 'applied',
        validation: null,
        past: [...state.past.slice(-49), beforeMerge],
        future: [],
        dirty: false,
        saveState: 'saved',
      }));
      await Promise.all([get().refreshRevisions(), get().loadGames()]);
    } catch (error) {
      set({
        recognitionState: 'failed',
        error: error instanceof Error ? error.message : '应用识别结果失败。',
      });
    }
  },

  clearRecognitionDiff: () => set({
    recognitionDiff: null,
    recognitionState: get().recognitionRun ? 'applied' : 'idle',
  }),
}));
