import {ref} from 'vue';
import {
	fetchPageViews,
	markPageViewed,
	recordImageViews,
	unmarkPageViewed,
} from '../api/mlApi';

export interface PageViewKey {
	date: string;
	sortField: string;
	page: number;
}

function keyOf({date, sortField, page}: PageViewKey): string {
	return `${date}|${sortField}|${page}`;
}

// Module-level singleton state shared across every composable instance,
// mirroring the pattern in composables/useFavorites.ts.
const viewedPages = ref<Map<string, string>>(new Map()); // key -> markedAt
const pendingKeys = ref<Set<string>>(new Set());

export function usePageViews() {
	function isPageViewed(key: PageViewKey): boolean {
		return viewedPages.value.has(keyOf(key));
	}

	function isPending(key: PageViewKey): boolean {
		return pendingKeys.value.has(keyOf(key));
	}

	/**
	 * Network hydration via GET /page-views. Used by the unviewed-pages
	 * listing to paint which (date, page) combinations are already marked.
	 */
	async function loadPageViews(params: {
		sortField: string;
		dateFrom?: string;
		dateTo?: string;
	}): Promise<void> {
		const {views} = await fetchPageViews(params);
		for (const view of views) {
			viewedPages.value.set(
				keyOf({date: view.date, sortField: view.sortField, page: view.page}),
				view.markedAt,
			);
		}
	}

	function clearPageViewsCache(): void {
		viewedPages.value.clear();
		pendingKeys.value.clear();
	}

	async function markViewed(
		key: PageViewKey,
		imageIds: string[],
	): Promise<void> {
		const k = keyOf(key);
		const previous = viewedPages.value.get(k);
		pendingKeys.value.add(k);
		viewedPages.value.set(k, new Date().toISOString()); // optimistic
		try {
			const result = await markPageViewed({...key, imageIds});
			viewedPages.value.set(k, result.markedAt);
		} catch (e) {
			if (previous) {
				viewedPages.value.set(k, previous);
			} else {
				viewedPages.value.delete(k);
			}
			throw e;
		} finally {
			pendingKeys.value.delete(k);
		}
	}

	async function unmarkViewed(key: PageViewKey): Promise<void> {
		const k = keyOf(key);
		const previous = viewedPages.value.get(k);
		pendingKeys.value.add(k);
		viewedPages.value.delete(k); // optimistic
		try {
			await unmarkPageViewed(key);
		} catch (e) {
			if (previous) viewedPages.value.set(k, previous);
			throw e;
		} finally {
			pendingKeys.value.delete(k);
		}
	}

	/**
	 * Fire-and-forget intermediate-label recording for opening an image's
	 * detail page or its zoom/fullscreen viewer. Swallows errors (logged to
	 * console) so callers don't need try/catch around a non-critical signal.
	 */
	function recordView(imageId: string, kind: 'detail' | 'zoom'): void {
		recordImageViews({ids: [imageId], kind}).catch((e) => {
			console.error(`Failed to record ${kind} view:`, e);
		});
	}

	return {
		viewedPages,
		isPageViewed,
		isPending,
		loadPageViews,
		clearPageViewsCache,
		markViewed,
		unmarkViewed,
		recordView,
	};
}
