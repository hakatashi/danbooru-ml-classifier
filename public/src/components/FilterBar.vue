<script setup lang="ts">
import {doc, getDoc, getFirestore} from 'firebase/firestore';
import {computed, onMounted, ref, watch} from 'vue';
import type {RatingFilter, SortOption} from '../composables/useImages';

const props = defineProps<{
	currentPage: number;
	currentSort: string;
	currentRatingProvider: 'joycaption' | 'minicpm';
	currentRatingMin: number | null;
	currentRatingMax: number | null;
	currentAgeProvider: 'joycaption' | 'minicpm';
	currentAgeMin: number | null;
	currentAgeMax: number | null;
	currentTwitterUserId: string | null;
	currentPixaiTag: string | null;
	currentPixaiCategory: 'character' | 'feature' | 'ip' | null;
	currentPixaiConfidence: 'high' | 'medium' | 'low' | null;
	canGoNext: boolean;
	canGoPrev: boolean;
	galleryMode: boolean;
}>();

export interface AgeFilter {
	provider: 'joycaption' | 'minicpm';
	min: number | null;
	max: number | null;
}

export interface TwitterUserFilter {
	userId: string | null;
}

export interface PixAITagFilter {
	tag: string;
	category: 'character' | 'feature' | 'ip';
	confidence: 'high' | 'medium' | 'low';
}

const emit = defineEmits<{
	(e: 'sort-change', sort: SortOption, sortKey: string): void;
	(e: 'page-change', page: number): void;
	(e: 'rating-change', ratingFilter: RatingFilter): void;
	(e: 'age-change', ageFilter: AgeFilter): void;
	(e: 'twitter-user-change', twitterUserFilter: TwitterUserFilter): void;
	(e: 'pixai-tag-change', pixaiTagFilter: PixAITagFilter | null): void;
	(e: 'gallery-mode-change', enabled: boolean): void;
}>();

const isModalOpen = ref(false);

const sort = ref(props.currentSort);
const ratingProvider = ref<'joycaption' | 'minicpm'>(
	props.currentRatingProvider,
);
const ratingMin = ref<number | null>(props.currentRatingMin);
const ratingMax = ref<number | null>(props.currentRatingMax);
const ageProvider = ref<'joycaption' | 'minicpm'>(props.currentAgeProvider);
const ageMin = ref<number | null>(props.currentAgeMin);
const ageMax = ref<number | null>(props.currentAgeMax);
const twitterUserId = ref<string | null>(props.currentTwitterUserId);
const pixaiTag = ref<string | null>(props.currentPixaiTag);
const pixaiCategory = ref<'character' | 'feature' | 'ip' | null>(
	props.currentPixaiCategory,
);
const pixaiConfidence = ref<'high' | 'medium' | 'low' | null>(
	props.currentPixaiConfidence,
);
const totalCount = ref<number | null>(null);
const perPage = 50; // Images per page

// Sync sort with props when it changes
watch(
	() => props.currentSort,
	(newSort) => {
		sort.value = newSort;
	},
);

// Sync rating filters with props
watch(
	() => props.currentRatingProvider,
	(newProvider) => {
		ratingProvider.value = newProvider;
	},
);

watch(
	() => props.currentRatingMin,
	(newMin) => {
		ratingMin.value = newMin;
	},
);

watch(
	() => props.currentRatingMax,
	(newMax) => {
		ratingMax.value = newMax;
	},
);

// Sync age filters with props
watch(
	() => props.currentAgeProvider,
	(newProvider) => {
		ageProvider.value = newProvider;
	},
);

watch(
	() => props.currentAgeMin,
	(newMin) => {
		ageMin.value = newMin;
	},
);

watch(
	() => props.currentAgeMax,
	(newMax) => {
		ageMax.value = newMax;
	},
);

// Emit rating change when filters change
watch(
	[ratingProvider, ratingMin, ratingMax],
	([newProvider, newMin, newMax]) => {
		emit('rating-change', {provider: newProvider, min: newMin, max: newMax});
	},
);

// Emit age change when filters change
watch([ageProvider, ageMin, ageMax], ([newProvider, newMin, newMax]) => {
	emit('age-change', {provider: newProvider, min: newMin, max: newMax});
});

// Sync Twitter user filter with props
watch(
	() => props.currentTwitterUserId,
	(newUserId) => {
		twitterUserId.value = newUserId;
	},
);

// Emit Twitter user change when filter changes
watch(twitterUserId, (newUserId) => {
	emit('twitter-user-change', {userId: newUserId});
});

