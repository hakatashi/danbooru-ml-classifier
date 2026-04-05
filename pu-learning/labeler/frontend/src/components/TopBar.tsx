import type { FilterType, StatusData } from '../types';
import { cx } from '../utils/cx';
import styles from './TopBar.module.css';

const FILTERS: { value: FilterType; label: string }[] = [
  { value: 'unlabeled', label: '未ラベル' },
  { value: 'labeled', label: 'ラベル済み' },
  { value: 'skipped', label: 'スキップ' },
  { value: 'all', label: '全て' },
];

interface Props {
  status: StatusData;
  filter: FilterType;
  onFilterChange: (f: FilterType) => void;
}

export function TopBar({ status, filter, onFilterChange }: Props) {
  const pct = status.total > 0 ? (status.labeled / status.total) * 100 : 0;

  return (
    <div className={styles.topbar}>
      <span className={styles.progressText}>
        {status.total === 0
          ? '読み込み中...'
          : `${status.labeled} / ${status.total} ラベル済み (${pct.toFixed(1)}%) | 残り ${status.remaining}`}
      </span>
      <div className={styles.progressBarWrap}>
        <div className={styles.progressBar} style={{ width: `${pct}%` }} />
      </div>
      <div className={styles.filterTabs}>
        {FILTERS.map((f) => (
          <button
            key={f.value}
            className={cx(styles.tabBtn, filter === f.value && styles.active)}
            onClick={() => onFilterChange(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>
    </div>
  );
}
