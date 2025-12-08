<script setup lang="ts">
import {doc, getDoc, getFirestore} from 'firebase/firestore';
import {computed, onMounted, ref, watch} from 'vue';
import {sortOptions} from '../config/sortOptions';
import type {FiltersState} from '../types/filters';
import FilterInput from './FilterInput.vue';
import FilterSelect from './FilterSelect.vue';

const props = defineProps<{
	filters: FiltersState;
	sort: string;
	currentPage: number;
	canGoNext: boolean;
	canGoPrev: boolean;
	galleryMode: boolean;
}>();

const emit = defineEmits<{
	'update:filters': [FiltersState];
	'update:sort': [string];
	'page-change': [number];
	'update:galleryMode': [boolean];
}>();

const isModalOpen = ref(false);
const totalCount = ref<number | null>(null);
const perPage = 50;

// Local filter state
const localFilters = ref<FiltersState>({...props.filters});

// Sync local filters with props
watch(
	() => props.filters,
	(newFilters) => {
		localFilters.value = {...newFilters};
	},
	{deep: true},
);

// Emit filter updates
function updateRating(
	key: 'provider' | 'min' | 'max',
	value: 'joycaption' | 'minicpm' | number | null,
) {
	const newFilters: FiltersState = {
		...localFilters.value,
		rating: {...localFilters.value.rating},
	};
	if (key === 'provider') {
		newFilters.rating.provider = value as 'joycaption' | 'minicpm';
	} else {
		newFilters.rating[key] = value as number | null;
	}
	emit('update:filters', newFilters);
}

function updateAge(
	key: 'provider' | 'min' | 'max',
	value: 'joycaption' | 'minicpm' | number | null,
) {
	const newFilters: FiltersState = {
		...localFilters.value,
		age: {...localFilters.value.age},
	};
	if (key === 'provider') {
		newFilters.age.provider = value as 'joycaption' | 'minicpm';
	} else {
		newFilters.age[key] = value as number | null;
	}
	emit('update:filters', newFilters);
}

function updateTwitterUser(userId: string | null) {
	const newFilters: FiltersState = {
		...localFilters.value,
		twitterUser: {userId},
	};
	emit('update:filters', newFilters);
}

function updatePixaiTag(value: string | null) {
	const newFilters: FiltersState = {
		...localFilters.value,
		rating: {...localFilters.value.rating},
		age: {...localFilters.value.age},
		twitterUser: {...localFilters.value.twitterUser},
	};
	if (value && localFilters.value.pixaiTag) {
		newFilters.pixaiTag = {
			...localFilters.value.pixaiTag,
			tag: value,
		};
	} else if (value) {
		newFilters.pixaiTag = {
			tag: value,
			category: 'feature',
			confidence: 'high',
		};
	} else {
		newFilters.pixaiTag = null;
	}
	emit('update:filters', newFilters);
}

function updatePixaiCategory(category: 'character' | 'feature' | 'ip' | null) {
	if (category && localFilters.value.pixaiTag) {
		const newFilters: FiltersState = {
			...localFilters.value,
			rating: {...localFilters.value.rating},
			age: {...localFilters.value.age},
			twitterUser: {...localFilters.value.twitterUser},
			pixaiTag: {
				...localFilters.value.pixaiTag,
				category,
			},
		};
		emit('update:filters', newFilters);
	}
}

function updatePixaiConfidence(confidence: 'high' | 'medium' | 'low' | null) {
	if (confidence && localFilters.value.pixaiTag) {
		const newFilters: FiltersState = {
			...localFilters.value,
			rating: {...localFilters.value.rating},
			age: {...localFilters.value.age},
			twitterUser: {...localFilters.value.twitterUser},
			pixaiTag: {
				...localFilters.value.pixaiTag,
				confidence,
			},
		};
		emit('update:filters', newFilters);
	}
}

function clearPixaiFilter() {
	const newFilters: FiltersState = {
		...localFilters.value,
		rating: {...localFilters.value.rating},
		age: {...localFilters.value.age},
		twitterUser: {...localFilters.value.twitterUser},
		pixaiTag: null,
	};
	emit('update:filters', newFilters);
}

// Fetch total count from moderationStats
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
	await fetchTotalCount(props.sort);
});

