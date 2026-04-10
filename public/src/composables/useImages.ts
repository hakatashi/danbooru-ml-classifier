import {
	collection,
	type DocumentData,
	doc,
	documentId,
	getDoc,
	getDocs,
	limit,
	orderBy,
	type QueryDocumentSnapshot,
	query,
	setDoc,
	startAfter,
	where,
} from 'firebase/firestore';
import {ref} from 'vue';
import {db} from '../firebase';
import type {ImageDocument} from '../types';
import type {FiltersState, SortOption} from '../types/filters';

const PAGE_SIZE = 50;

const loading = ref(false);
const error = ref<string | null>(null);
const currentSort = ref<SortOption | null>(null);
const currentFilters = ref<FiltersState | null>(null);

// Store page data with cursors for navigation
const pageCache = ref<
	Map<
		number,
		{
			images: ImageDocument[];
			startCursor: QueryDocumentSnapshot<DocumentData> | null;
			endCursor: QueryDocumentSnapshot<DocumentData> | null;
		}
	>
>(new Map());
const hasNextPage = ref(true);
const hasPrevPage = ref(false);

// Dedicated favorites cache — keyed by image ID, loaded from the `favorites` collection
const favoritesCache = ref<
	Map<string, {isFavorited: boolean; categories: string[]}>
>(new Map());

function filtersEqual(a: FiltersState | null, b: FiltersState | null): boolean {
	if (a === null || b === null) return a === b;

	return (
		a.rating.provider === b.rating.provider &&
		a.rating.min === b.rating.min &&
		a.rating.max === b.rating.max &&
		a.age.provider === b.age.provider &&
		a.age.min === b.age.min &&
		a.age.max === b.age.max &&
		a.twitterUser.userId === b.twitterUser.userId &&
		a.pixaiTag?.tag === b.pixaiTag?.tag &&
		a.pixaiTag?.category === b.pixaiTag?.category &&
		a.pixaiTag?.confidence === b.pixaiTag?.confidence
	);
}

