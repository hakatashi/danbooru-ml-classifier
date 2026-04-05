import { useEffect, useRef, useState } from 'react';
import type { ImageItem } from '../types';
import { cx } from '../utils/cx';
import { imageUrl } from '../api';
import styles from './ImageViewer.module.css';

const IMAGE_CACHE_PREFIX = '/mnt/cache/danbooru-ml-classifier/images/';

interface Props {
  item: ImageItem | null;
}

export function ImageViewer({ item }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!item || !imgRef.current) return;
    setLoading(true);
    imgRef.current.src = imageUrl(item.path);
  }, [item?.path]);

  const dirPart = item?.path.replace(IMAGE_CACHE_PREFIX, '') ?? '';

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
          <div className={styles.imageInfo}>{dirPart}</div>
        </>
      ) : (
        <div className={styles.noImages}>表示できる画像がありません</div>
      )}
    </div>
  );
}