// Update total count when sort changes
watch(
	() => props.sort,
	async (newSort) => {
		await fetchTotalCount(newSort);
	},
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

// Generate number arrays for min/max options
const ratingOptions = Array.from({length: 11}, (_, i) => i);
const ageOptions = Array.from({length: 18}, (_, i) => i * 5);
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
				<!-- Left side: Filters -->
				<div
					class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3"
				>
					<!-- Sort dropdown -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							Sort:
						</label>
						<select
							:value="sort"
							@change="emit('update:sort', ($event.target as HTMLSelectElement).value)"
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

					<!-- Rating filter -->
					<FilterSelect
						label="Rating"
						:provider="localFilters.rating.provider"
						:min="localFilters.rating.min"
						:max="localFilters.rating.max"
						:min-options="ratingOptions"
						:max-options="ratingOptions"
						@update:provider="(v) => updateRating('provider', v)"
						@update:min="(v) => updateRating('min', v)"
						@update:max="(v) => updateRating('max', v)"
					/>

					<!-- Age filter -->
					<FilterSelect
						label="Age"
						:provider="localFilters.age.provider"
						:min="localFilters.age.min"
						:max="localFilters.age.max"
						:min-options="ageOptions"
						:max-options="ageOptions"
						@update:provider="(v) => updateAge('provider', v)"
						@update:min="(v) => updateAge('min', v)"
						@update:max="(v) => updateAge('max', v)"
					/>

					<!-- Twitter User filter -->
					<FilterInput
						label="Twitter User"
						:model-value="localFilters.twitterUser.userId"
						placeholder="User ID"
						@update:model-value="updateTwitterUser"
					/>

					<!-- PixAI Tag filter -->
					<div class="flex items-center gap-2 min-w-0">
						<label class="text-sm font-medium text-gray-700 shrink-0">
							PixAI Tag:
						</label>
						<input
							:value="localFilters.pixaiTag?.tag || ''"
							@input="updatePixaiTag(($event.target as HTMLInputElement).value)"
							type="text"
							placeholder="Tag name"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent w-28"
						>
						<select
							:value="localFilters.pixaiTag?.category || null"
							@change="updatePixaiCategory(($event.target as HTMLSelectElement).value as 'character' | 'feature' | 'ip' | null)"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
						>
							<option :value="null">Category</option>
							<option value="character">Character</option>
							<option value="feature">Feature</option>
							<option value="ip">IP</option>
						</select>
						<select
							:value="localFilters.pixaiTag?.confidence || null"
							@change="updatePixaiConfidence(($event.target as HTMLSelectElement).value as 'high' | 'medium' | 'low' | null)"
							class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
						>
							<option :value="null">Confidence</option>
							<option value="high">High</option>
							<option value="medium">Medium</option>
							<option value="low">Low</option>
						</select>
						<button
							v-if="localFilters.pixaiTag"
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
							@click="emit('update:galleryMode', !galleryMode)"
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
									:value="sort"
									@change="emit('update:sort', ($event.target as HTMLSelectElement).value)"
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

							<!-- Rating filter -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">
									Rating Filter
								</label>
								<div class="space-y-3">
									<select
										:value="localFilters.rating.provider"
										@change="updateRating('provider', ($event.target as HTMLSelectElement).value as 'joycaption' | 'minicpm')"
										class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
									>
										<option value="minicpm">MiniCPM</option>
										<option value="joycaption">JoyCaption</option>
									</select>
									<div class="grid grid-cols-2 gap-2">
										<select
											:value="localFilters.rating.min"
											@change="updateRating('min', Number(($event.target as HTMLSelectElement).value))"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Min</option>
											<option
												v-for="val in ratingOptions"
												:key="val"
												:value="val"
											>
												{{ val }}
											</option>
										</select>
										<select
											:value="localFilters.rating.max"
											@change="updateRating('max', Number(($event.target as HTMLSelectElement).value))"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Max</option>
											<option
												v-for="val in ratingOptions"
												:key="val"
												:value="val"
											>
												{{ val }}
											</option>
										</select>
									</div>
								</div>
							</div>

							<!-- Age filter -->
							<div class="space-y-2">
								<label class="text-sm font-medium text-gray-700">
									Age Filter
								</label>
								<div class="space-y-3">
									<select
										:value="localFilters.age.provider"
										@change="updateAge('provider', ($event.target as HTMLSelectElement).value as 'joycaption' | 'minicpm')"
										class="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
									>
										<option value="minicpm">MiniCPM</option>
										<option value="joycaption">JoyCaption</option>
									</select>
									<div class="grid grid-cols-2 gap-2">
										<select
											:value="localFilters.age.min"
											@change="updateAge('min', Number(($event.target as HTMLSelectElement).value))"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Min</option>
											<option v-for="val in ageOptions" :key="val" :value="val">
												{{ val }}
											</option>
										</select>
										<select
											:value="localFilters.age.max"
											@change="updateAge('max', Number(($event.target as HTMLSelectElement).value))"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Max</option>
											<option v-for="val in ageOptions" :key="val" :value="val">
												{{ val }}
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
										:value="localFilters.twitterUser.userId || ''"
										@input="updateTwitterUser(($event.target as HTMLInputElement).value)"
										type="text"
										placeholder="Enter Twitter User ID"
										class="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
									>
									<button
										v-if="localFilters.twitterUser.userId"
										@click="updateTwitterUser(null)"
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
											:value="localFilters.pixaiTag?.tag || ''"
											@input="updatePixaiTag(($event.target as HTMLInputElement).value)"
											type="text"
											placeholder="Enter tag name"
											class="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
										<button
											v-if="localFilters.pixaiTag"
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
											:value="localFilters.pixaiTag?.category || null"
											@change="updatePixaiCategory(($event.target as HTMLSelectElement).value as 'character' | 'feature' | 'ip' | null)"
											class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
										>
											<option :value="null">Category</option>
											<option value="character">Character</option>
											<option value="feature">Feature</option>
											<option value="ip">IP</option>
										</select>
										<select
											:value="localFilters.pixaiTag?.confidence || null"
											@change="updatePixaiConfidence(($event.target as HTMLSelectElement).value as 'high' | 'medium' | 'low' | null)"
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
									@click="emit('update:galleryMode', !galleryMode)"
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
