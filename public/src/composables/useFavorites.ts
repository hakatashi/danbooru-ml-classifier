import {ref} from 'vue';
import {
	type ApiImageDocument,
	type FavoriteCategoryCount,
	fetchFavoriteCategories,
	lookupFavorites,
	updateFavorites,
} from '../api/mlApi';
import type {FavoritesData} from '../types';

export interface FavoritesEntry {
	isFavorited: boolean;
	categories: string[];
	favoritedAt?: string | null;
	updatedAt?: string;
}

// Module-level singleton state shared across every composable instance,
// mirroring the pattern in composables/useImages.ts.
const favoritesCache = ref<Map<string, FavoritesEntry>>(new Map());
const pendingIds = ref<Set<string>>(new Set());
const categoryList = ref<FavoriteCategoryCount[]>([]);

export function useFavorites() {
	function isFavorite(imageId: string, category = 'Uncategorized'): boolean {
		return (
			favoritesCache.value.get(imageId)?.categories.includes(category) ?? false
		);
	}

	function isFavoritedAny(imageId: string): boolean {
		return favoritesCache.value.get(imageId)?.isFavorited ?? false;
	}

	function getCategories(imageId: string): string[] {
		return favoritesCache.value.get(imageId)?.categories ?? [];
	}

	function isPending(imageId: string): boolean {
		return pendingIds.value.has(imageId);
	}

	/**
	 * Synchronous, no-network hydration from ApiImageDocument.favorites, which
	 * is already embedded in every /images and /favorites response. Use this
	 * whenever images come from the Mongo-backed API; it makes the previous
	 * per-page Firestore round trip disappear entirely.
	 */
	function hydrateFromImages(images: ApiImageDocument[]): void {
		for (const image of images) {
			if (image.favorites) {
				favoritesCache.value.set(image.id, {
					isFavorited: image.favorites.isFavorited,
					categories: image.favorites.categories,
					favoritedAt: image.favorites.favoritedAt,
					updatedAt: image.favorites.updatedAt,
				});
			}
		}
	}

	function hydrateOne(imageId: string, favorites: FavoritesData): void {
		favoritesCache.value.set(imageId, {
			isFavorited: favorites.isFavorited,
			categories: favorites.categories,
			favoritedAt: favorites.favoritedAt,
			updatedAt: favorites.updatedAt,
		});
	}

	/**
	 * Network hydration via POST /favorites/lookup. Only needed for views that
	 * source images from Firestore (ArchivesView, ImageDetailView), where
	 * `favorites` isn't embedded in the document.
	 */
	async function loadFavoritesForImages(imageIds: string[]): Promise<void> {
		if (imageIds.length === 0) return;
		const CHUNK = 500;
		for (let i = 0; i < imageIds.length; i += CHUNK) {
			const chunk = imageIds.slice(i, i + CHUNK);
			const {favorites} = await lookupFavorites(chunk);
			for (const [id, entry] of Object.entries(favorites)) {
				hydrateOne(id, entry);
			}
		}
	}

	async function loadCategories(): Promise<void> {
		const result = await fetchFavoriteCategories();
		categoryList.value = result.categories;
	}

	function clearFavoritesCache(): void {
		favoritesCache.value.clear();
		pendingIds.value.clear();
		categoryList.value = [];
	}

	async function setCategories(
		imageId: string,
		categories: string[],
	): Promise<void> {
		const previous = favoritesCache.value.get(imageId);
		pendingIds.value.add(imageId);
		// Optimistic update
		favoritesCache.value.set(imageId, {
			isFavorited: categories.length > 0,
			categories,
			favoritedAt: previous?.favoritedAt,
			updatedAt: new Date().toISOString(),
		});
		try {
			const result = await updateFavorites({
				ids: [imageId],
				op: 'set',
				categories,
			});
			const updated = result.updated[0];
			if (updated) {
				favoritesCache.value.set(imageId, {
					isFavorited: updated.isFavorited,
					categories: updated.categories,
					favoritedAt: updated.favoritedAt,
					updatedAt: updated.updatedAt,
				});
			}
		} catch (e) {
			// Revert on failure
			if (previous) {
				favoritesCache.value.set(imageId, previous);
			} else {
				favoritesCache.value.delete(imageId);
			}
			throw e;
		} finally {
			pendingIds.value.delete(imageId);
		}
	}

	async function toggleFavorite(
		imageId: string,
		category = 'Uncategorized',
	): Promise<boolean> {
		const previous = favoritesCache.value.get(imageId);
		const previousCategories = previous?.categories ?? [];
		const willBeFavorited = !previousCategories.includes(category);
		const optimisticCategories = willBeFavorited
			? [...previousCategories, category]
			: previousCategories.filter((c) => c !== category);

		pendingIds.value.add(imageId);
		favoritesCache.value.set(imageId, {
			isFavorited: optimisticCategories.length > 0,
			categories: optimisticCategories,
			favoritedAt: previous?.favoritedAt,
			updatedAt: new Date().toISOString(),
		});

		try {
			const result = await updateFavorites({
				ids: [imageId],
				op: 'toggle',
				categories: [category],
			});
			const updated = result.updated[0];
			if (updated) {
				favoritesCache.value.set(imageId, {
					isFavorited: updated.isFavorited,
					categories: updated.categories,
					favoritedAt: updated.favoritedAt,
					updatedAt: updated.updatedAt,
				});
				return updated.categories.includes(category);
			}
			return willBeFavorited;
		} catch (e) {
			if (previous) {
				favoritesCache.value.set(imageId, previous);
			} else {
				favoritesCache.value.delete(imageId);
			}
			throw e;
		} finally {
			pendingIds.value.delete(imageId);
		}
	}

	async function bulkUpdateCategories(
		imageIds: string[],
		op: 'add' | 'remove' | 'set',
		categories: string[],
	): Promise<{updatedCount: number; notFound: string[]}> {
		const previousEntries = new Map(
			imageIds.map((id) => [id, favoritesCache.value.get(id)] as const),
		);
		for (const id of imageIds) pendingIds.value.add(id);

		try {
			const CHUNK = 500;
			let notFound: string[] = [];
			let updatedCount = 0;
			for (let i = 0; i < imageIds.length; i += CHUNK) {
				const chunk = imageIds.slice(i, i + CHUNK);
				const result = await updateFavorites({ids: chunk, op, categories});
				for (const updated of result.updated) {
					favoritesCache.value.set(updated.id, {
						isFavorited: updated.isFavorited,
						categories: updated.categories,
						favoritedAt: updated.favoritedAt,
						updatedAt: updated.updatedAt,
					});
				}
				notFound = [...notFound, ...result.notFound];
				updatedCount += result.count;
			}
			return {updatedCount, notFound};
		} catch (e) {
			for (const [id, previous] of previousEntries) {
				if (previous) {
					favoritesCache.value.set(id, previous);
				} else {
					favoritesCache.value.delete(id);
				}
			}
			throw e;
		} finally {
			for (const id of imageIds) pendingIds.value.delete(id);
		}
	}

	return {
		favoritesCache,
		categoryList,
		isFavorite,
		isFavoritedAny,
		getCategories,
		isPending,
		hydrateFromImages,
		loadFavoritesForImages,
		loadCategories,
		clearFavoritesCache,
		setCategories,
		toggleFavorite,
		bulkUpdateCategories,
	};
}
