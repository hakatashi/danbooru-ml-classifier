import { useEffect, useRef, useState } from 'react';
import type { ImageItem } from '../types';
import { sourceUrl } from '../types';
import { cx } from '../utils/cx';
import { imageUrl, fetchSource } from '../api';
import styles from './ImageViewer.module.css';

const IMAGE_CACHE_PREFIX = '/mnt/cache/danbooru-ml-classifier/images/';

type SourceState = 'idle' | 'loading' | 'done' | 'none' | 'error';

interface Props {
  item: ImageItem | null;
}

export function ImageViewer({ item }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [loading, setLoading] = useState(false);
  const [sourceState, setSourceState] = useState<SourceState>('idle');

  useEffect(() => {
    if (!item || !imgRef.current) return;
    setLoading(true);
    setSourceState('idle');
    imgRef.current.src = imageUrl(item.path);
  }, [item?.path]);

  const dirPart = item?.path.replace(IMAGE_CACHE_PREFIX, '') ?? '';
  const postUrl = item ? sourceUrl(item.path) : null;

  // Determine if this provider supports source fetching (danbooru / gelbooru)
  const provider = dirPart.split('/')[0];
  const supportsSource = provider === 'danbooru' || provider === 'gelbooru';

  async function handleFetchSource() {
    if (!item || sourceState === 'loading') return;
    setSourceState('loading');
    try {
      const src = await fetchSource(item.path);
      if (src) {
        window.open(src, '_blank', 'noopener,noreferrer');
        setSourceState('done');
      } else {
        setSourceState('none');
      }
    } catch {
      setSourceState('error');
    }
  }

  const sourceBtnLabel: Record<SourceState, string> = {
    idle:    'Source',
    loading: '...',
    done:    'Source',
    none:    'Source なし',
    error:   'エラー',
  };

  return (
    <div className={styles.imageArea}>
      {item ? (
        <>
          <img
            ref={imgRef}
            className={cx(styles.mainImg, loading && styles.loading)}
            alt=""
            onLoad={() => setLoading(false)}
            onError={() => setLoading(false)}
          />
          <div className={styles.imageInfo}>
            {postUrl ? (
              <a
                href={postUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.sourceLink}
              >
                {dirPart}
              </a>
            ) : (
              dirPart
            )}
            {supportsSource && (
              <button
                className={cx(
                  styles.fetchSourceBtn,
                  sourceState === 'none' && styles.fetchSourceNone,
                  sourceState === 'error' && styles.fetchSourceError,
                )}
                onClick={handleFetchSource}
                disabled={sourceState === 'loading'}
                title="投稿のSource欄のURLを新しいタブで開く"
              >
                {sourceBtnLabel[sourceState]}
              </button>
            )}
          </div>
        </>
      ) : (
        <div className={styles.noImages}>表示できる画像がありません</div>
      )}
    </div>
  );
}
