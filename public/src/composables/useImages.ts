import {
	collection,
	type DocumentData,
	doc,
	getDoc,
	getDocs,
	limit,
	orderBy,
	type QueryDocumentSnapshot,
	query,
	startAfter,
	where,
} from 'firebase/firestore';
import {ref} from 'vue';
import {db} from '../firebase';
import type {ImageDocument} from '../types';

export interface SortOption {
	field: string;
	direction: 'asc' | 'desc';
}

export interface RatingFilter {
	provider: 'joycaption' | 'minicpm';
	min: number | null;
	max: number | null;
}

const PAGE_SIZE = 20;

const loading = ref(false);
const error = ref<string | null>(null);
const currentSort = ref<SortOption | null>(null);
const currentRatingFilter = ref<RatingFilter | null>(null);

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

export function useImages() {
	async function loadPage(
		sort: SortOption,
		page: number,
		ratingFilter: RatingFilter | null = null,
		direction: 'forward' | 'backward' = 'forward',
	) {
		let effectivePage = page;

		// If sort or rating filter changed, reset cache
		const filterChanged =
			currentRatingFilter.value?.provider !== ratingFilter?.provider ||
			currentRatingFilter.value?.min !== ratingFilter?.min ||
			currentRatingFilter.value?.max !== ratingFilter?.max;

		if (
			currentSort.value?.field !== sort.field ||
			currentSort.value?.direction !== sort.direction ||
			filterChanged
		) {
			pageCache.value.clear();
			hasNextPage.value = true;
			hasPrevPage.value = false;
			currentSort.value = sort;
			currentRatingFilter.value = ratingFilter;
			effectivePage = 0;
		}

		// Check if page is already cached
		const cached = pageCache.value.get(effectivePage);
		if (cached) {
			hasNextPage.value =
				effectivePage === pageCache.value.size - 1 ? hasNextPage.value : true;
			hasPrevPage.value = effectivePage > 0;
			return {images: cached.images, page: effectivePage};
		}

		loading.value = true;
		error.value = null;

		try {
			const imagesRef = collection(db, 'images');

			// Build query constraints
			const constraints = [];

			// Add rating filters if present
			if (ratingFilter?.min !== null && ratingFilter?.min !== undefined) {
				const ratingField = `moderations.${ratingFilter.provider}.result`;
				constraints.push(where(ratingField, '>=', ratingFilter.min));
			}
			if (ratingFilter?.max !== null && ratingFilter?.max !== undefined) {
				const ratingField = `moderations.${ratingFilter.provider}.result`;
				constraints.push(where(ratingField, '<=', ratingFilter.max));
			}

			// Add orderBy
			constraints.push(orderBy(sort.field, sort.direction));

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
			snapshot.forEach((doc) => {
				const data = doc.data();
				// Filter: only include images with captions
				if (data.captions && Object.keys(data.captions).length > 0) {
					newImages.push({
						id: doc.id,
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

	return {
		loading,
		error,
		hasNextPage,
		hasPrevPage,
		loadPage,
		getImageById,
		clearCache,
	};
}
