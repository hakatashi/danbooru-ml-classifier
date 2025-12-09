import {computed} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {DEFAULT_SORT, getSortOption} from '../config/sortOptions';
import type {FiltersState, SortOption} from '../types/filters';

export function useFilterSync() {
	const route = useRoute();
	const router = useRouter();

	// Parse sort from URL
	const sortValue = computed(() => {
		const sortQuery = route.query.sort;
		return typeof sortQuery === 'string' ? sortQuery : DEFAULT_SORT;
	});

	const sort = computed<SortOption>(() => getSortOption(sortValue.value));

	// Parse page from URL
	const page = computed(() => {
		const pageQuery = route.query.page;
		if (typeof pageQuery === 'string') {
			const parsed = Number.parseInt(pageQuery, 10);
			return Number.isNaN(parsed) || parsed < 0 ? 0 : parsed;
		}
		return 0;
	});

	// Parse filters from URL
	const filters = computed<FiltersState>(() => {
		const ratingProvider =
			route.query.ratingProvider === 'joycaption' ? 'joycaption' : 'minicpm';
		const ratingMin =
			typeof route.query.ratingMin === 'string'
				? Number.parseInt(route.query.ratingMin, 10)
				: null;
		const ratingMax =
			typeof route.query.ratingMax === 'string'
				? Number.parseInt(route.query.ratingMax, 10)
				: null;

		const ageProvider =
			route.query.ageProvider === 'joycaption' ? 'joycaption' : 'minicpm';
		const ageMin =
			typeof route.query.ageMin === 'string'
				? Number.parseInt(route.query.ageMin, 10)
				: null;
		const ageMax =
			typeof route.query.ageMax === 'string'
				? Number.parseInt(route.query.ageMax, 10)
				: null;

		const twitterUserId =
			typeof route.query.twitterUserId === 'string'
				? route.query.twitterUserId
				: null;

		const pixaiTag =
			typeof route.query.pixaiTag === 'string' ? route.query.pixaiTag : null;
		const pixaiCategory =
			route.query.pixaiCategory === 'character' ||
			route.query.pixaiCategory === 'feature' ||
			route.query.pixaiCategory === 'ip'
				? route.query.pixaiCategory
				: 'feature';
		const pixaiConfidence =
			route.query.pixaiConfidence === 'high' ||
			route.query.pixaiConfidence === 'medium' ||
			route.query.pixaiConfidence === 'low'
				? route.query.pixaiConfidence
				: 'high';

		return {
			rating: {
				provider: ratingProvider,
				min: Number.isNaN(ratingMin) ? null : ratingMin,
				max: Number.isNaN(ratingMax) ? null : ratingMax,
			},
			age: {
				provider: ageProvider,
				min: Number.isNaN(ageMin) ? null : ageMin,
				max: Number.isNaN(ageMax) ? null : ageMax,
			},
			twitterUser: {
				userId: twitterUserId,
			},
			pixaiTag: pixaiTag
				? {
						tag: pixaiTag,
						category: pixaiCategory,
						confidence: pixaiConfidence,
					}
				: null,
		};
	});

	// Build query object from current state
	function buildQuery(
		newSort?: string,
		newPage?: number,
		newFilters?: Partial<FiltersState>,
	): Record<string, string> {
		const currentFilters = filters.value;
		const mergedFilters: FiltersState = newFilters
			? {
					rating: newFilters.rating ?? currentFilters.rating,
					age: newFilters.age ?? currentFilters.age,
					twitterUser: newFilters.twitterUser ?? currentFilters.twitterUser,
					pixaiTag:
						newFilters.pixaiTag !== undefined
							? newFilters.pixaiTag
							: currentFilters.pixaiTag,
				}
			: currentFilters;

		const query: Record<string, string> = {
			sort: newSort !== undefined ? newSort : sortValue.value,
			page: (newPage !== undefined ? newPage : page.value).toString(),
		};

		// Add rating filter
		if (mergedFilters.rating.provider !== 'minicpm') {
			query.ratingProvider = mergedFilters.rating.provider;
		}
		if (mergedFilters.rating.min !== null) {
			query.ratingMin = mergedFilters.rating.min.toString();
		}
		if (mergedFilters.rating.max !== null) {
			query.ratingMax = mergedFilters.rating.max.toString();
		}

		// Add age filter
		if (mergedFilters.age.provider !== 'minicpm') {
			query.ageProvider = mergedFilters.age.provider;
		}
		if (mergedFilters.age.min !== null) {
			query.ageMin = mergedFilters.age.min.toString();
		}
		if (mergedFilters.age.max !== null) {
			query.ageMax = mergedFilters.age.max.toString();
		}

		// Add Twitter user filter
		if (mergedFilters.twitterUser.userId) {
			query.twitterUserId = mergedFilters.twitterUser.userId;
		}

		// Add PixAI tag filter
		if (mergedFilters.pixaiTag?.tag) {
			query.pixaiTag = mergedFilters.pixaiTag.tag;
			query.pixaiCategory = mergedFilters.pixaiTag.category;
			query.pixaiConfidence = mergedFilters.pixaiTag.confidence;
		}

		return query;
	}

	// Update sort and reset page
	async function updateSort(newSort: string) {
		await router.push({
			query: buildQuery(newSort, 0),
		});
	}

	// Update page
	async function updatePage(newPage: number) {
		await router.push({
			query: buildQuery(undefined, newPage),
		});
	}

	// Update filters and reset page
	async function updateFilters(
		newFilters: FiltersState | Partial<FiltersState>,
	) {
		await router.push({
			query: buildQuery(undefined, 0, newFilters),
		});
	}

	return {
		sortValue,
		sort,
		page,
		filters,
		updateSort,
		updatePage,
		updateFilters,
	};
}
