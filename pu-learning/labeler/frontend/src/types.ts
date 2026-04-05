export type FilterType = 'unlabeled' | 'labeled' | 'skipped' | 'all';
export type LabelType = 'pixiv_public' | 'pixiv_private' | 'not_bookmarked' | '__skip__' | null;
export type LabelSubFilter = 'all' | 'pixiv_public' | 'pixiv_private' | 'not_bookmarked';

export interface ImageItem {
  path: string;
  label: LabelType;
  rating: number | null;
  index: number;
}

export interface StatusData {
  total: number;
  labeled: number;
  skipped: number;
  remaining: number;
}

export interface UndoEntry {
  path: string;
  prevEntry: { label: LabelType; rating: number | null } | null;
}

export function labelClass(label: LabelType): string {
  if (label === 'pixiv_public') return 'label-public';
  if (label === 'pixiv_private') return 'label-private';
  if (label === 'not_bookmarked') return 'label-not-bm';
  if (label === '__skip__') return 'label-skip';
  return '';
}

export function labelShort(label: LabelType): string {
  if (label === 'pixiv_public') return 'public';
  if (label === 'pixiv_private') return 'private';
  if (label === 'not_bookmarked') return 'not_bm';
  if (label === '__skip__') return 'skip';
  return label ?? '';
}

export function isRatable(label: LabelType): boolean {
  return label === 'pixiv_public' || label === 'pixiv_private';
}
