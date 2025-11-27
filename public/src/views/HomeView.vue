<script setup lang="ts">
import type {User} from 'firebase/auth';
import {computed, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import FilterBar from '../components/FilterBar.vue';
import ImageCard from '../components/ImageCard.vue';
import {
	type RatingFilter,
	type SortOption,
	useImages,
} from '../composables/useImages';
import type {ImageDocument} from '../types';

const props = defineProps<{
	user: User | null;
}>();

const router = useRouter();
const route = useRoute();

const {loading, error, hasNextPage, hasPrevPage, loadPage} = useImages();
const currentPage = ref(0);
const currentSort = ref<SortOption | null>(null);
const currentImages = ref<ImageDocument[]>([]);

// Sort options mapping
const sortOptionsMap = {
	'joycaption-desc': {
		field: 'moderations.joycaption.result',
		direction: 'desc' as const,
	},
	'joycaption-asc': {
		field: 'moderations.joycaption.result',
		direction: 'asc' as const,
	},
	'minicpm-desc': {
		field: 'moderations.minicpm.result',
		direction: 'desc' as const,
	},
	'minicpm-asc': {
		field: 'moderations.minicpm.result',
		direction: 'asc' as const,
	},
	'joycaption-created-desc': {
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'desc' as const,
	},
	'joycaption-created-asc': {
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'asc' as const,
	},
	'minicpm-created-desc': {
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'desc' as const,
	},
	'minicpm-created-asc': {
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'asc' as const,
	},
};

// Get sort and page values from query params
const sortValue = computed(() => {
	const sortQuery = route.query.sort;
	return typeof sortQuery === 'string' ? sortQuery : 'minicpm-desc';
});

const pageValue = computed(() => {
	const pageQuery = route.query.page;
	if (typeof pageQuery === 'string') {
		const parsed = Number.parseInt(pageQuery, 10);
		return Number.isNaN(parsed) || parsed < 0 ? 0 : parsed;
	}
	return 0;
});

const ratingProviderValue = computed(() => {
	const providerQuery = route.query.ratingProvider;
	if (
		typeof providerQuery === 'string' &&
		(providerQuery === 'joycaption' || providerQuery === 'minicpm')
	) {
		return providerQuery;
	}
	return 'minicpm';
});

const ratingMinValue = computed(() => {
	const minQuery = route.query.ratingMin;
	if (typeof minQuery === 'string') {
		const parsed = Number.parseInt(minQuery, 10);
		return Number.isNaN(parsed) ? null : parsed;
	}
	return null;
});

const ratingMaxValue = computed(() => {
	const maxQuery = route.query.ratingMax;
	if (typeof maxQuery === 'string') {
		const parsed = Number.parseInt(maxQuery, 10);
		return Number.isNaN(parsed) ? null : parsed;
	}
	return null;
});

// Watch for user authentication and query parameter changes
watch(
	[
		() => props.user,
		sortValue,
		pageValue,
		ratingProviderValue,
		ratingMinValue,
		ratingMaxValue,
	],
	async ([
		newUser,
		newSort,
		newPage,
		newRatingProvider,
		newRatingMin,
		newRatingMax,
	]) => {
		if (newUser && newSort) {
			const sortOption =
				sortOptionsMap[newSort as keyof typeof sortOptionsMap] ||
				sortOptionsMap['minicpm-desc'];
			currentSort.value = sortOption;

			const ratingFilter: RatingFilter = {
				provider: newRatingProvider,
				min: newRatingMin,
				max: newRatingMax,
			};

			const result = await loadPage(sortOption, newPage, ratingFilter);
			currentImages.value = result.images;
			currentPage.value = result.page;
		}
	},
	{immediate: true},
);

const canGoNext = computed(() => {
	return hasNextPage.value && currentImages.value.length > 0;
});

const canGoPrev = computed(() => {
	return hasPrevPage.value;
});

async function onSortChange(_sort: SortOption, sortKey: string) {
	// Update URL with new sort, reset page to 0
	await router.push({
		query: {
			sort: sortKey,
			page: '0',
		},
	});
}

async function onPageChange(page: number) {
	if (!currentSort.value || !props.user) return;

	window.scrollTo({top: 0, behavior: 'smooth'});

	// Update URL with new page, preserve rating filters
	const query: Record<string, string> = {
		sort: sortValue.value,
		page: page.toString(),
	};

	if (ratingProviderValue.value !== 'minicpm') {
		query.ratingProvider = ratingProviderValue.value;
	}
	if (ratingMinValue.value !== null) {
		query.ratingMin = ratingMinValue.value.toString();
	}
	if (ratingMaxValue.value !== null) {
		query.ratingMax = ratingMaxValue.value.toString();
	}

	await router.push({query});
}

async function onRatingChange(ratingFilter: RatingFilter) {
	// Update URL with new rating filter, reset page to 0
	const query: Record<string, string> = {
		sort: sortValue.value,
		page: '0',
	};

	if (ratingFilter.provider !== 'minicpm') {
		query.ratingProvider = ratingFilter.provider;
	}
	if (ratingFilter.min !== null) {
		query.ratingMin = ratingFilter.min.toString();
	}
	if (ratingFilter.max !== null) {
		query.ratingMax = ratingFilter.max.toString();
	}

	await router.push({query});
}
</script>

<template>
	<div>
		<!-- Auth Required -->
		<div
			v-if="!user"
			class="bg-white rounded-2xl shadow-lg p-12 text-center max-w-md mx-auto"
		>
			<div
				class="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-6"
			>
				<svg
					class="w-10 h-10 text-blue-500"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
					/>
				</svg>
			</div>
			<h2 class="text-2xl font-bold text-gray-900 mb-3">
				Authentication Required
			</h2>
			<p class="text-gray-600 mb-6">
				Please login with your Google account to view the VLM results.
			</p>
		</div>

		<!-- Main Content -->
		<template v-else>
			<FilterBar
				:current-page="currentPage"
				:current-sort="sortValue"
				:current-rating-provider="ratingProviderValue"
				:current-rating-min="ratingMinValue"
				:current-rating-max="ratingMaxValue"
				:can-go-next="canGoNext"
				:can-go-prev="canGoPrev"
				@sort-change="onSortChange"
				@page-change="onPageChange"
				@rating-change="onRatingChange"
			/>

			<!-- Loading State -->
			<div
				v-if="loading && currentImages.length === 0"
				class="flex justify-center items-center py-20"
			>
				<div class="flex flex-col items-center gap-4">
					<div
						class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
					></div>
					<p class="text-gray-600">Loading images...</p>
				</div>
			</div>

			<!-- Error State -->
			<div
				v-else-if="error"
				class="bg-red-50 border border-red-200 rounded-xl p-6 text-center"
			>
				<p class="text-red-700">Error: {{ error }}</p>
			</div>

			<!-- Empty State -->
			<div
				v-else-if="currentImages.length === 0"
				class="bg-white rounded-xl shadow-md p-12 text-center"
			>
				<div
					class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4"
				>
					<svg
						class="w-8 h-8 text-gray-400"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
						/>
					</svg>
				</div>
				<p class="text-gray-600">No images found</p>
			</div>

			<!-- Image Grid - Increased density -->
			<div
				v-else
				class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-3"
			>
				<ImageCard
					v-for="image in currentImages"
					:key="image.id"
					:image="image"
				/>
			</div>
		</template>
	</div>
</template>
