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
	updateDoc,
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

export interface AgeFilter {
	provider: 'joycaption' | 'minicpm';
	min: number | null;
	max: number | null;
}

export interface TwitterUserFilter {
	userId: string | null;
}

const PAGE_SIZE = 50;

const loading = ref(false);
const error = ref<string | null>(null);
const currentSort = ref<SortOption | null>(null);
const currentRatingFilter = ref<RatingFilter | null>(null);
const currentAgeFilter = ref<AgeFilter | null>(null);
const currentTwitterUserFilter = ref<TwitterUserFilter | null>(null);

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
		ageFilter: AgeFilter | null = null,
		twitterUserFilter: TwitterUserFilter | null = null,
		direction: 'forward' | 'backward' = 'forward',
	) {
		let effectivePage = page;

		// If sort or filters changed, reset cache
		const ratingFilterChanged =
			currentRatingFilter.value?.provider !== ratingFilter?.provider ||
			currentRatingFilter.value?.min !== ratingFilter?.min ||
			currentRatingFilter.value?.max !== ratingFilter?.max;

		const ageFilterChanged =
			currentAgeFilter.value?.provider !== ageFilter?.provider ||
			currentAgeFilter.value?.min !== ageFilter?.min ||
			currentAgeFilter.value?.max !== ageFilter?.max;

		const twitterUserFilterChanged =
			currentTwitterUserFilter.value?.userId !== twitterUserFilter?.userId;

		if (
			currentSort.value?.field !== sort.field ||
			currentSort.value?.direction !== sort.direction ||
			ratingFilterChanged ||
			ageFilterChanged ||
			twitterUserFilterChanged
		) {
			pageCache.value.clear();
			hasNextPage.value = true;
			hasPrevPage.value = false;
			currentSort.value = sort;
			currentRatingFilter.value = ratingFilter;
			currentAgeFilter.value = ageFilter;
			currentTwitterUserFilter.value = twitterUserFilter;
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

			// Add Twitter user filter if present
			if (twitterUserFilter?.userId) {
				constraints.push(
					where('source.user.id_str', '==', twitterUserFilter.userId),
				);
			}

			// Add rating filters if present
			if (ratingFilter?.min !== null && ratingFilter?.min !== undefined) {
				const ratingField = `moderations.${ratingFilter.provider}.result`;
				constraints.push(where(ratingField, '>=', ratingFilter.min));
			}
			if (ratingFilter?.max !== null && ratingFilter?.max !== undefined) {
				const ratingField = `moderations.${ratingFilter.provider}.result`;
				constraints.push(where(ratingField, '<=', ratingFilter.max));
			}

			// Add age filters if present
			if (ageFilter?.min !== null && ageFilter?.min !== undefined) {
				const ageField = `ageEstimations.${ageFilter.provider}.main_character_age`;
				constraints.push(where(ageField, '>=', ageFilter.min));
			}
			if (ageFilter?.max !== null && ageFilter?.max !== undefined) {
				const ageField = `ageEstimations.${ageFilter.provider}.main_character_age`;
				constraints.push(where(ageField, '<=', ageFilter.max));
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

	async function toggleFavorite(
		imageId: string,
		category = 'Uncategorized',
	): Promise<boolean> {
		try {
			const imageRef = doc(db, 'images', imageId);
			const imageSnap = await getDoc(imageRef);

			if (!imageSnap.exists()) {
				throw new Error('Image not found');
			}

			const imageData = imageSnap.data() as ImageDocument;
			const currentCategories = imageData.favorites?.categories || [];

			let newCategories: string[];
			if (currentCategories.includes(category)) {
				// Remove from favorites
				newCategories = currentCategories.filter((c) => c !== category);
			} else {
				// Add to favorites
				newCategories = [...currentCategories, category];
			}

			const isFavorited = newCategories.length > 0;

			await updateDoc(imageRef, {
				favorites: {
					isFavorited,
					categories: newCategories,
				},
			});

			// Update cache if present
			for (const pageData of pageCache.value.values()) {
				const cachedImage = pageData.images.find((img) => img.id === imageId);
				if (cachedImage) {
					cachedImage.favorites = {isFavorited, categories: newCategories};
				}
			}

			return newCategories.includes(category);
		} catch (e) {
			console.error('Error toggling favorite:', e);
			throw e;
		}
	}

	function isFavorite(
		image: ImageDocument,
		category = 'Uncategorized',
	): boolean {
		return image.favorites?.categories.includes(category) ?? false;
	}

	return {
		loading,
		error,
		hasNextPage,
		hasPrevPage,
		loadPage,
		getImageById,
		clearCache,
		toggleFavorite,
		isFavorite,
	};
}
