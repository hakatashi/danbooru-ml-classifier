import { useEffect, useRef, useState, memo } from 'react';
import type { ImageItem, LabelType } from '../types';
import { cx } from '../utils/cx';
import { thumbnailUrl } from '../api';
import styles from './Sidebar.module.css';

const ITEM_HEIGHT = 52;
const OVERSCAN = 8;

function labelColorClass(label: LabelType): string {
  if (label === 'pixiv_public') return styles.labelPublic;
  if (label === 'pixiv_private') return styles.labelPrivate;
  if (label === 'not_bookmarked') return styles.labelNotBm;
  if (label === '__skip__') return styles.labelSkip;
  return '';
}

function labelShort(label: LabelType): string {
  if (label === 'pixiv_public') return 'public';
  if (label === 'pixiv_private') return 'private';
  if (label === 'not_bookmarked') return 'not_bm';
  if (label === '__skip__') return 'skip';
  return '';
}

interface ThumbItemProps {
  item: ImageItem;
  pos: number;
  isActive: boolean;
  onSelect: (pos: number) => void;
}

const ThumbItem = memo(function ThumbItem({ item, pos, isActive, onSelect }: ThumbItemProps) {
  const filename = item.path.split('/').pop() ?? '';
  const ratingTxt =
    item.rating != null && (item.label === 'pixiv_public' || item.label === 'pixiv_private')
      ? '★'.repeat(item.rating)
      : '';

  return (
    <div
      className={cx(styles.thumbItem, isActive && styles.active)}
      onClick={() => onSelect(pos)}
    >
      <img
        src={thumbnailUrl(item.path)}
        width={40}
        height={40}
        loading="lazy"
        decoding="async"
        className={styles.thumbImg}
        onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
        alt=""
      />
      <div className={styles.thumbMeta}>
        <div className={styles.thumbName} title={filename}>{filename}</div>
        <div className={cx(styles.thumbLabel, labelColorClass(item.label))}>
          {labelShort(item.label)}{ratingTxt ? ` ${ratingTxt}` : ''}
        </div>
      </div>
    </div>
  );
});

interface SidebarProps {
  items: ImageItem[];
  currentPos: number;
  onSelect: (pos: number) => void;
}

export function Sidebar({ items, currentPos, onSelect }: SidebarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewHeight, setViewHeight] = useState(400);
  const prevPosRef = useRef(currentPos);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    setViewHeight(el.clientHeight);
    const ro = new ResizeObserver(([entry]) => setViewHeight(entry.contentRect.height));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || currentPos === prevPosRef.current) return;
    prevPosRef.current = currentPos;
    const targetTop = currentPos * ITEM_HEIGHT - el.clientHeight / 2 + ITEM_HEIGHT / 2;
    el.scrollTop = Math.max(0, targetTop);
  }, [currentPos]);

  const rawStart = Math.floor(scrollTop / ITEM_HEIGHT);
  const rawEnd = Math.ceil((scrollTop + viewHeight) / ITEM_HEIGHT);
  const start = Math.max(0, rawStart - OVERSCAN);
  const end = Math.min(items.length - 1, rawEnd + OVERSCAN);

  return (
    <div className={styles.sidebar}>
      <div className={styles.header}>
        画像一覧 <span className={styles.count}>({items.length})</span>
      </div>
      <div
        className={styles.scroll}
        ref={scrollRef}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        <div className={styles.spacer} style={{ height: items.length * ITEM_HEIGHT }} />
        <div
          className={styles.content}
          style={{ transform: `translateY(${start * ITEM_HEIGHT}px)` }}
        >
          {items.length === 0 ? (
            <div className={styles.empty}>画像なし</div>
          ) : (
            items.slice(start, end + 1).map((item, i) => {
              const pos = start + i;
              return (
                <ThumbItem
                  key={item.path}
                  item={item}
                  pos={pos}
                  isActive={pos === currentPos}
                  onSelect={onSelect}
                />
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
