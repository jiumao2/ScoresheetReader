import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import {
  ImagePlus,
  Redo2,
  RefreshCw,
  ScanLine,
  ShieldCheck,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import type { ScoresheetDocument } from '../types';

interface SourcePaneProps {
  document: ScoresheetDocument;
  onRequestUpload: () => void;
}

interface ViewSnapshot {
  zoom: number;
  scrollLeft: number;
  scrollTop: number;
}

const sameView = (left: ViewSnapshot, right: ViewSnapshot) => (
  Math.abs(left.zoom - right.zoom) < 0.005 &&
  Math.abs(left.scrollLeft - right.scrollLeft) < 1 &&
  Math.abs(left.scrollTop - right.scrollTop) < 1
);

export function SourcePane({ document, onRequestUpload }: SourcePaneProps) {
  const imageFrame = useRef<HTMLDivElement>(null);
  const sourceScroll = useRef<HTMLDivElement>(null);
  const activeDocument = useRef(document.id);
  const panSession = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
    snapshot: ViewSnapshot;
  } | null>(null);
  const zoomRef = useRef(1);
  const wheelSession = useRef(false);
  const wheelTimer = useRef<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [history, setHistory] = useState<ViewSnapshot[]>([]);
  const [future, setFuture] = useState<ViewSnapshot[]>([]);
  const [panning, setPanning] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [loadState, setLoadState] = useState<'loading' | 'loaded' | 'error'>('loading');
  const sourceUrl = document.source.original_url || document.source.aligned_url;

  const imageUrl = useMemo(
    () => (sourceUrl
      ? `${sourceUrl}${sourceUrl.includes('?') ? '&' : '?'}v=${document.revision}-${reloadVersion}`
      : ''),
    [sourceUrl, document.revision, reloadVersion],
  );

  const captureView = (): ViewSnapshot => ({
    zoom: zoomRef.current,
    scrollLeft: sourceScroll.current?.scrollLeft ?? 0,
    scrollTop: sourceScroll.current?.scrollTop ?? 0,
  });

  const pushHistory = (snapshot: ViewSnapshot) => {
    setHistory((items) => {
      const previous = items.at(-1);
      return previous && sameView(previous, snapshot)
        ? items
        : [...items.slice(-29), snapshot];
    });
    setFuture([]);
  };

  const applyView = (snapshot: ViewSnapshot) => {
    const nextZoom = Math.min(2.5, Math.max(0.65, snapshot.zoom));
    zoomRef.current = nextZoom;
    setZoom(nextZoom);
    window.requestAnimationFrame(() => {
      if (!sourceScroll.current) return;
      sourceScroll.current.scrollLeft = snapshot.scrollLeft;
      sourceScroll.current.scrollTop = snapshot.scrollTop;
    });
  };

  const zoomAt = (value: number, clientX: number, clientY: number, remember = true) => {
    const frame = imageFrame.current;
    const scroller = sourceScroll.current;
    if (!frame || !scroller) return;
    const nextZoom = Math.min(2.5, Math.max(0.65, Math.round(value * 100) / 100));
    if (nextZoom === zoomRef.current) return;
    if (remember) pushHistory(captureView());
    const frameBefore = frame.getBoundingClientRect();
    const anchorX = Math.min(1, Math.max(0, (clientX - frameBefore.left) / frameBefore.width));
    const anchorY = Math.min(1, Math.max(0, (clientY - frameBefore.top) / frameBefore.height));
    zoomRef.current = nextZoom;
    setZoom(nextZoom);
    window.requestAnimationFrame(() => {
      const frameAfter = imageFrame.current?.getBoundingClientRect();
      if (!frameAfter) return;
      scroller.scrollLeft += frameAfter.left + frameAfter.width * anchorX - clientX;
      scroller.scrollTop += frameAfter.top + frameAfter.height * anchorY - clientY;
    });
  };

  useEffect(() => {
    if (activeDocument.current === document.id) return;
    activeDocument.current = document.id;
    zoomRef.current = 1;
    setZoom(1);
    setHistory([]);
    setFuture([]);
    setReloadVersion(0);
    window.requestAnimationFrame(() => {
      if (!sourceScroll.current) return;
      sourceScroll.current.scrollLeft = 0;
      sourceScroll.current.scrollTop = 0;
    });
  }, [document.id]);

  useEffect(() => {
    const scroller = sourceScroll.current;
    if (!scroller) return;
    const handleWheel = (event: WheelEvent) => {
      const frame = imageFrame.current;
      if (!frame || event.deltaY === 0) return;
      event.preventDefault();
      if (!wheelSession.current) {
        wheelSession.current = true;
        pushHistory(captureView());
      }
      if (wheelTimer.current !== null) window.clearTimeout(wheelTimer.current);
      wheelTimer.current = window.setTimeout(() => {
        wheelSession.current = false;
        wheelTimer.current = null;
      }, 180);
      const factor = event.deltaY < 0 ? 1.1 : 0.9;
      zoomAt(zoomRef.current * factor, event.clientX, event.clientY, false);
    };
    scroller.addEventListener('wheel', handleWheel, { passive: false });
    return () => {
      scroller.removeEventListener('wheel', handleWheel);
      if (wheelTimer.current !== null) window.clearTimeout(wheelTimer.current);
    };
  }, []);

  const undoView = () => {
    const previous = history.at(-1);
    if (!previous) return;
    setHistory((items) => items.slice(0, -1));
    setFuture((items) => [captureView(), ...items.slice(0, 29)]);
    applyView(previous);
  };

  const redoView = () => {
    const next = future[0];
    if (!next) return;
    setFuture((items) => items.slice(1));
    setHistory((items) => [...items.slice(-29), captureView()]);
    applyView(next);
  };

  const fitImage = () => {
    const current = captureView();
    const fitted = { zoom: 1, scrollLeft: 0, scrollTop: 0 };
    if (sameView(current, fitted)) return;
    pushHistory(current);
    applyView(fitted);
  };

  const beginPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const scroller = sourceScroll.current;
    if (!sourceUrl || !scroller || event.button !== 0) return;
    if ((event.target as HTMLElement).closest('button')) return;
    event.preventDefault();
    panSession.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: scroller.scrollLeft,
      scrollTop: scroller.scrollTop,
      snapshot: captureView(),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanning(true);
  };

  const movePan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSession.current;
    const scroller = sourceScroll.current;
    if (!session || !scroller || session.pointerId !== event.pointerId) return;
    scroller.scrollLeft = session.scrollLeft - (event.clientX - session.startX);
    scroller.scrollTop = session.scrollTop - (event.clientY - session.startY);
  };

  const finishPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSession.current;
    if (!session || session.pointerId !== event.pointerId) return;
    panSession.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setPanning(false);
    if (!sameView(session.snapshot, captureView())) pushHistory(session.snapshot);
  };

  const reloadImage = () => {
    setLoadState('loading');
    setReloadVersion((value) => value + 1);
  };

  const zoomFromCenter = (nextZoom: number) => {
    const rect = sourceScroll.current?.getBoundingClientRect();
    if (!rect) return;
    zoomAt(nextZoom, rect.left + rect.width / 2, rect.top + rect.height / 2);
  };

  return (
    <section className="workspace-pane source-pane" aria-label="原始照片">
      <header className="source-header">
        <div className="source-heading">
          <span className="pane-kicker">照片对照台</span>
          <strong title={document.source.original_filename || undefined}>
            {document.source.original_filename || '尚未上传照片'}
          </strong>
        </div>
        <div className="source-header-actions">
          {sourceUrl ? <span className="source-status"><span />原图</span> : null}
          <button className="source-icon-button" onClick={onRequestUpload} title="选择其他照片并新建草稿" aria-label="选择其他照片">
            <ImagePlus size={15} />
          </button>
          <button className="source-icon-button" disabled={!sourceUrl} onClick={reloadImage} title="重新从本机载入当前照片" aria-label="重新载入照片">
            <RefreshCw size={15} />
          </button>
        </div>
      </header>

      <div className="source-toolstrip" aria-label="照片查看工具">
        <div className="source-tool-group">
          <button className="source-icon-button" onClick={undoView} disabled={history.length === 0} title="撤回照片视图" aria-label="撤回照片视图">
            <Undo2 size={15} />
          </button>
          <button className="source-icon-button" onClick={redoView} disabled={future.length === 0} title="恢复下一张照片视图" aria-label="照片视图向前一步">
            <Redo2 size={15} />
          </button>
        </div>
        <span className="source-tool-divider" />
        <div className="source-tool-group source-zoom-tools">
          <button className="source-icon-button" disabled={!sourceUrl || zoom <= 0.65} onClick={() => zoomFromCenter(zoom - 0.1)} title="缩小原图" aria-label="降低原图倍率">
            <ZoomOut size={15} />
          </button>
          <button className="source-zoom-readout" disabled={!sourceUrl} onClick={fitImage} title="适合栏宽并复位位置" aria-label="适合栏宽">
            {Math.round(zoom * 100)}%
          </button>
          <button className="source-icon-button" disabled={!sourceUrl || zoom >= 2.5} onClick={() => zoomFromCenter(zoom + 0.1)} title="放大原图" aria-label="提高原图倍率">
            <ZoomIn size={15} />
          </button>
        </div>
        <span className="source-view-mode">只读原图</span>
      </div>

      <div
        ref={sourceScroll}
        className={`source-scroll${panning ? ' is-panning' : ''}`}
        aria-label="照片画布：拖动平移，滚轮缩放"
        onPointerDown={beginPan}
        onPointerMove={movePan}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
      >
        {sourceUrl ? (
          <div className="source-canvas">
            <div className="source-image-stage" style={{ width: `${zoom * 100}%` }}>
              <div className={`source-image-frame${loadState === 'loading' ? ' is-loading' : ''}`} ref={imageFrame}>
                <img
                  src={imageUrl}
                  alt="上传的篮球记录表"
                  draggable={false}
                  onLoad={() => setLoadState('loaded')}
                  onError={() => setLoadState('error')}
                />
                <span className="frame-corner top-left" aria-hidden="true" />
                <span className="frame-corner top-right" aria-hidden="true" />
                <span className="frame-corner bottom-right" aria-hidden="true" />
                <span className="frame-corner bottom-left" aria-hidden="true" />
                {loadState === 'error' ? (
                  <div className="source-load-error">
                    <ScanLine size={24} />
                    <strong>照片载入失败</strong>
                    <button onClick={reloadImage}>重新载入</button>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : (
          <button type="button" className="source-empty" onClick={onRequestUpload} aria-label="导入记录表照片">
            <span className="source-empty-mark"><ScanLine size={31} /></span>
            <strong>点击导入记录表照片</strong>
            <p>支持 JPEG、PNG、WebP，最大 25 MB。照片只保存在本机，不会发送给识别服务。</p>
            <span className="source-empty-action">选择本机照片</span>
          </button>
        )}
      </div>

      {sourceUrl ? (
        <footer className="source-footer">
          <div className="source-footer-meta">
            <span><ShieldCheck size={13} />本机原图</span>
            <small>{document.source.width} × {document.source.height}px</small>
          </div>
          <span className="source-gesture-hint">拖动平移 · 滚轮缩放</span>
        </footer>
      ) : null}
    </section>
  );
}