export function useImages() {
	/**
	 * Load favorites from the `favorites` collection for the given image IDs.
	 * Uses chunked `in` queries (Firestore limit: 30 per query).
	 */
	async function loadFavoritesForImages(imageIds: string[]): Promise<void> {
		if (imageIds.length === 0) return;

		const favRef = collection(db, 'favorites');
		const CHUNK = 30;

		for (let i = 0; i < imageIds.length; i += CHUNK) {
			const chunk = imageIds.slice(i, i + CHUNK);
			const q = query(favRef, where(documentId(), 'in', chunk));
			const snapshot = await getDocs(q);
			snapshot.forEach((d) => {
				favoritesCache.value.set(
					d.id,
					d.data() as {isFavorited: boolean; categories: string[]},
				);
			});
		}
	}

	async function loadPage(
		sort: SortOption,
		page: number,
		filters: FiltersState,
		direction: 'forward' | 'backward' = 'forward',
	) {
		let effectivePage = page;

		// If sort or filters changed, reset cache
		if (
			currentSort.value?.field !== sort.field ||
			currentSort.value?.direction !== sort.direction ||
			!filtersEqual(currentFilters.value, filters)
		) {
			pageCache.value.clear();
			hasNextPage.value = true;
			hasPrevPage.value = false;
			currentSort.value = sort;
			currentFilters.value = filters;
			effectivePage = 0;
		}

		// Check if page is already cached
		const cached = pageCache.value.get(effectivePage);
		if (cached) {
			hasNextPage.value =
				effectivePage === pageCache.value.size - 1 ? hasNextPage.value : true;
			hasPrevPage.value = effectivePage > 0;
			// Ensure favorites are loaded for cached images too
			await loadFavoritesForImages(cached.images.map((img) => img.id));
			return {images: cached.images, page: effectivePage};
		}

		loading.value = true;
		error.value = null;

		try {
			const imagesRef = collection(db, 'images');

			// Build query constraints
			const constraints = [];

			// Add Twitter user filter if present
			if (filters.twitterUser.userId) {
				constraints.push(
					where('source.user.id_str', '==', filters.twitterUser.userId),
				);
			}

			// Add rating filters if present
			if (filters.rating.min !== null && filters.rating.min !== undefined) {
				const ratingField = `moderations.${filters.rating.provider}.result`;
				constraints.push(where(ratingField, '>=', filters.rating.min));
			}
			if (filters.rating.max !== null && filters.rating.max !== undefined) {
				const ratingField = `moderations.${filters.rating.provider}.result`;
				constraints.push(where(ratingField, '<=', filters.rating.max));
			}

			// Add age filters if present
			if (filters.age.min !== null && filters.age.min !== undefined) {
				const ageField = `ageEstimations.${filters.age.provider}.main_character_age`;
				constraints.push(where(ageField, '>=', filters.age.min));
			}
			if (filters.age.max !== null && filters.age.max !== undefined) {
				const ageField = `ageEstimations.${filters.age.provider}.main_character_age`;
				constraints.push(where(ageField, '<=', filters.age.max));
			}

			// Add PixAI tag filter if present
			if (filters.pixaiTag?.tag) {
				const confidenceLevel = `${filters.pixaiTag.confidence}_confidence`;
				const tagField = `tags.pixai.tag_list.${confidenceLevel}.${filters.pixaiTag.category}.${filters.pixaiTag.tag}`;
				constraints.push(where(tagField, '==', true));
			}

			// Add orderBy (skip when PixAI tag filter is active to avoid index requirements)
			if (!filters.pixaiTag?.tag) {
				constraints.push(orderBy(sort.field, sort.direction));
			}

			// Add pagination
			if (direction === 'forward' && effectivePage > 0) {
				const prevPage = pageCache.value.get(effectivePage - 1);
				if (!prevPage?.endCursor) {
					throw new Error(
						'Cannot navigate forward without previous page cursor',
					);
				}
				constraints.push(startAfter(prevPage.endCursor));
			}

			constraints.push(limit(PAGE_SIZE));

			const q = query(imagesRef, ...constraints);

			const snapshot = await getDocs(q);

			if (snapshot.empty) {
				hasNextPage.value = false;
				return {images: [], page: effectivePage};
			}

			const newImages: ImageDocument[] = [];
			snapshot.forEach((d) => {
				const data = d.data();
				// Filter: only include images with captions
				if (data.captions && Object.keys(data.captions).length > 0) {
					newImages.push({
						id: d.id,
						...data,
					} as ImageDocument);
				}
			});

			// Cache the page
			pageCache.value.set(effectivePage, {
				images: newImages,
				startCursor: snapshot.docs[0] ?? null,
				endCursor: snapshot.docs[snapshot.docs.length - 1] ?? null,
			});

			hasNextPage.value = snapshot.docs.length === PAGE_SIZE;
			hasPrevPage.value = effectivePage > 0;

			// Load favorites for newly fetched images
			await loadFavoritesForImages(newImages.map((img) => img.id));

			return {images: newImages, page: effectivePage};
		} catch (e) {
			console.error('Error loading images:', e);
			error.value = (e as Error).message;
			return {images: [], page: effectivePage};
		} finally {
			loading.value = false;
		}
	}

	async function getImageById(id: string): Promise<ImageDocument | null> {
		// First check if already loaded in any cached page
		for (const pageData of pageCache.value.values()) {
			const cached = pageData.images.find((img) => img.id === id);
			if (cached) return cached;
		}

		// Fetch from Firestore
		try {
			const docRef = doc(db, 'images', id);
			const docSnap = await getDoc(docRef);
			if (docSnap.exists()) {
				return {
					id: docSnap.id,
					...docSnap.data(),
				} as ImageDocument;
			}
			return null;
		} catch (e) {
			console.error('Error fetching image:', e);
			return null;
		}
	}

	function clearCache() {
		pageCache.value.clear();
		hasNextPage.value = true;
		hasPrevPage.value = false;
		currentSort.value = null;
	}

	/**
	 * Toggle the favorite status for an image. Writes to the dedicated
	 * `favorites` collection (not the `images` collection).
	 */
	async function toggleFavorite(
		imageId: string,
		category = 'Uncategorized',
	): Promise<boolean> {
		try {
			const current = favoritesCache.value.get(imageId);
			const currentCategories = current?.categories ?? [];

			let newCategories: string[];
			if (currentCategories.includes(category)) {
				// Remove from favorites
				newCategories = currentCategories.filter((c) => c !== category);
			} else {
				// Add to favorites
				newCategories = [...currentCategories, category];
			}

			const isFavorited = newCategories.length > 0;

			const favRef = doc(db, 'favorites', imageId);
			await setDoc(favRef, {isFavorited, categories: newCategories});

			// Update reactive cache
			favoritesCache.value.set(imageId, {
				isFavorited,
				categories: newCategories,
			});

			return newCategories.includes(category);
		} catch (e) {
			console.error('Error toggling favorite:', e);
			throw e;
		}
	}

	function isFavorite(imageId: string, category = 'Uncategorized'): boolean {
		return (
			favoritesCache.value.get(imageId)?.categories.includes(category) ?? false
		);
	}

	return {
		loading,
		error,
		hasNextPage,
		hasPrevPage,
		loadPage,
		getImageById,
		clearCache,
		loadFavoritesForImages,
		toggleFavorite,
		isFavorite,
	};
}
