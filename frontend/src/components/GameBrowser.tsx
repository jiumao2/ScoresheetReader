import { CalendarDays, MapPin, RefreshCw, Search, Upload, X } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import type { GameSummary } from '../types';

interface GameBrowserProps {
  games: GameSummary[];
  loading: boolean;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onOpen: (documentId: string) => Promise<void>;
  onUpload: (gameId: string, file: File) => Promise<void>;
}

const stateLabels: Record<GameSummary['scoresheet_state'], string> = {
  not_uploaded: '待上传',
  uploaded: '已上传',
  recognized: '已识别',
  confirmed: '已提交',
};

export function GameBrowser({
  games,
  loading,
  onClose,
  onRefresh,
  onOpen,
  onUpload,
}: GameBrowserProps) {
  const [query, setQuery] = useState('');
  const [selectedGame, setSelectedGame] = useState<GameSummary | null>(null);
  const [uploading, setUploading] = useState(false);
  const [openingGameId, setOpeningGameId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN');
  const visibleGames = useMemo(() => games.filter((game) => {
    if (!normalizedQuery) return true;
    return [
      game.competition,
      game.division,
      game.team_a_name,
      game.team_b_name,
      game.date,
      game.venue,
    ].some((value) => value.toLocaleLowerCase('zh-CN').includes(normalizedQuery));
  }), [games, normalizedQuery]);

  return (
    <div className="game-browser-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="game-browser" role="dialog" aria-modal="true" aria-labelledby="game-browser-title">
        <header>
          <div>
            <span className="pane-kicker">赛程先验</span>
            <h2 id="game-browser-title">选择比赛</h2>
            <p>已有记录表的比赛可直接打开；未上传比赛在选中后导入照片。</p>
          </div>
          <button className="icon-button" aria-label="关闭比赛列表" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="game-browser-tools">
          <label className="game-search">
            <Search size={15} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索球队、组别、日期或地点" autoFocus />
          </label>
          <button className="secondary-action" onClick={() => void onRefresh()} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : undefined} /> 刷新
          </button>
          <span>{visibleGames.length} / {games.length} 场</span>
        </div>
        <div className="game-list" aria-label="可选比赛">
          {visibleGames.map((game) => (
            <button
              key={game.id}
              className={`game-row${selectedGame?.id === game.id ? ' is-selected' : ''}${openingGameId === game.id ? ' is-opening' : ''}`}
              disabled={(!game.ready && !game.document_id) || uploading || openingGameId !== null}
              title={game.document_id ? '打开这场比赛的记录表' : (!game.ready ? game.unavailable_reason : '选择后上传记录表照片')}
              onClick={async () => {
                if (!game.document_id) {
                  setSelectedGame(game);
                  return;
                }
                setOpeningGameId(game.id);
                try {
                  await onOpen(game.document_id);
                  onClose();
                } catch {
                  // The editor store exposes the failure through its existing error toast.
                } finally {
                  setOpeningGameId(null);
                }
              }}
            >
              <span className="game-date"><CalendarDays size={14} />{game.date}<small>{game.scheduled_time}</small></span>
              <span className="game-matchup"><strong>{game.team_a_name}</strong><em>VS</em><strong>{game.team_b_name}</strong><small>{game.division}</small></span>
              <span className="game-venue"><MapPin size={13} />{game.venue || '地点待定'}</span>
              <span className={`game-ready state-${game.document_id ? game.scoresheet_state : (game.ready ? 'not_uploaded' : 'disabled')}`}>
                {openingGameId === game.id ? '打开中' : (game.document_id ? stateLabels[game.scoresheet_state] : (game.ready ? '待上传' : '球队待定'))}
              </span>
            </button>
          ))}
          {!loading && visibleGames.length === 0 ? <div className="game-list-empty">没有匹配的比赛</div> : null}
        </div>
        <footer>
          <div>
            {selectedGame ? <><strong>{selectedGame.team_a_name} vs {selectedGame.team_b_name}</strong><span>名单不含球衣号码，号码仍由图片读取</span></> : <span>选择未上传比赛以导入照片；已有结果可直接点击打开</span>}
          </div>
          <input
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file || !selectedGame) return;
              setUploading(true);
              try {
                await onUpload(selectedGame.id, file);
                onClose();
              } finally {
                setUploading(false);
                event.target.value = '';
              }
            }}
          />
          <button className="confirm-button" disabled={!selectedGame || uploading} onClick={() => fileInput.current?.click()}>
            <Upload size={15} /> {uploading ? '正在创建草稿…' : '上传这场比赛的照片'}
          </button>
        </footer>
      </section>
    </div>
  );
}
