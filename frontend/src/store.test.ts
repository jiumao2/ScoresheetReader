import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { useEditorStore } from './store';
import { makeDocument, makeTemplate } from './test/fixtures';

beforeEach(() => {
  vi.restoreAllMocks();
  useEditorStore.setState({
    document: makeDocument(),
    serverRevision: 0,
    template: makeTemplate(),
    games: [],
    gamesLoading: false,
    recognitionMode: 'mock',
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
    loading: false,
    error: '',
  });
});

describe('editor persistence and history', () => {
  it('supports undo and redo with whole-document semantic snapshots', () => {
    useEditorStore.getState().mutate((draft) => {
      draft.header.competition = '修改后的比赛';
    });
    expect(useEditorStore.getState().document?.header.competition).toBe('修改后的比赛');
    expect(useEditorStore.getState().past).toHaveLength(1);

    useEditorStore.getState().undo();
    expect(useEditorStore.getState().document?.header.competition).toBe('合成测试赛');
    useEditorStore.getState().redo();
    expect(useEditorStore.getState().document?.header.competition).toBe('修改后的比赛');
  });

  it('saves and restores an offline synthetic draft from browser storage', async () => {
    vi.spyOn(api, 'template').mockResolvedValue(makeTemplate());
    useEditorStore.getState().mutate((draft) => {
      draft.header.competition = '关闭浏览器后恢复';
    });
    await useEditorStore.getState().save();
    useEditorStore.setState({ document: null, template: null, loading: true });

    await useEditorStore.getState().initialize();
    expect(useEditorStore.getState().document?.header.competition).toBe('关闭浏览器后恢复');
    expect(useEditorStore.getState().saveState).toBe('saved');
  });

  it('turns a stale backend revision into a visible conflict state', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, dirty: true });
    const conflict = Object.assign(new Error('草稿已被更新'), { status: 409 });
    vi.spyOn(api, 'save').mockRejectedValue(conflict);

    await useEditorStore.getState().save();
    expect(useEditorStore.getState().saveState).toBe('conflict');
    expect(useEditorStore.getState().error).toBe('草稿已被更新');
  });

  it('never validates or submits stale server data when pending edits fail to save', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, dirty: true, saveState: 'dirty' });
    vi.spyOn(api, 'save').mockRejectedValue(new Error('磁盘写入失败'));
    const validate = vi.spyOn(api, 'validate');

    const report = await useEditorStore.getState().validate();

    expect(report).toBeNull();
    expect(validate).not.toHaveBeenCalled();
    expect(useEditorStore.getState().dirty).toBe(true);
    expect(useEditorStore.getState().error).toBe('草稿尚未成功保存，已停止校验和提交。');
  });

  it('rebases an undo snapshot onto the current server revision before saving', async () => {
    const document = { ...makeDocument('persisted-document'), revision: 5 };
    useEditorStore.setState({ document, serverRevision: 5 });
    const save = vi.spyOn(api, 'save')
      .mockImplementation(async (candidate, baseRevision) => ({
        ...structuredClone(candidate),
        revision: baseRevision + 1,
      }));

    useEditorStore.getState().mutate((draft) => { draft.header.game_number = '123'; });
    await useEditorStore.getState().save();
    expect(useEditorStore.getState().serverRevision).toBe(6);
    useEditorStore.getState().undo();
    expect(useEditorStore.getState().document?.revision).toBe(6);
    await useEditorStore.getState().save('undo');

    expect(save).toHaveBeenLastCalledWith(
      expect.objectContaining({ revision: 6 }),
      6,
      'undo',
    );
    expect(useEditorStore.getState().saveState).toBe('saved');
  });

  it('opens a recognized game document and restores its model run', async () => {
    const document = {
      ...makeDocument('recognized-from-schedule'),
      revision: 4,
      recognition: {
        run_id: 'saved-run',
        notes: '一处号码待核对',
        table_personnel: ['示例记录台人员'],
        problem_paths: ['/teams/0/players/1/jersey_number'],
        applied_at: '2026-08-19T00:00:00Z',
      },
    };
    const run = {
      id: 'saved-run', document_id: document.id, base_revision: 0,
      status: 'succeeded' as const, model: 'qwen3.8-max', cached: false,
      prompt_version: 'scoresheet-2026-08-19-v4',
      auto_applied: true, applied_revision: 1, recognition_notes: '一处号码待核对',
      usage: { input_tokens: 6449, output_tokens: 3602, image_tokens: 2122, reasoning_tokens: 0, total_tokens: 10051 },
      error: '', result: {}, created_at: '2026-08-19T00:00:00Z',
      updated_at: '2026-08-19T00:00:00Z',
    };
    vi.spyOn(api, 'document').mockResolvedValue(document);
    vi.spyOn(api, 'recognition').mockResolvedValue(run);
    vi.spyOn(api, 'revisions').mockResolvedValue([]);

    await useEditorStore.getState().openDocument(document.id);

    expect(useEditorStore.getState().document?.id).toBe(document.id);
    expect(useEditorStore.getState().serverRevision).toBe(4);
    expect(useEditorStore.getState().recognitionRun?.usage.total_tokens).toBe(10051);
    expect(useEditorStore.getState().recognitionState).toBe('applied');
    expect(localStorage.getItem('scoresheet-reader:last-document-id')).toBe(document.id);
  });

  it('loads an automatically applied zero-token recognition into the editor history', async () => {
    const original = {
      ...makeDocument('recognized-document'),
      revision: 0,
      game_prior: {
        game_id: 'game-1', competition: '测试赛', division: '测试组', date: '2026-08-19',
        scheduled_time: '14:00', venue: '球馆', source_hash: 'hash', locked_paths: [],
        team_a: { team_id: 'a', name: '示例学院甲', player_names: ['甲队员一'] },
        team_b: { team_id: 'b', name: '示例学院乙', player_names: ['乙队员一'] },
      },
      source: { ...makeDocument().source, original_url: '/source.png' },
    };
    const recognized = {
      ...structuredClone(original),
      revision: 1,
      recognition: {
        run_id: 'run-1', notes: '', table_personnel: [], problem_paths: [],
        applied_at: '2026-08-19T00:00:00Z',
      },
    };
    const run = {
      id: 'run-1', document_id: original.id, base_revision: 0, status: 'succeeded' as const,
      model: 'qwen3.8-max', cached: false, auto_applied: true, applied_revision: 1,
      prompt_version: 'scoresheet-2026-08-19-v4',
      recognition_notes: '', usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
      error: '', result: {}, created_at: '2026-08-19T00:00:00Z', updated_at: '2026-08-19T00:00:00Z',
    };
    useEditorStore.setState({ document: original, serverRevision: 0 });
    vi.spyOn(api, 'createRecognition').mockResolvedValue(run);
    vi.spyOn(api, 'document').mockResolvedValue(recognized);
    vi.spyOn(api, 'revisions').mockResolvedValue([]);

    await useEditorStore.getState().recognize();

    expect(useEditorStore.getState().document?.revision).toBe(1);
    expect(useEditorStore.getState().serverRevision).toBe(1);
    expect(useEditorStore.getState().recognitionRun?.usage.total_tokens).toBe(0);
    expect(useEditorStore.getState().recognitionState).toBe('applied');
    expect(useEditorStore.getState().past).toHaveLength(1);
  });
});
