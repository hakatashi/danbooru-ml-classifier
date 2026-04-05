import type { LabelSubFilter } from '../types';
import { cx } from '../utils/cx';
import styles from './SubBar.module.css';

const SUB_FILTERS: { value: LabelSubFilter; label: string }[] = [
  { value: 'all', label: '全て' },
  { value: 'pixiv_public', label: 'public' },
  { value: 'pixiv_private', label: 'private' },
  { value: 'not_bookmarked', label: 'not_bm' },
];

interface Props {
  visible: boolean;
  subFilter: LabelSubFilter;
  onSubFilterChange: (f: LabelSubFilter) => void;
}

export function SubBar({ visible, subFilter, onSubFilterChange }: Props) {
  return (
    <div className={cx(styles.subbar, visible && styles.visible)}>
      <span className={styles.label}>ラベル絞込:</span>
      {SUB_FILTERS.map((f) => (
        <button
          key={f.value}
          className={cx(styles.subTab, subFilter === f.value && styles.active)}
          onClick={() => onSubFilterChange(f.value)}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}
