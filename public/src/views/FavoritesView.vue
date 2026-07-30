<script setup lang="ts">
import type {User} from 'firebase/auth';
import {
	CheckSquare,
	Heart,
	Image as ImageIcon,
	Lock,
	Square,
	X,
} from 'lucide-vue-next';
import {computed, onMounted, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {
	type ApiImageDocument,
	type FavoriteCategoriesResponse,
	fetchFavoriteCategories,
	fetchFavorites,
	fetchImportantTags,
	fetchInferenceModels,
	getImageUrl,
	type InferenceModel,
} from '../api/mlApi';
import CategoryPicker from '../components/CategoryPicker.vue';
import FavoriteButton from '../components/FavoriteButton.vue';
import ImageLightbox from '../components/ImageLightbox.vue';
import JustifiedGallery from '../components/JustifiedGallery.vue';
import Pagination from '../components/Pagination.vue';
import {useFavorites} from '../composables/useFavorites';
import {NAMED_SORT_FIELDS, NAMED_SORTS} from '../config/namedSorts';

const props = defineProps<{user: User | null}>();

const route = useRoute();
const router = useRouter();

const PAGE_SIZE = 100;

// ── Filter / sort state, synced with the URL ─────────────────────────────────

const selectedSort = ref<string>(
	typeof route.query.sort === 'string'
		? route.query.sort
		: 'favorites.favoritedAt',
);
const sortDir = ref<'asc' | 'desc'>(route.query.dir === 'asc' ? 'asc' : 'desc');
const selectedType = ref<string>(
	typeof route.query.type === 'string' ? route.query.type : '',
);
const selectedCategory = ref<string>(
	typeof route.query.category === 'string' ? route.query.category : '',
);
const dateFrom = ref<string>(
	typeof route.query.from === 'string' ? route.query.from : '',
);
const dateTo = ref<string>(
	typeof route.query.to === 'string' ? route.query.to : '',
);
const includeUndated = ref(route.query.undated === '1');
const groupByCategory = ref(route.query.group === '1');
const currentPage = ref(
	typeof route.query.page === 'string' ? Number(route.query.page) : 0,
);

function pushUrlParams() {
	router.replace({
		query: {
			sort: selectedSort.value,
			dir: sortDir.value,
			type: selectedType.value || undefined,
			category: selectedCategory.value || undefined,
			from: dateFrom.value || undefined,
			to: dateTo.value || undefined,
			undated: includeUndated.value ? '1' : undefined,
			group: groupByCategory.value ? '1' : undefined,
			page: currentPage.value > 0 ? String(currentPage.value) : undefined,
		},
	});
}

// ── Data ──────────────────────────────────────────────────────────────────────

const images = ref<ApiImageDocument[]>([]);
const totalCount = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);

const categoriesResponse = ref<FavoriteCategoriesResponse | null>(null);
const extraModels = ref<InferenceModel[]>([]);
const importantTags = ref<{deepdanbooru?: string[]; pixai?: string[]}>({});

const categorySections = ref<Map<string, ApiImageDocument[]>>(new Map());
const categorySectionsLoading = ref(false);

const {hydrateFromImages, bulkUpdateCategories} = useFavorites();

const isNamedSort = computed(() => NAMED_SORT_FIELDS.has(selectedSort.value));
const totalPages = computed(() => Math.ceil(totalCount.value / PAGE_SIZE));
const canGoNext = computed(() => currentPage.value < totalPages.value - 1);
const canGoPrev = computed(() => currentPage.value > 0);

