import type { LabelType } from '../types';
import { isRatable } from '../types';
import { cx } from '../utils/cx';
import styles from './Controls.module.css';

interface Props {
  currentLabel: LabelType;
  currentRating: number | null;
  currentPos: number;
  totalItems: number;
  canUndo: boolean;
  onLabel: (label: string) => void;
  onRating: (rating: number) => void;
  onNavigate: (delta: number) => void;
  onSkip: () => void;
  onUndo: () => void;
}

const RATINGS = [
  { value: 1, label: '★ 弱',   shortcut: '4' },
  { value: 2, label: '★★ 中', shortcut: '5' },
  { value: 3, label: '★★★ 強', shortcut: '6' },
];

export function Controls({
  currentLabel,
  currentRating,
  currentPos,
  totalItems,
  canUndo,
  onLabel,
  onRating,
  onNavigate,
  onSkip,
  onUndo,
}: Props) {
  const ratingVisible = isRatable(currentLabel);

  return (
    <div className={styles.controls}>
      <div className={cx(styles.ratingRow, ratingVisible && styles.visible)}>
        <span className={styles.ratingRowLabel}>強度:</span>
        {RATINGS.map((r) => (
          <button
            key={r.value}
            className={cx(styles.ratingBtn, currentRating === r.value && styles.active)}
            onClick={() => onRating(r.value)}
          >
            {r.label}
            <span className={styles.shortcut}>{r.shortcut}</span>
          </button>
        ))}
      </div>

      <div className={styles.labelButtons}>
        <button
          className={cx(styles.labelBtn, styles.btnPublic, currentLabel === 'pixiv_public' && styles.selected)}
          onClick={() => onLabel('pixiv_public')}
        >
          pixiv_public
          <span className={styles.shortcut}>1 / Q</span>
        </button>
        <button
          className={cx(styles.labelBtn, styles.btnPrivate, currentLabel === 'pixiv_private' && styles.selected)}
          onClick={() => onLabel('pixiv_private')}
        >
          pixiv_private
          <span className={styles.shortcut}>2 / W</span>
        </button>
        <button
          className={cx(styles.labelBtn, styles.btnNotBm, currentLabel === 'not_bookmarked' && styles.selected)}
          onClick={() => onLabel('not_bookmarked')}
        >
          not_bookmarked
          <span className={styles.shortcut}>3 / E</span>
        </button>
      </div>

      <div className={styles.navButtons}>
        <button className={styles.navBtn} onClick={() => onNavigate(-1)} disabled={currentPos === 0}>
          ← 前
        </button>
        <span className={styles.positionIndicator}>
          {totalItems > 0 ? `${currentPos + 1} / ${totalItems}` : '-/-'}
        </span>
        <button className={styles.navBtn} onClick={() => onNavigate(1)} disabled={currentPos >= totalItems - 1}>
          次 →
        </button>
        <button className={styles.btnSkip} onClick={onSkip}>
          スキップ (S)
        </button>
        <button className={styles.btnUndo} onClick={onUndo} disabled={!canUndo}>
          ↩ Undo (Ctrl+Z)
        </button>
      </div>
    </div>
  );
}
