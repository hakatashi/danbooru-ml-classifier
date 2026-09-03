// Shared fallback shown in place of a broken/missing image -- used both when
// `imageDeleted` is already known (worker/prune_old_images.py deleted the
// on-disk file to reclaim disk space, but keeps the MongoDB doc) and as an
// `@error` safety net for any other case (thumbnail lag, legacy Twitter
// imports with no thumbnail variant, etc).
export const IMAGE_PLACEHOLDER_DATA_URL =
	'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><rect width=%22200%22 height=%22200%22 fill=%22%23f1f5f9%22/><text x=%2250%%22 y=%2250%%22 text-anchor=%22middle%22 fill=%22%23999%22>Image removed</text></svg>';

// Swaps a broken <img>'s src to the shared placeholder; use as an @error handler.
export function useImagePlaceholderFallback(event: Event): void {
	const target = event.target as HTMLImageElement;
	if (target.src !== IMAGE_PLACEHOLDER_DATA_URL) {
		target.src = IMAGE_PLACEHOLDER_DATA_URL;
	}
}