// Sync PixAI tag filters with props
watch(
	() => props.currentPixaiTag,
	(newTag) => {
		pixaiTag.value = newTag;
	},
);

watch(
	() => props.currentPixaiCategory,
	(newCategory) => {
		pixaiCategory.value = newCategory;
	},
);

watch(
	() => props.currentPixaiConfidence,
	(newConfidence) => {
		pixaiConfidence.value = newConfidence;
	},
);

// Emit PixAI tag change when filters change
watch(
	[pixaiTag, pixaiCategory, pixaiConfidence],
	([newTag, newCategory, newConfidence]) => {
		if (newTag && newCategory && newConfidence) {
			emit('pixai-tag-change', {
				tag: newTag,
				category: newCategory,
				confidence: newConfidence,
			});
		} else {
			emit('pixai-tag-change', null);
		}
	},
);

function clearPixaiFilter() {
	pixaiTag.value = null;
	pixaiCategory.value = null;
	pixaiConfidence.value = null;
}

const sortOptions = [
	{
		value: 'joycaption-desc',
		label: 'JoyCaption Rating (High to Low)',
		field: 'moderations.joycaption.result',
		direction: 'desc' as const,
	},
	{
		value: 'joycaption-asc',
		label: 'JoyCaption Rating (Low to High)',
		field: 'moderations.joycaption.result',
		direction: 'asc' as const,
	},
	{
		value: 'minicpm-desc',
		label: 'MiniCPM Rating (High to Low)',
		field: 'moderations.minicpm.result',
		direction: 'desc' as const,
	},
	{
		value: 'minicpm-asc',
		label: 'MiniCPM Rating (Low to High)',
		field: 'moderations.minicpm.result',
		direction: 'asc' as const,
	},
	{
		value: 'joycaption-age-desc',
		label: 'JoyCaption Age (High to Low)',
		field: 'ageEstimations.joycaption.main_character_age',
		direction: 'desc' as const,
	},
	{
		value: 'joycaption-age-asc',
		label: 'JoyCaption Age (Low to High)',
		field: 'ageEstimations.joycaption.main_character_age',
		direction: 'asc' as const,
	},
	{
		value: 'minicpm-age-desc',
		label: 'MiniCPM Age (High to Low)',
		field: 'ageEstimations.minicpm.main_character_age',
		direction: 'desc' as const,
	},
	{
		value: 'minicpm-age-asc',
		label: 'MiniCPM Age (Low to High)',
		field: 'ageEstimations.minicpm.main_character_age',
		direction: 'asc' as const,
	},
	{
		value: 'qwen3-age-desc',
		label: 'Qwen3 Age (High to Low)',
		field: 'ageEstimations.qwen3.main_character_age',
		direction: 'desc' as const,
	},
	{
		value: 'qwen3-age-asc',
		label: 'Qwen3 Age (Low to High)',
		field: 'ageEstimations.qwen3.main_character_age',
		direction: 'asc' as const,
	},
	{
		value: 'joycaption-created-desc',
		label: 'JoyCaption Created (Newest First)',
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'desc' as const,
	},
	{
		value: 'joycaption-created-asc',
		label: 'JoyCaption Created (Oldest First)',
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'asc' as const,
	},
	{
		value: 'minicpm-created-desc',
		label: 'MiniCPM Created (Newest First)',
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'desc' as const,
	},
	{
		value: 'minicpm-created-asc',
		label: 'MiniCPM Created (Oldest First)',
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'asc' as const,
	},
];

function getSortOption(value: string): SortOption {
	const option = sortOptions.find((o) => o.value === value);
	return option
		? {field: option.field, direction: option.direction}
		: {field: 'moderations.joycaption.result', direction: 'desc'};
}

// Fetch total count from moderationStats for a given sort model
async function fetchTotalCount(sortValue: string) {
	const sortModel = sortValue.includes('joycaption') ? 'joycaption' : 'minicpm';
	try {
		const db = getFirestore();
		const statsDoc = await getDoc(doc(db, 'moderationStats', sortModel));
		if (statsDoc.exists()) {
			totalCount.value = statsDoc.data().count;
		}
	} catch (error) {
		console.error('Error fetching moderation stats:', error);
	}
}

// Initialize on mount
onMounted(async () => {
	await fetchTotalCount(sort.value);
});

// Update total count and emit sort change when sort changes
watch(
	sort,
	async (newSort) => {
		await fetchTotalCount(newSort);
		emit('sort-change', getSortOption(newSort), newSort);
	},
	{immediate: true},
);

// Calculate total pages
const totalPages = computed(() => {
	if (totalCount.value === null) return null;
	return Math.ceil(totalCount.value / perPage);
});