async function loadFavorites() {
	if (!props.user) return;
	loading.value = true;
	error.value = null;
	try {
		const result = await fetchFavorites({
			sort_field: selectedSort.value,
			sort_dir: sortDir.value,
			type: selectedType.value || undefined,
			category: selectedCategory.value || undefined,
			date_from: dateFrom.value || undefined,
			date_to: dateTo.value || undefined,
			include_undated: includeUndated.value,
			page: currentPage.value,
			limit: PAGE_SIZE,
		});
		images.value = result.images;
		totalCount.value = result.total;
		hydrateFromImages(result.images);
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
}

async function loadCategoriesResponse() {
	if (!props.user) return;
	try {
		categoriesResponse.value = await fetchFavoriteCategories();
	} catch {
		// ignore -- filter chips just won't populate
	}
}

async function loadMetadata() {
	try {
		const [modelsResult, tagsResult] = await Promise.all([
			fetchInferenceModels(),
			fetchImportantTags(),
		]);
		extraModels.value = modelsResult.models.filter(
			(m) =>
				!m.fields.every((f) =>
					NAMED_SORT_FIELDS.has(`inferences.${m.key}.${f}`),
				),
		);
		importantTags.value = tagsResult.tags;
	} catch {
		// ignore
	}
}

const CATEGORY_SECTION_LIMIT = 30;

async function loadCategorySections() {
	if (!categoriesResponse.value) return;
	categorySectionsLoading.value = true;
	try {
		const names = categoriesResponse.value.categories.map((c) => c.name);
		const results = await Promise.all(
			names.map((name) =>
				fetchFavorites({
					sort_field: selectedSort.value,
					sort_dir: sortDir.value,
					type: selectedType.value || undefined,
					category: name,
					date_from: dateFrom.value || undefined,
					date_to: dateTo.value || undefined,
					include_undated: includeUndated.value,
					limit: CATEGORY_SECTION_LIMIT,
				}),
			),
		);
		const map = new Map<string, ApiImageDocument[]>();
		names.forEach((name, i) => {
			map.set(name, results[i].images);
			hydrateFromImages(results[i].images);
		});
		categorySections.value = map;
	} finally {
		categorySectionsLoading.value = false;
	}
}

async function refresh() {
	if (groupByCategory.value) {
		await loadCategoriesResponse();
		await loadCategorySections();
	} else {
		await loadFavorites();
	}
}

watch(
	[
		() => props.user,
		selectedSort,
		sortDir,
		selectedType,
		selectedCategory,
		dateFrom,
		dateTo,
		includeUndated,
		currentPage,
		groupByCategory,
	],
	refresh,
	{immediate: true},
);

onMounted(async () => {
	await loadMetadata();
	await loadCategoriesResponse();
});

// ── Handlers ────────────────────────────────────────────────────────────────

function selectNamedSort(field: string) {
	selectedSort.value = field;
	sortDir.value = 'desc';
	currentPage.value = 0;
	pushUrlParams();
}

function selectPresetSort(field: string, dir: 'asc' | 'desc') {
	selectedSort.value = field;
	sortDir.value = dir;
	currentPage.value = 0;
	pushUrlParams();
}

function onScoreDropdownChange(e: Event) {
	const val = (e.target as HTMLSelectElement).value;
	if (!val) return;
	selectedSort.value = val;
	sortDir.value = 'desc';
	currentPage.value = 0;
	pushUrlParams();
}

function onTypeChange(e: Event) {
	selectedType.value = (e.target as HTMLSelectElement).value;
	currentPage.value = 0;
	pushUrlParams();
}

function selectCategory(name: string) {
	selectedCategory.value = selectedCategory.value === name ? '' : name;
	currentPage.value = 0;
	pushUrlParams();
}

function onDateChange() {
	currentPage.value = 0;
	pushUrlParams();
}

function toggleIncludeUndated() {
	includeUndated.value = !includeUndated.value;
	currentPage.value = 0;
	pushUrlParams();
}

function toggleGroupByCategory() {
	groupByCategory.value = !groupByCategory.value;
	pushUrlParams();
}

function viewCategorySection(name: string) {
	selectedCategory.value = name;
	groupByCategory.value = false;
	currentPage.value = 0;
	pushUrlParams();
}

function handlePageChange(page: number) {
	currentPage.value = page;
	pushUrlParams();
	window.scrollTo({top: 0, behavior: 'smooth'});
}

// ── Selection mode ──────────────────────────────────────────────────────────

const selectionMode = ref(false);
const selectedIds = ref<Set<string>>(new Set());
const bulkActionError = ref<string | null>(null);
const bulkActionPending = ref(false);

function toggleSelectionMode() {
	selectionMode.value = !selectionMode.value;
	if (!selectionMode.value) selectedIds.value = new Set();
}

function toggleSelect(image: ApiImageDocument) {
	const next = new Set(selectedIds.value);
	if (next.has(image.id)) {
		next.delete(image.id);
	} else {
		next.add(image.id);
	}
	selectedIds.value = next;
}

function selectAllOnPage() {
	selectedIds.value = new Set(images.value.map((img) => img.id));
}

function clearSelection() {
	selectedIds.value = new Set();
}

async function applyBulkOp(op: 'add' | 'remove' | 'set', categories: string[]) {
	if (selectedIds.value.size === 0) return;
	bulkActionPending.value = true;
	bulkActionError.value = null;
	try {
		const {notFound} = await bulkUpdateCategories(
			[...selectedIds.value],
			op,
			categories,
		);
		if (notFound.length > 0) {
			bulkActionError.value = `${notFound.length} image(s) could not be found`;
		}
		await refresh();
		await loadCategoriesResponse();
	} catch (e) {
		bulkActionError.value = (e as Error).message;
	} finally {
		bulkActionPending.value = false;
	}
}

async function unfavoriteSelected() {
	if (selectedIds.value.size === 0) return;
	if (
		!window.confirm(
			`Remove ${selectedIds.value.size} image(s) from favorites entirely?`,
		)
	)
		return;
	await applyBulkOp('set', []);
	clearSelection();
}

// ── Lightbox with prev/next ──────────────────────────────────────────────────
// The lightbox navigates within whichever array the clicked image came from
// -- the flat `images` page, or a single category's `sectionImages` when
// grouped by category (grouped mode never populates `images`).

const lightboxIndex = ref<number | null>(null);
const lightboxSourceImages = ref<ApiImageDocument[]>([]);

function openLightbox(image: ApiImageDocument, sourceList: ApiImageDocument[]) {
	lightboxSourceImages.value = sourceList;
	lightboxIndex.value = sourceList.findIndex((img) => img.id === image.id);
}

function closeLightbox() {
	lightboxIndex.value = null;
}

const lightboxImage = computed(() =>
	lightboxIndex.value !== null
		? lightboxSourceImages.value[lightboxIndex.value]
		: null,
);
const lightboxCanPrev = computed(
	() => lightboxIndex.value !== null && lightboxIndex.value > 0,
);
const lightboxCanNext = computed(
	() =>
		lightboxIndex.value !== null &&
		lightboxIndex.value < lightboxSourceImages.value.length - 1,
);

function lightboxPrev() {
	if (lightboxIndex.value !== null && lightboxIndex.value > 0) {
		lightboxIndex.value--;
	}
}

function lightboxNext() {
	if (
		lightboxIndex.value !== null &&
		lightboxIndex.value < lightboxSourceImages.value.length - 1
	) {
		lightboxIndex.value++;
	}
}

function handleImageClick(
	image: ApiImageDocument,
	sourceList: ApiImageDocument[],
) {
	if (selectionMode.value) {
		toggleSelect(image);
	} else {
		openLightbox(image, sourceList);
	}
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
				<Lock :size="40" class="text-blue-500" />
			</div>
			<h2 class="text-2xl font-bold text-gray-900 mb-3">
				Authentication Required
			</h2>
			<p class="text-gray-600">
				Please login with your Google account to view Favorites.
			</p>
		</div>

		<template v-else>
			<!-- Filter Bar -->
			<div
				class="sticky top-[72px] z-30 bg-white shadow-sm border-b border-gray-200 px-4 py-3 mb-4"
			>
				<div class="flex flex-wrap items-center gap-3">
					<!-- Sort presets -->
					<div class="flex flex-wrap items-center gap-1.5">
						<button
							type="button"
							@click="selectPresetSort('favorites.favoritedAt', 'desc')"
							:class="[
								'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
								selectedSort === 'favorites.favoritedAt' && sortDir === 'desc'
									? 'bg-blue-600 text-white shadow-sm'
									: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
							]"
						>
							Newest
						</button>
						<button
							type="button"
							@click="selectPresetSort('favorites.favoritedAt', 'asc')"
							:class="[
								'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
								selectedSort === 'favorites.favoritedAt' && sortDir === 'asc'
									? 'bg-blue-600 text-white shadow-sm'
									: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
							]"
						>
							Oldest
						</button>
						<button
							type="button"
							@click="selectPresetSort('favorites.updatedAt', 'desc')"
							:class="[
								'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
								selectedSort === 'favorites.updatedAt'
									? 'bg-blue-600 text-white shadow-sm'
									: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
							]"
						>
							Recently Updated
						</button>
					</div>

					<div class="w-px h-8 bg-gray-200 hidden sm:block" />

					<!-- Named score sort presets -->
					<div class="flex flex-wrap items-center gap-1.5">
						<button
							v-for="preset in NAMED_SORTS"
							:key="preset.field"
							type="button"
							@click="selectNamedSort(preset.field)"
							:class="[
								'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
								selectedSort === preset.field
									? 'bg-blue-600 text-white shadow-sm'
									: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
							]"
							:title="preset.field"
						>
							{{ preset.symbol }} {{ preset.name }}
						</button>
					</div>

					<!-- Other score sort fields -->
					<select
						:value="isNamedSort ? '' : selectedSort"
						@change="onScoreDropdownChange"
						class="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white text-gray-700 min-w-[160px]"
					>
						<option value="">Other sort fields...</option>
						<optgroup
							v-if="extraModels.length > 0"
							label="─── Inference Models ───"
						>
							<optgroup
								v-for="model in extraModels"
								:key="model.key"
								:label="model.key"
							>
								<option
									v-for="field in model.fields"
									:key="field"
									:value="`inferences.${model.key}.${field}`"
								>
									{{ field }}
								</option>
							</optgroup>
						</optgroup>
						<optgroup
							v-if="importantTags.deepdanbooru?.length"
							label="─── DeepDanbooru Tags ───"
						>
							<option
								v-for="tag in importantTags.deepdanbooru"
								:key="tag"
								:value="`importantTagProbs.deepdanbooru.${tag}`"
							>
								{{ tag }}
							</option>
						</optgroup>
						<optgroup
							v-if="importantTags.pixai?.length"
							label="─── PixAI Tags ───"
						>
							<option
								v-for="tag in importantTags.pixai"
								:key="tag"
								:value="`importantTagProbs.pixai.${tag}`"
							>
								{{ tag }}
							</option>
						</optgroup>
					</select>

					<div class="w-px h-8 bg-gray-200 hidden sm:block" />

					<!-- Type filter -->
					<select
						:value="selectedType"
						@change="onTypeChange"
						class="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white text-gray-700"
					>
						<option value="">All sources</option>
						<option
							v-for="t in categoriesResponse?.types ?? []"
							:key="t.name"
							:value="t.name"
						>
							{{ t.name }}
							({{ t.count }})
						</option>
					</select>

					<!-- Date range -->
					<div class="flex items-center gap-1.5">
						<input
							v-model="dateFrom"
							type="date"
							class="text-sm border border-gray-300 rounded-lg px-2 py-1.5"
							@change="onDateChange"
						>
						<span class="text-gray-400 text-sm">–</span>
						<input
							v-model="dateTo"
							type="date"
							class="text-sm border border-gray-300 rounded-lg px-2 py-1.5"
							@change="onDateChange"
						>
					</div>
					<label
						v-if="dateFrom || dateTo"
						class="flex items-center gap-1.5 text-xs text-gray-600"
					>
						<input
							type="checkbox"
							:checked="includeUndated"
							@change="toggleIncludeUndated"
							class="rounded border-gray-300"
						>
						Include undated
					</label>

					<div class="w-px h-8 bg-gray-200 hidden sm:block" />

					<!-- Group toggle -->
					<button
						type="button"
						@click="toggleGroupByCategory"
						:class="[
							'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
							groupByCategory
								? 'bg-blue-600 text-white shadow-sm'
								: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
						]"
					>
						Group by category
					</button>

					<!-- Selection mode toggle -->
					<button
						type="button"
						@click="toggleSelectionMode"
						:class="[
							'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
							selectionMode
								? 'bg-blue-600 text-white shadow-sm'
								: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
						]"
					>
						<CheckSquare v-if="selectionMode" :size="16" />
						<Square v-else :size="16" />
						Select
					</button>

					<!-- Spacer -->
					<div class="flex-1" />

					<span class="text-sm text-gray-500">{{ totalCount }} favorites</span>
				</div>

				<!-- Category chips -->
				<div
					v-if="categoriesResponse && categoriesResponse.categories.length > 0"
					class="flex flex-wrap items-center gap-1.5 mt-2"
				>
					<button
						type="button"
						@click="selectCategory('')"
						:class="[
							'px-2.5 py-1 rounded-full text-xs font-medium transition-colors',
							selectedCategory === ''
								? 'bg-purple-600 text-white'
								: 'bg-purple-50 text-purple-700 hover:bg-purple-100',
						]"
					>
						All ({{ categoriesResponse.total }})
					</button>
					<button
						v-for="cat in categoriesResponse.categories"
						:key="cat.name"
						type="button"
						@click="selectCategory(cat.name)"
						:class="[
							'px-2.5 py-1 rounded-full text-xs font-medium transition-colors',
							selectedCategory === cat.name
								? 'bg-purple-600 text-white'
								: 'bg-purple-50 text-purple-700 hover:bg-purple-100',
						]"
					>
						{{ cat.name }}
						({{ cat.count }})
					</button>
				</div>
			</div>

			<!-- Loading -->
			<div
				v-if="(loading || categorySectionsLoading) && images.length === 0 && categorySections.size === 0"
				class="flex justify-center items-center py-20"
			>
				<div class="flex flex-col items-center gap-4">
					<div
						class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
					/>
					<p class="text-gray-600">Loading favorites...</p>
				</div>
			</div>

			<!-- Error -->
			<div
				v-else-if="error"
				class="bg-red-50 border border-red-200 rounded-xl p-6 text-center"
			>
				<p class="text-red-700">Error: {{ error }}</p>
			</div>

			<!-- Empty -->
			<div
				v-else-if="!groupByCategory && !loading && images.length === 0"
				class="bg-white rounded-xl shadow-md p-12 text-center"
			>
				<div
					class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4"
				>
					<ImageIcon :size="32" class="text-gray-400" />
				</div>
				<p class="text-gray-600">No favorites found</p>
			</div>

			<!-- Grouped by category -->
			<div v-else-if="groupByCategory" class="space-y-8">
				<div
					v-for="[ name, sectionImages ] in categorySections"
					:key="name"
					v-show="sectionImages.length > 0"
				>
					<div
						class="flex items-center gap-2 mb-2 sticky top-[136px] bg-gray-50/95 backdrop-blur-sm py-1.5 z-20"
					>
						<h3 class="font-semibold text-gray-900">{{ name }}</h3>
						<button
							type="button"
							class="text-xs text-blue-600 hover:underline"
							@click="viewCategorySection(name)"
						>
							View all →
						</button>
					</div>
					<JustifiedGallery
						:images="sectionImages"
						:selectable="selectionMode"
						:selected-ids="selectedIds"
						@image-click="(img) => handleImageClick(img, sectionImages)"
						@toggle-select="toggleSelect"
					>
						<template #overlay-top-left="{image}">
							<FavoriteButton
								:image-id="image.id"
								:size="14"
								variant="overlay"
							/>
						</template>
						<template #overlay-top-right="{image}">
							<CategoryPicker
								:categories="categoriesResponse?.categories ?? []"
								:initial-selected="image.favorites?.categories ?? []"
								align="right"
								@apply="
									(cats) =>
										bulkUpdateCategories([image.id], 'set', cats).then(refresh)
								"
							/>
						</template>
					</JustifiedGallery>
				</div>
			</div>

			<!-- Flat gallery -->
			<div v-else>
				<JustifiedGallery
					:images="images"
					:selectable="selectionMode"
					:selected-ids="selectedIds"
					@image-click="(img) => handleImageClick(img, images)"
					@toggle-select="toggleSelect"
				>
					<template #overlay-top-left="{image}">
						<FavoriteButton :image-id="image.id" :size="14" variant="overlay" />
					</template>
					<template #overlay-top-right="{image}">
						<CategoryPicker
							:categories="categoriesResponse?.categories ?? []"
							:initial-selected="image.favorites?.categories ?? []"
							align="right"
							@apply="
								(cats) =>
									bulkUpdateCategories([image.id], 'set', cats).then(refresh)
							"
						/>
					</template>
				</JustifiedGallery>

				<Pagination
					v-if="totalPages > 1"
					:current-page="currentPage"
					:can-go-next="canGoNext"
					:can-go-prev="canGoPrev"
					@page-change="handlePageChange"
				/>
			</div>
		</template>

		<!-- Bulk selection action bar -->
		<div
			v-if="selectionMode && selectedIds.size > 0"
			class="fixed bottom-0 inset-x-0 z-40 bg-white border-t border-gray-200 shadow-lg px-4 py-3"
		>
			<div class="flex flex-wrap items-center gap-3 max-w-5xl mx-auto">
				<span class="text-sm font-medium text-gray-700">
					{{ selectedIds.size }}
					selected
				</span>
				<button
					type="button"
					class="text-xs text-blue-600 hover:underline"
					@click="selectAllOnPage"
				>
					Select all on page
				</button>
				<button
					type="button"
					class="text-xs text-gray-500 hover:underline"
					@click="clearSelection"
				>
					Clear
				</button>

				<div class="flex-1" />

				<CategoryPicker
					:categories="categoriesResponse?.categories ?? []"
					trigger-label="Add category…"
					align="right"
					@apply="(cats) => applyBulkOp('add', cats)"
				/>
				<CategoryPicker
					:categories="categoriesResponse?.categories ?? []"
					trigger-label="Remove category…"
					align="right"
					@apply="(cats) => applyBulkOp('remove', cats)"
				/>
				<CategoryPicker
					:categories="categoriesResponse?.categories ?? []"
					trigger-label="Set categories…"
					align="right"
					@apply="(cats) => applyBulkOp('set', cats)"
				/>
				<button
					type="button"
					:disabled="bulkActionPending"
					class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50"
					@click="unfavoriteSelected"
				>
					<Heart :size="13" />
					Unfavorite
				</button>
				<button
					type="button"
					class="p-1.5 rounded-lg hover:bg-gray-100"
					title="Exit selection mode"
					@click="toggleSelectionMode"
				>
					<X :size="16" />
				</button>
			</div>
			<p
				v-if="bulkActionError"
				class="text-xs text-red-600 mt-1 max-w-5xl mx-auto"
			>
				{{ bulkActionError }}
			</p>
		</div>

		<!-- Lightbox -->
		<ImageLightbox
			v-if="lightboxImage"
			:src="getImageUrl(lightboxImage, false)"
			:alt="lightboxImage.id"
			:can-prev="lightboxCanPrev"
			:can-next="lightboxCanNext"
			@close="closeLightbox"
			@prev="lightboxPrev"
			@next="lightboxNext"
		/>
	</div>
</template>
