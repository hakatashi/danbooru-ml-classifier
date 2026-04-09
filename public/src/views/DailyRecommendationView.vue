<script setup lang="ts">
import type {User} from 'firebase/auth';
import {
	Calendar,
	ChevronLeft,
	ChevronRight,
	Image as ImageIcon,
	Lock,
} from 'lucide-vue-next';
import {computed, onMounted, onUnmounted, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {
	type ApiImageDocument,
	fetchDailyCounts,
	fetchImages,
	fetchImportantTags,
	fetchInferenceModels,
	getImageUrl,
	type InferenceModel,
} from '../api/mlApi';
import DailyCalendar from '../components/DailyCalendar.vue';
import ImageLightbox from '../components/ImageLightbox.vue';
import {useGallery} from '../composables/useGallery';
import type {ImageDocument} from '../types';

const props = defineProps<{user: User | null}>();

// ── Named sort presets ──────────────────────────────────────────────────────

const NAMED_SORTS = [
	{
		name: 'Aries',
		symbol: '♈',
		field: 'inferences.eva02_twitter_elkan_noto_joblib.score',
	},
	{
		name: 'Taurus',
		symbol: '♉',
		field: 'inferences.deepdanbooru_twitter_biased_svm_joblib.score',
	},
	{
		name: 'Gemini',
		symbol: '♊',
		field: 'inferences.eva02_pixiv_private_nnpu_joblib.score',
	},
	{
		name: 'Cancer',
		symbol: '♋',
		field: 'inferences.pixai_pixiv_private_elkan_noto_joblib.score',
	},
	{
		name: 'Leo',
		symbol: '♌',
		field: 'inferences.deepdanbooru_pixiv_private_elkan_noto_joblib.score',
	},
] as const;

const NAMED_FIELDS: Set<string> = new Set(NAMED_SORTS.map((s) => s.field));

// ── Route / URL param sync ───────────────────────────────────────────────────

const route = useRoute();
const router = useRouter();

function todayString(): string {
	return new Date().toISOString().split('T')[0];
}

const selectedDate = ref(
	typeof route.query.date === 'string' ? route.query.date : todayString(),
);
const selectedSort = ref<string>(
	typeof route.query.sort === 'string'
		? route.query.sort
		: NAMED_SORTS[0].field,
);
const currentPage = ref(
	typeof route.query.page === 'string' ? Number(route.query.page) : 0,
);

function pushUrlParams() {
	router.replace({
		query: {
			date: selectedDate.value,
			sort: selectedSort.value,
			page: currentPage.value > 0 ? String(currentPage.value) : undefined,
		},
	});
}

// Sync from URL when route changes externally
watch(
	() => route.query,
	(q) => {
		if (typeof q.date === 'string' && q.date !== selectedDate.value)
			selectedDate.value = q.date;
		if (typeof q.sort === 'string' && q.sort !== selectedSort.value)
			selectedSort.value = q.sort;
		if (typeof q.page === 'string') {
			const p = Number(q.page);
			if (p !== currentPage.value) currentPage.value = p;
		} else if (currentPage.value !== 0) {
			currentPage.value = 0;
		}
	},
);

// ── State ───────────────────────────────────────────────────────────────────

const images = ref<ApiImageDocument[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const totalCount = ref(0);
const PAGE_SIZE = 50;

// Calendar popup
const showCalendar = ref(false);
const calendarRef = ref<InstanceType<typeof DailyCalendar> | null>(null);
const calendarContainerRef = ref<HTMLElement | null>(null);
const dailyCounts = ref<Record<string, number>>({});
const loadedMonths = ref<Set<string>>(new Set());

// Extra models and tags for dropdown
const extraModels = ref<InferenceModel[]>([]);
const importantTags = ref<{deepdanbooru?: string[]; pixai?: string[]}>({});

// Gallery
const {
	imageAspectRatios,
	galleryRows,
	handleImageLoad,
	calculateGalleryRows,
	getGalleryImageContainerStyle,
	getGalleryImageStyle,
	resetGallery,
} = useGallery();

const lightboxImage = ref<string | null>(null);
const lightboxAlt = ref('');

// ── Computed ────────────────────────────────────────────────────────────────

const totalPages = computed(() => Math.ceil(totalCount.value / PAGE_SIZE));

const isNamedSort = computed(() => NAMED_FIELDS.has(selectedSort.value));

// ── Data loading ────────────────────────────────────────────────────────────

async function loadImages() {
	if (!selectedSort.value || !props.user) return;
	loading.value = true;
	error.value = null;
	try {
		const result = await fetchImages({
			sort_field: selectedSort.value,
			date: selectedDate.value,
			page: currentPage.value,
			limit: PAGE_SIZE,
		});
		images.value = result.images;
		totalCount.value = result.total;
		resetGallery();
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
}

async function loadDailyCounts(month: string) {
	if (!month || loadedMonths.value.has(month)) return;
	loadedMonths.value.add(month);
	try {
		const result = await fetchDailyCounts(month);
		dailyCounts.value = {...dailyCounts.value, ...result.days};
	} catch {
		// ignore
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
				!m.fields.every((f) => NAMED_FIELDS.has(`inferences.${m.key}.${f}`)),
		);
		importantTags.value = tagsResult.tags;
	} catch {
		// ignore
	}
}

// ── Watchers ────────────────────────────────────────────────────────────────

watch([() => props.user, selectedDate, selectedSort, currentPage], loadImages, {
	immediate: true,
});

watch(
	() => calendarRef.value?.currentYearMonth,
	(month) => {
		if (month) loadDailyCounts(month);
	},
);

watch(
	() => images.value,
	(imgs) => {
		if (imgs.length > 0) {
			setTimeout(() => {
				const imgEls = document.querySelectorAll('.daily-gallery-image');
				imgEls.forEach((img) => {
					const el = img as HTMLImageElement;
					if (el.complete && el.naturalWidth > 0) {
						imageAspectRatios.value.set(
							el.alt,
							el.naturalWidth / el.naturalHeight,
						);
					}
				});
				if (imageAspectRatios.value.size === imgs.length) {
					calculateGalleryRows(imgs as unknown as ImageDocument[]);
				}
			}, 100);
		}
	},
	{deep: true},
);

// ── Handlers ────────────────────────────────────────────────────────────────

function selectNamedSort(field: string) {
	selectedSort.value = field;
	currentPage.value = 0;
	pushUrlParams();
}

function onDropdownChange(e: Event) {
	const val = (e.target as HTMLSelectElement).value;
	if (!val) return;
	selectedSort.value = val;
	currentPage.value = 0;
	pushUrlParams();
}

function onDateSelect(date: string) {
	selectedDate.value = date;
	currentPage.value = 0;
	showCalendar.value = false;
	pushUrlParams();
}

function prevPage() {
	if (currentPage.value > 0) {
		currentPage.value--;
		pushUrlParams();
		window.scrollTo({top: 0, behavior: 'smooth'});
	}
}

function nextPage() {
	if (currentPage.value < totalPages.value - 1) {
		currentPage.value++;
		pushUrlParams();
		window.scrollTo({top: 0, behavior: 'smooth'});
	}
}

function onImageLoad(e: Event, imageId: string) {
	handleImageLoad(e, imageId);
	if (imageAspectRatios.value.size === images.value.length) {
		calculateGalleryRows(images.value as unknown as ImageDocument[]);
	}
}

function openLightbox(image: ApiImageDocument) {
	lightboxImage.value = getImageUrl(image);
	lightboxAlt.value = image.id;
}

function closeLightbox() {
	lightboxImage.value = null;
	lightboxAlt.value = '';
}

function handleClickOutside(e: MouseEvent) {
	if (
		calendarContainerRef.value &&
		!calendarContainerRef.value.contains(e.target as Node)
	) {
		showCalendar.value = false;
	}
}

onMounted(async () => {
	document.addEventListener('mousedown', handleClickOutside);
	// Initial URL params
	pushUrlParams();
	await loadMetadata();
	const [y, m] = selectedDate.value.split('-');
	await loadDailyCounts(`${y}-${m}`);
});

onUnmounted(() => {
	document.removeEventListener('mousedown', handleClickOutside);
});

// ── Helpers ─────────────────────────────────────────────────────────────────

function getSortLabel(field: string): string {
	const named = NAMED_SORTS.find((s) => s.field === field);
	if (named) return `${named.symbol} ${named.name}`;
	return field.replace(/^inferences\./, '').replace(/_joblib/, '');
}

function getScoreForCurrentSort(image: ApiImageDocument): number | null {
	const parts = selectedSort.value.split('.');
	if (parts.length >= 3 && parts[0] === 'inferences') {
		const model = parts[1];
		const field = parts[2];
		const inference = image.inferences?.[model];
		if (inference) return (inference as Record<string, number>)[field] ?? null;
	}
	if (parts.length >= 3 && parts[0] === 'importantTagProbs') {
		const feature = parts[1] as 'deepdanbooru' | 'pixai';
		const tag = parts.slice(2).join('.');
		return image.importantTagProbs?.[feature]?.[tag] ?? null;
	}
	return null;
}

function formatScore(score: number | null): string {
	if (score === null) return 'N/A';
	return score.toFixed(3);
}

function getScoreColorClass(score: number | null): string {
	if (score === null) return 'bg-gray-400';
	if (score >= 0.8) return 'bg-purple-600';
	if (score >= 0.6) return 'bg-blue-600';
	if (score >= 0.4) return 'bg-green-600';
	if (score >= 0.2) return 'bg-yellow-600';
	return 'bg-gray-500';
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
				Please login with your Google account to view Daily Recommendations.
			</p>
		</div>

		<!-- Main Content -->
		<template v-else>
			<!-- Filter Bar -->
			<div
				class="sticky top-[72px] z-30 bg-white shadow-sm border-b border-gray-200 px-4 py-3 mb-4"
			>
				<div class="flex flex-wrap items-center gap-3">
					<!-- Date Picker -->
					<div ref="calendarContainerRef" class="relative">
						<button
							type="button"
							@click="showCalendar = !showCalendar"
							class="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-300 hover:border-blue-400 bg-white text-sm font-medium text-gray-700 transition-colors"
						>
							<Calendar :size="16" class="text-gray-500" />
							{{ selectedDate }}
						</button>
						<!-- Calendar Popup -->
						<div v-if="showCalendar" class="absolute top-full left-0 mt-1 z-50">
							<DailyCalendar
								ref="calendarRef"
								:model-value="selectedDate"
								:counts="dailyCounts"
								@update:model-value="onDateSelect"
								@month-change="loadDailyCounts"
							/>
						</div>
					</div>

					<!-- Divider -->
					<div class="w-px h-8 bg-gray-200 hidden sm:block" />

					<!-- Named Sort Buttons -->
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

					<!-- Divider -->
					<div class="w-px h-8 bg-gray-200 hidden sm:block" />

					<!-- Extra Sort Dropdown -->
					<select
						:value="isNamedSort ? '' : selectedSort"
						@change="onDropdownChange"
						class="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white text-gray-700 min-w-[180px]"
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

					<!-- Spacer -->
					<div class="flex-1" />

					<!-- Count + Pagination -->
					<div class="flex items-center gap-2">
						<span class="text-sm text-gray-500"> {{ totalCount }} images </span>
						<div v-if="totalPages > 1" class="flex items-center gap-1">
							<button
								type="button"
								@click="prevPage"
								:disabled="currentPage === 0"
								class="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
							>
								<ChevronLeft :size="16" />
							</button>
							<span class="text-sm text-gray-600 px-1">
								{{ currentPage + 1 }}
								/ {{ totalPages }}
							</span>
							<button
								type="button"
								@click="nextPage"
								:disabled="currentPage >= totalPages - 1"
								class="p-1.5 rounded-lg hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
							>
								<ChevronRight :size="16" />
							</button>
						</div>
					</div>
				</div>

				<!-- Current sort indicator for non-named sorts -->
				<div v-if="!isNamedSort" class="mt-2 text-xs text-gray-500">
					Sorting by:
					<span class="font-mono text-blue-600">{{ selectedSort }}</span>
				</div>
			</div>

			<!-- Loading State -->
			<div
				v-if="loading && images.length === 0"
				class="flex justify-center items-center py-20"
			>
				<div class="flex flex-col items-center gap-4">
					<div
						class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
					/>
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
				v-else-if="!loading && images.length === 0"
				class="bg-white rounded-xl shadow-md p-12 text-center"
			>
				<div
					class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4"
				>
					<ImageIcon :size="32" class="text-gray-400" />
				</div>
				<p class="text-gray-600">No images found for {{ selectedDate }}</p>
			</div>

			<!-- Gallery -->
			<div v-else>
				<!-- Justified rows -->
				<div v-if="galleryRows.length > 0" class="flex flex-col gap-2">
					<div
						v-for="(row, rowIndex) in galleryRows"
						:key="rowIndex"
						class="flex gap-2 justify-center"
					>
						<div
							v-for="image in row"
							:key="image.id"
							class="relative flex-shrink-0 group cursor-pointer overflow-hidden"
							:style="getGalleryImageContainerStyle(image.id, image.height)"
							@click="openLightbox(image)"
						>
							<img
								:src="getImageUrl(image)"
								:alt="image.id"
								class="daily-gallery-image bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
								:style="getGalleryImageStyle(image.id, image.height)"
								loading="lazy"
								@load="(e) => onImageLoad(e, image.id)"
							>
							<div
								class="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors"
							/>
							<div
								class="absolute top-2 left-2 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity z-10"
							>
								<RouterLink
									:to="`/daily/image/${image.id}`"
									target="_blank"
									class="px-2 py-1 bg-black/70 hover:bg-black/90 text-white text-xs rounded-md"
									@click.stop
								>
									Details
								</RouterLink>
							</div>
							<div class="absolute top-2 right-2">
								<div
									:class="[
										getScoreColorClass(getScoreForCurrentSort(image)),
										'px-1.5 py-0.5 rounded text-white font-semibold text-xs shadow opacity-0 group-hover:opacity-100 transition-opacity',
									]"
								>
									{{ getSortLabel(selectedSort) }}:
									{{ formatScore(getScoreForCurrentSort(image)) }}
								</div>
							</div>
						</div>
					</div>
				</div>

				<!-- Pre-load: fixed height before aspect ratios known -->
				<div v-else class="flex flex-wrap justify-center gap-2">
					<div
						v-for="image in images"
						:key="image.id"
						class="relative h-[480px] flex-shrink-0 group cursor-pointer overflow-hidden"
						:style="getGalleryImageContainerStyle(image.id, 480)"
						@click="openLightbox(image)"
					>
						<img
							:src="getImageUrl(image)"
							:alt="image.id"
							class="daily-gallery-image bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
							:style="getGalleryImageStyle(image.id, 480)"
							loading="lazy"
							@load="(e) => onImageLoad(e, image.id)"
						>
						<div
							class="absolute top-2 left-2 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity z-10"
						>
							<RouterLink
								:to="`/daily/image/${image.id}`"
								target="_blank"
								class="px-2 py-1 bg-black/70 hover:bg-black/90 text-white text-xs rounded-md"
								@click.stop
							>
								Details
							</RouterLink>
						</div>
						<div class="absolute top-2 right-2">
							<div
								:class="[
									getScoreColorClass(getScoreForCurrentSort(image)),
									'px-1.5 py-0.5 rounded text-white font-semibold text-xs shadow opacity-0 group-hover:opacity-100 transition-opacity',
								]"
							>
								{{ getSortLabel(selectedSort) }}:
								{{ formatScore(getScoreForCurrentSort(image)) }}
							</div>
						</div>
					</div>
				</div>

				<!-- Bottom Pagination -->
				<div
					v-if="totalPages > 1"
					class="flex justify-center items-center gap-2 mt-6 pb-4"
				>
					<button
						type="button"
						@click="prevPage"
						:disabled="currentPage === 0"
						class="flex items-center gap-1 px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
					>
						<ChevronLeft :size="16" />
						Previous
					</button>
					<span class="text-sm text-gray-600">
						Page {{ currentPage + 1 }} of {{ totalPages }}
					</span>
					<button
						type="button"
						@click="nextPage"
						:disabled="currentPage >= totalPages - 1"
						class="flex items-center gap-1 px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
					>
						Next
						<ChevronRight :size="16" />
					</button>
				</div>
			</div>
		</template>

		<!-- Lightbox -->
		<ImageLightbox
			v-if="lightboxImage"
			:src="lightboxImage"
			:alt="lightboxAlt"
			@close="closeLightbox"
		/>
	</div>
</template>