function prev() {
	if (props.canGoPrev) {
		emit('page-change', props.currentPage - 1);
	}
}

function next() {
	if (props.canGoNext) {
		emit('page-change', props.currentPage + 1);
	}
}
</script>

<template>
	<div
		class="sticky top-[72px] z-10 bg-white rounded-xl shadow-md p-3 sm:p-4 mb-6"
	>
		<!-- Mobile view: Menu button and pagination only -->
		<div class="lg:hidden flex items-center justify-between gap-3">
			<!-- Menu button -->
			<button
				@click="isModalOpen = true"
				class="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-lg text-sm font-medium transition-all flex items-center gap-2"
			>
				<svg
					class="w-5 h-5"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M4 6h16M4 12h16M4 18h16"
					/>
				</svg>
				<span>Filters</span>
			</button>

			<!-- Pagination controls -->
			<div class="flex items-center gap-2">
				<button
					@click="prev"
					:disabled="!canGoPrev"
					:class="[
						'px-3 py-1.5 rounded-lg text-sm font-medium transition-all',
						canGoPrev
							? 'bg-blue-500 text-white hover:bg-blue-600'
							: 'bg-gray-100 text-gray-400 cursor-not-allowed',
					]"
				>
					<svg
						class="w-4 h-4"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M15 19l-7-7 7-7"
						/>
					</svg>
				</button>

				<span class="text-gray-600 font-medium text-sm whitespace-nowrap">
					{{ currentPage + 1 }}
					<span v-if="totalPages !== null">/ {{ totalPages }}</span>
				</span>

				<button
					@click="next"
					:disabled="!canGoNext"
					:class="[
						'px-3 py-1.5 rounded-lg text-sm font-medium transition-all',
						canGoNext
							? 'bg-blue-500 text-white hover:bg-blue-600'
							: 'bg-gray-100 text-gray-400 cursor-not-allowed',
					]"
				>
					<svg
						class="w-4 h-4"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M9 5l7 7-7 7"
						/>
					</svg>
				</button>
			</div>
		</div>

		<!-- Desktop view: Full filters and pagination -->
		<div class="hidden lg:flex flex-col gap-3">
			<div
				class="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3"
			>
				<!-- Left side: Sort and Rating filters -->
				<div
					class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3"
				>
					<!-- Sort dropdown -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							Sort:
						</label>
						<select
							v-model="sort"
							class="flex-1 sm:flex-none px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
						>
							<option
								v-for="option in sortOptions"
								:key="option.value"
								:value="option.value"
							>
								{{ option.label }}
							</option>
						</select>
					</div>

					<!-- Rating filters -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							Rating:
						</label>
						<div class="flex items-center gap-2">
							<select
								v-model="ratingProvider"
								class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
							>
								<option value="minicpm">MiniCPM</option>
								<option value="joycaption">JoyCaption</option>
							</select>
							<select
								v-model.number="ratingMin"
								class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
							>
								<option :value="null">Min</option>
								<option v-for="i in 11" :key="i - 1" :value="i - 1">
									{{ i - 1 }}
								</option>
							</select>
							<span class="text-gray-500 text-sm">-</span>
							<select
								v-model.number="ratingMax"
								class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
							>
								<option :value="null">Max</option>
								<option v-for="i in 11" :key="i - 1" :value="i - 1">
									{{ i - 1 }}
								</option>
							</select>
						</div>
					</div>

					<!-- Age filters -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							Age:
						</label>
						<div class="flex items-center gap-2">
							<select
								v-model="ageProvider"
								class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
							>
								<option value="minicpm">MiniCPM</option>
								<option value="joycaption">JoyCaption</option>
							</select>
							<select
								v-model.number="ageMin"
								class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
							>
								<option :value="null">Min</option>
								<option v-for="i in 18" :key="i * 5" :value="i * 5">
									{{ i * 5 }}
								</option>
							</select>
							<span class="text-gray-500 text-sm">-</span>
							<select
								v-model.number="ageMax"
								class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
							>
								<option :value="null">Max</option>
								<option v-for="i in 18" :key="i * 5" :value="i * 5">
									{{ i * 5 }}
								</option>
							</select>
						</div>
					</div>

					<!-- Twitter User filter -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							Twitter User:
						</label>
						<input
							v-model="twitterUserId"
							type="text"
							placeholder="User ID"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent w-32"
						>
						<button
							v-if="twitterUserId"
							@click="twitterUserId = null"
							class="p-1 text-gray-500 hover:text-gray-700"
							title="Clear filter"
						>
							<svg
								class="w-4 h-4"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M6 18L18 6M6 6l12 12"
								/>
							</svg>
						</button>
					</div>

					<!-- PixAI Tag filter -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							PixAI Tag:
						</label>
						<input
							v-model="pixaiTag"
							type="text"
							placeholder="Tag name"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent w-28"
						>
						<select
							v-model="pixaiCategory"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
						>
							<option :value="null">Category</option>
							<option value="character">Character</option>
							<option value="feature">Feature</option>
							<option value="ip">IP</option>
						</select>
						<select
							v-model="pixaiConfidence"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
						>
							<option :value="null">Confidence</option>
							<option value="high">High</option>
							<option value="medium">Medium</option>
							<option value="low">Low</option>
						</select>
						<button
							v-if="pixaiTag || pixaiCategory || pixaiConfidence"
							@click="clearPixaiFilter"
							class="p-1 text-gray-500 hover:text-gray-700"
							title="Clear filter"
						>
							<svg
								class="w-4 h-4"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M6 18L18 6M6 6l12 12"
								/>
							</svg>
						</button>
					</div>

					<!-- Gallery mode toggle -->
					<div class="flex items-center gap-2">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							Gallery:
						</label>
						<button
							@click="emit('gallery-mode-change', !galleryMode)"
							:class="[
								'relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2',
								galleryMode ? 'bg-blue-500' : 'bg-gray-200',
							]"
						>
							<span
								:class="[
									'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
									galleryMode ? 'translate-x-6' : 'translate-x-1',
								]"
							></span>
						</button>
					</div>
				</div>

				<!-- Right side: Pagination controls -->
				<div
					class="flex items-center justify-center lg:justify-end gap-2 sm:gap-3"
				>
					<button
						@click="prev"
						:disabled="!canGoPrev"
						:class="[
							'px-3 sm:px-4 py-1.5 rounded-lg text-sm font-medium transition-all shrink-0',
							canGoPrev
								? 'bg-blue-500 text-white hover:bg-blue-600 shadow-sm hover:shadow-md'
								: 'bg-gray-100 text-gray-400 cursor-not-allowed',
						]"
					>
						<span class="flex items-center gap-1">
							<svg
								class="w-4 h-4"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M15 19l-7-7 7-7"
								/>
							</svg>
							<span class="hidden sm:inline">Previous</span>
						</span>
					</button>

					<span
						class="text-gray-600 font-medium text-sm px-1 sm:px-2 whitespace-nowrap"
					>
						Page {{ currentPage + 1 }}
						<span v-if="totalPages !== null">/ {{ totalPages }}</span>
					</span>

					<button
						@click="next"
						:disabled="!canGoNext"
						:class="[
							'px-3 sm:px-4 py-1.5 rounded-lg text-sm font-medium transition-all shrink-0',
							canGoNext
								? 'bg-blue-500 text-white hover:bg-blue-600 shadow-sm hover:shadow-md'
								: 'bg-gray-100 text-gray-400 cursor-not-allowed',
						]"
					>
						<span class="flex items-center gap-1">
							<span class="hidden sm:inline">Next</span>
							<svg
								class="w-4 h-4"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M9 5l7 7-7 7"
								/>
							</svg>
						</span>
					</button>
				</div>
			</div>
		</div>
	</div>

	<!-- Mobile filter modal -->
	<Teleport to="body">
		<Transition
			enter-active-class="transition-opacity duration-200"
			leave-active-class="transition-opacity duration-200"
			enter-from-class="opacity-0"
			leave-to-class="opacity-0"
		>
			<div
				v-if="isModalOpen"
				class="fixed inset-0 bg-black/50 z-50 lg:hidden"
				@click="isModalOpen = false"
			>
				<Transition
					enter-active-class="transition-transform duration-300"
					leave-active-class="transition-transform duration-300"
					enter-from-class="translate-y-full"
					leave-to-class="translate-y-full"
				>
					<div
						v-if="isModalOpen"
						class="absolute bottom-0 left-0 right-0 bg-white rounded-t-2xl shadow-xl max-h-[80vh] overflow-y-auto"
						@click.stop
					>
						<!-- Modal header -->
						<div
							class="sticky top-0 bg-white border-b border-gray-200 p-4 flex items-center justify-between rounded-t-2xl"
						>
							<h2 class="text-lg font-semibold text-gray-800">Filters</h2>
							<button
								@click="isModalOpen = false"
								class="p-2 hover:bg-gray-100 rounded-lg transition-colors"
							>
								<svg
									class="w-6 h-6 text-gray-600"
									fill="none"
									stroke="currentColor"
									viewBox="0 0 24 24"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M6 18L18 6M6 6l12 12"
									/>
								</svg>
							</button>
						</div>

						<!-- Modal content -->
						<div class="p-4 space-y-4">
							<!-- Sort dropdown -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">Sort</label>
								<select
									v-model="sort"
									class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
								>
									<option
										v-for="option in sortOptions"
										:key="option.value"
										:value="option.value"
									>
										{{ option.label }}
									</option>
								</select>
							</div>

							<!-- Rating filters -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">
									Rating Filter
								</label>
								<div class="space-y-3">
									<select
										v-model="ratingProvider"
										class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
									>
										<option value="minicpm">MiniCPM</option>
										<option value="joycaption">JoyCaption</option>
									</select>
									<div class="grid grid-cols-2 gap-2">
										<select
											v-model.number="ratingMin"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Min</option>
											<option v-for="i in 11" :key="i - 1" :value="i - 1">
												{{ i - 1 }}
											</option>
										</select>
										<select
											v-model.number="ratingMax"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Max</option>
											<option v-for="i in 11" :key="i - 1" :value="i - 1">
												{{ i - 1 }}
											</option>
										</select>
									</div>
								</div>
							</div>

							<!-- Age filters -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">
									Age Filter
								</label>
								<div class="space-y-3">
									<select
										v-model="ageProvider"
										class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
									>
										<option value="minicpm">MiniCPM</option>
										<option value="joycaption">JoyCaption</option>
									</select>
									<div class="grid grid-cols-2 gap-2">
										<select
											v-model.number="ageMin"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Min</option>
											<option v-for="i in 18" :key="i * 5" :value="i * 5">
												{{ i * 5 }}
											</option>
										</select>
										<select
											v-model.number="ageMax"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Max</option>
											<option v-for="i in 18" :key="i * 5" :value="i * 5">
												{{ i * 5 }}
											</option>
										</select>
									</div>
								</div>
							</div>

							<!-- Twitter User filter -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">
									Twitter User Filter
								</label>
								<div class="flex items-center gap-2">
									<input
										v-model="twitterUserId"
										type="text"
										placeholder="Enter Twitter User ID"
										class="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
									>
									<button
										v-if="twitterUserId"
										@click="twitterUserId = null"
										class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg"
										title="Clear filter"
									>
										<svg
											class="w-5 h-5"
											fill="none"
											stroke="currentColor"
											viewBox="0 0 24 24"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="2"
												d="M6 18L18 6M6 6l12 12"
											/>
										</svg>
									</button>
								</div>
							</div>

							<!-- PixAI Tag filter -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">
									PixAI Tag Filter
								</label>
								<div class="space-y-3">
									<div class="flex items-center gap-2">
										<input
											v-model="pixaiTag"
											type="text"
											placeholder="Enter tag name"
											class="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
										<button
											v-if="pixaiTag || pixaiCategory || pixaiConfidence"
											@click="clearPixaiFilter"
											class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg"
											title="Clear filter"
										>
											<svg
												class="w-5 h-5"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													d="M6 18L18 6M6 6l12 12"
												/>
											</svg>
										</button>
									</div>
									<div class="grid grid-cols-2 gap-2">
										<select
											v-model="pixaiCategory"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Category</option>
											<option value="character">Character</option>
											<option value="feature">Feature</option>
											<option value="ip">IP</option>
										</select>
										<select
											v-model="pixaiConfidence"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Confidence</option>
											<option value="high">High</option>
											<option value="medium">Medium</option>
											<option value="low">Low</option>
										</select>
									</div>
								</div>
							</div>

							<!-- Gallery mode toggle -->
							<div class="flex items-center justify-between">
								<label class="text-sm font-medium text-gray-700">
									Gallery Mode
								</label>
								<button
									@click="emit('gallery-mode-change', !galleryMode)"
									:class="[
										'relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2',
										galleryMode ? 'bg-blue-500' : 'bg-gray-200',
									]"
								>
									<span
										:class="[
											'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
											galleryMode ? 'translate-x-6' : 'translate-x-1',
										]"
									></span>
								</button>
							</div>
						</div>

						<!-- Modal footer with close button -->
						<div class="sticky bottom-0 bg-white border-t border-gray-200 p-4">
							<button
								@click="isModalOpen = false"
								class="w-full px-4 py-2 bg-blue-500 text-white rounded-lg font-medium hover:bg-blue-600 transition-colors"
							>
								Apply Filters
							</button>
						</div>
					</div>
				</Transition>
			</div>
		</Transition>
	</Teleport>
</template>
