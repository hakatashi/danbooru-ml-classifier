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

const IMAGE_CACHE_PREFIX = '/mnt/cache/danbooru-ml-classifier/images/';

/** Extract the source URL for a given image path, or null if unrecognised. */
export function sourceUrl(path: string): string | null {
  const rel = path.startsWith(IMAGE_CACHE_PREFIX) ? path.slice(IMAGE_CACHE_PREFIX.length) : path;
  const [provider, filename] = rel.split('/');
  if (!filename) return null;

  const stem = filename.replace(/\.[^.]+$/, ''); // strip extension

  if (provider === 'danbooru') {
    return `https://danbooru.donmai.us/posts/${stem}`;
  }
  if (provider === 'gelbooru') {
    return `https://gelbooru.com/index.php?page=post&s=view&id=${stem}`;
  }
  if (provider === 'pixiv') {
    // filename pattern: {id}_p{page}.ext — extract only the numeric ID
    const id = stem.replace(/_p\d+$/, '');
    return `https://www.pixiv.net/artworks/${id}`;
  }
  if (provider === 'sankaku') {
    return `https://chan.sankakucomplex.com/ja/posts/${stem}`;
  }
  return null;
}
