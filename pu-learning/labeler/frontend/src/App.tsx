import { useCallback, useEffect, useRef, useState } from 'react';
import type { FilterType, ImageItem, LabelSubFilter, StatusData, UndoEntry } from './types';
import { isRatable } from './types';
import * as api from './api';
import { cx } from './utils/cx';
import { TopBar } from './components/TopBar';
import { SubBar } from './components/SubBar';
import { Sidebar } from './components/Sidebar';
import { ImageViewer } from './components/ImageViewer';
import { Controls } from './components/Controls';
import { Toast } from './components/Toast';
import styles from './App.module.css';

const PRELOAD_AHEAD = 3;
const PRELOAD_BEHIND = 1;
const MAX_UNDO = 30;

export default function App() {
  const [items, setItems] = useState<ImageItem[]>([]);
  const [currentPos, setCurrentPos] = useState(0);
  const [filter, setFilter] = useState<FilterType>('unlabeled');
  const [labelSubFilter, setLabelSubFilter] = useState<LabelSubFilter>('all');
  const [status, setStatus] = useState<StatusData>({ total: 0, labeled: 0, skipped: 0, remaining: 0 });
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [toastMsg, setToastMsg] = useState('');
  const [toastVisible, setToastVisible] = useState(false);

  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const preloadCacheRef = useRef<Map<string, HTMLImageElement>>(new Map());

  // ── Toast ───────────────────────────────────────────────────────────────
  const showToast = useCallback((msg: string) => {
    setToastMsg(msg);
    setToastVisible(true);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToastVisible(false), 1500);
  }, []);

  // ── Preload ─────────────────────────────────────────────────────────────
  const preloadAround = useCallback((pos: number, itemList: ImageItem[]) => {
    const cache = preloadCacheRef.current;
    const keep = new Set<string>();
    for (let i = pos - PRELOAD_BEHIND; i <= pos + PRELOAD_AHEAD; i++) {
      if (i < 0 || i >= itemList.length || i === pos) continue;
      const path = itemList[i].path;
      keep.add(path);
      if (!cache.has(path)) {
        const img = new Image();
        img.src = api.imageUrl(path);
        cache.set(path, img);
      }
    }
    for (const path of cache.keys()) {
      if (!keep.has(path)) cache.delete(path);
    }
  }, []);

  // ── Status refresh ──────────────────────────────────────────────────────
  const refreshStatus = useCallback(async () => {
    const s = await api.fetchStatus();
    setStatus(s);
  }, []);

  // ── Load items ──────────────────────────────────────────────────────────
  const loadItems = useCallback(
    async (resetPos: boolean, currentFilter: FilterType, currentSubFilter: LabelSubFilter) => {
      const d = await api.fetchImages(currentFilter);
      let newItems = d.items;

      if (currentFilter === 'labeled') {
        newItems = [...newItems].reverse();
        if (currentSubFilter !== 'all') {
          newItems = newItems.filter((item) => item.label === currentSubFilter);
        }
      }

      setItems(newItems);
      preloadCacheRef.current.clear();
      if (resetPos) {
        setCurrentPos(0);
        preloadAround(0, newItems);
      }
    },
    [preloadAround],
  );

  // ── Init ────────────────────────────────────────────────────────────────
  useEffect(() => {
    void refreshStatus();
    void loadItems(true, 'unlabeled', 'all');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Filter change ───────────────────────────────────────────────────────
  const handleFilterChange = useCallback(
    async (newFilter: FilterType) => {
      setFilter(newFilter);
      await loadItems(true, newFilter, labelSubFilter);
      await refreshStatus();
    },
    [loadItems, refreshStatus, labelSubFilter],
  );

  const handleSubFilterChange = useCallback(
    async (newSubFilter: LabelSubFilter) => {
      setLabelSubFilter(newSubFilter);
      await loadItems(true, filter, newSubFilter);
    },
    [loadItems, filter],
  );

  // ── Navigate ────────────────────────────────────────────────────────────
  const navigate = useCallback(
    (delta: number, itemList: ImageItem[] = items, pos: number = currentPos) => {
      const newPos = pos + delta;
      if (newPos < 0 || newPos >= itemList.length) return;
      setCurrentPos(newPos);
      preloadAround(newPos, itemList);
    },
    [items, currentPos, preloadAround],
  );

  const handleSelect = useCallback(
    (pos: number) => {
      setCurrentPos(pos);
      preloadAround(pos, items);
    },
    [items, preloadAround],
  );

  // ── Undo stack ──────────────────────────────────────────────────────────
  const pushUndo = useCallback((path: string, prevEntry: UndoEntry['prevEntry']) => {
    setUndoStack((prev) => {
      const next = [...prev, { path, prevEntry }];
      return next.length > MAX_UNDO ? next.slice(next.length - MAX_UNDO) : next;
    });
  }, []);

  const handleUndo = useCallback(async () => {
    if (undoStack.length === 0) {
      showToast('元に戻す操作がありません');
      return;
    }
    const { path, prevEntry } = undoStack[undoStack.length - 1];
    setUndoStack((prev) => prev.slice(0, -1));

    if (!prevEntry) {
      await api.postUnlabel(path);
    } else {
      await api.postLabel(path, prevEntry.label ?? '', prevEntry.rating ?? 1);
    }

    setItems((prev) =>
      prev.map((item) =>
        item.path === path
          ? { ...item, label: prevEntry?.label ?? null, rating: prevEntry?.rating ?? null }
          : item,
      ),
    );
    await refreshStatus();
    showToast('元に戻しました');
  }, [undoStack, refreshStatus, showToast]);

  // ── Labeling ────────────────────────────────────────────────────────────
  const applyLabel = useCallback(
    async (label: string) => {
      if (items.length === 0) return;
      const item = items[currentPos];

      if (item.label === label) {
        // Toggle off: remove label
        const d = await api.postUnlabel(item.path);
        if (!d.ok) return;
        pushUndo(item.path, { label: item.label, rating: item.rating });
        setItems((prev) =>
          prev.map((it) => (it.path === item.path ? { ...it, label: null, rating: null } : it)),
        );
        await refreshStatus();
        showToast('ラベルを削除しました');
        return;
      }

      const d = await api.postLabel(item.path, label, 1);
      if (!d.ok) { showToast(`エラー: ${d.error ?? ''}`); return; }

      pushUndo(item.path, item.label != null ? { label: item.label, rating: item.rating } : null);
      const newLabel = label as ImageItem['label'];
      const newRating = isRatable(newLabel) ? 1 : null;

      setItems((prev) =>
        prev.map((it) =>
          it.path === item.path ? { ...it, label: newLabel, rating: newRating } : it,
        ),
      );
      if (d.labeled != null && d.total != null) {
        setStatus((s) => ({ ...s, labeled: d.labeled!, total: d.total!, remaining: d.total! - d.labeled! }));
      }
      showToast(`${label.replace('pixiv_', '').replace('not_bookmarked', 'not_bm')} を保存しました`);

      if (filter === 'unlabeled') {
        setTimeout(() => {
          setItems((prev) => {
            const next = prev.filter((it) => it.path !== item.path);
            const nextPos = Math.min(currentPos, next.length - 1);
            setCurrentPos(nextPos);
            preloadAround(nextPos, next);
            return next;
          });
        }, 150);
      }
    },
    [items, currentPos, filter, pushUndo, refreshStatus, showToast, preloadAround],
  );

  const applyRating = useCallback(
    async (rating: number) => {
      if (items.length === 0) return;
      const item = items[currentPos];
      if (!isRatable(item.label)) return;

      const d = await api.postRating(item.path, rating);
      if (!d.ok) { showToast(`エラー: ${d.error ?? ''}`); return; }

      pushUndo(item.path, { label: item.label, rating: item.rating ?? 1 });
      setItems((prev) =>
        prev.map((it) => (it.path === item.path ? { ...it, rating } : it)),
      );
      showToast(`${'★'.repeat(rating)} で保存しました`);
    },
    [items, currentPos, pushUndo, showToast],
  );

  const skipImage = useCallback(async () => {
    if (items.length === 0) return;
    const item = items[currentPos];
    const d = await api.postLabel(item.path, '__skip__');
    if (!d.ok) return;
    pushUndo(item.path, item.label != null ? { label: item.label, rating: item.rating } : null);
    setItems((prev) =>
      prev.map((it) => (it.path === item.path ? { ...it, label: '__skip__', rating: null } : it)),
    );
    showToast('スキップしました');
    if (filter === 'unlabeled') {
      setTimeout(() => {
        setItems((prev) => {
          const next = prev.filter((it) => it.path !== item.path);
          const nextPos = Math.min(currentPos, next.length - 1);
          setCurrentPos(nextPos);
          preloadAround(nextPos, next);
          return next;
        });
      }, 150);
    }
  }, [items, currentPos, filter, pushUndo, showToast, preloadAround]);

  // ── Keyboard shortcuts ──────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;

      if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault();
        void handleUndo();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      switch (e.key) {
        case '1': case 'q': case 'Q': void applyLabel('pixiv_public'); break;
        case '2': case 'w': case 'W': void applyLabel('pixiv_private'); break;
        case '3': case 'e': case 'E': void applyLabel('not_bookmarked'); break;
        case 's': case 'S':           void skipImage(); break;
        case '4': void applyRating(1); break;
        case '5': void applyRating(2); break;
        case '6': void applyRating(3); break;
        case 'ArrowLeft':  case 'ArrowUp':   navigate(-1); break;
        case 'ArrowRight': case 'ArrowDown': navigate(1); break;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleUndo, applyLabel, applyRating, skipImage, navigate]);

  const currentItem = items.length > 0 ? items[Math.min(currentPos, items.length - 1)] : null;

  return (
    <>
      <TopBar status={status} filter={filter} onFilterChange={handleFilterChange} />
      <SubBar
        visible={filter === 'labeled'}
        subFilter={labelSubFilter}
        onSubFilterChange={handleSubFilterChange}
      />
      <div className={styles.main}>
        <Sidebar items={items} currentPos={currentPos} onSelect={handleSelect} />
        <div className={cx(styles.viewer)}>
          <ImageViewer item={currentItem} />
          <Controls
            currentLabel={currentItem?.label ?? null}
            currentRating={currentItem?.rating ?? null}
            currentPos={currentPos}
            totalItems={items.length}
            canUndo={undoStack.length > 0}
            onLabel={applyLabel}
            onRating={applyRating}
            onNavigate={navigate}
            onSkip={skipImage}
            onUndo={handleUndo}
          />
        </div>
      </div>
      <Toast message={toastMsg} visible={toastVisible} />
      <div className={styles.help}>
        1/Q: public &nbsp; 2/W: private &nbsp; 3/E: not_bm &nbsp; S: skip &nbsp;
        4/5/6: 強度 &nbsp; ←/→: 移動 &nbsp; Ctrl+Z: undo
      </div>
    </>
  );
}
