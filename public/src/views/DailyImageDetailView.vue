<script setup lang="ts">
import type {User} from 'firebase/auth';
import {ChevronLeft, ExternalLink, Heart, Lock} from 'lucide-vue-next';
import {computed, onMounted, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {
	type ApiImageDocument,
	fetchImageById,
	fetchPostSource,
	fetchSimilarImages,
	getImageUrl,
	type SimilarImage,
} from '../api/mlApi';
import ImageLightbox from '../components/ImageLightbox.vue';
import ThinkBlock from '../components/ThinkBlock.vue';
import {useImages} from '../composables/useImages';
import type {TagData, TagList} from '../types';

type ConfidenceLevel = 'high' | 'medium' | 'low';
type TagCategory = 'character' | 'feature' | 'ip';

const props = defineProps<{user: User | null}>();

const NAMED_SORTS = [
	{
		name: 'Aries',
		symbol: '♈',
		field: 'inferences.eva02_twitter_elkan_noto_joblib.score',
		modelKey: 'eva02_twitter_elkan_noto_joblib',
	},
	{
		name: 'Taurus',
		symbol: '♉',
		field: 'inferences.deepdanbooru_twitter_biased_svm_joblib.score',
		modelKey: 'deepdanbooru_twitter_biased_svm_joblib',
	},
	{
		name: 'Gemini',
		symbol: '♊',
		field: 'inferences.eva02_pixiv_private_nnpu_joblib.score',
		modelKey: 'eva02_pixiv_private_nnpu_joblib',
	},
	{
		name: 'Cancer',
		symbol: '♋',
		field: 'inferences.pixai_pixiv_private_elkan_noto_joblib.score',
		modelKey: 'pixai_pixiv_private_elkan_noto_joblib',
	},
	{
		name: 'Leo',
		symbol: '♌',
		field: 'inferences.deepdanbooru_pixiv_private_elkan_noto_joblib.score',
		modelKey: 'deepdanbooru_pixiv_private_elkan_noto_joblib',
	},
] as const;

const route = useRoute();
const router = useRouter();

const {toggleFavorite, isFavorite, loadFavoritesForImages} = useImages();
const savingFavoriteIds = ref<Set<string>>(new Set());

async function handleToggleFavorite(event: Event, imageId: string) {
	event.stopPropagation();
	event.preventDefault();
	if (savingFavoriteIds.value.has(imageId)) return;
	savingFavoriteIds.value.add(imageId);
	try {
		await toggleFavorite(imageId);
	} catch (err) {
		console.error('Failed to toggle favorite:', err);
	} finally {
		savingFavoriteIds.value.delete(imageId);
	}
}

const image = ref<ApiImageDocument | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const showLightbox = ref(false);
const tagConfidenceFilter = ref<ConfidenceLevel>('medium');

const imageUrl = computed(() => (image.value ? getImageUrl(image.value) : ''));

const captionModels = computed(() => Object.keys(image.value?.captions ?? {}));

// Sort all inference scores descending
const sortedInferences = computed(() => {
	const inf = image.value?.inferences;
	if (!inf) return [];
	return Object.entries(inf)
		.flatMap(([modelKey, scores]) =>
			Object.entries(scores).map(([field, score]) => {
				const sortField = `inferences.${modelKey}.${field}`;
				return {
					modelKey,
					field,
					rank:
						image.value?.scoreRanks?.[sortField]?.rank ??
						Number.POSITIVE_INFINITY,
					sortField,
					score,
				};
			}),
		)
		.sort((a, b) => a.rank - b.rank);
});

// Important tag probs sorted by value descending
const deepdanbooruTags = computed(() => {
	const probs = image.value?.importantTagProbs?.deepdanbooru;
	if (!probs) return [];
	return Object.entries(probs).sort(([, a], [, b]) => b - a);
});

const pixaiTags = computed(() => {
	const probs = image.value?.importantTagProbs?.pixai;
	if (!probs) return [];
	return Object.entries(probs).sort(([, a], [, b]) => b - a);
});

function getNamedScore(modelKey: string): number | null {
	return (image.value?.inferences?.[modelKey]?.score as number) ?? null;
}

function getNamedRank(
	modelKey: string,
): {rank: number | null; total: number} | null {
	const sortField = `inferences.${modelKey}.score`;
	return image.value?.scoreRanks?.[sortField] ?? null;
}

function getScoreBarWidth(value: number): string {
	return `${Math.max(0, Math.min(100, value * 100)).toFixed(1)}%`;
}

function getScoreColorClass(value: number): string {
	if (value >= 0.8) return 'bg-purple-500';
	if (value >= 0.6) return 'bg-blue-500';
	if (value >= 0.4) return 'bg-green-500';
	if (value >= 0.2) return 'bg-yellow-500';
	return 'bg-gray-400';
}

/** Bar width based on rank: rank 1 → 100%, rank=total → ~0% */
function getRankBarWidth(rank: number | null, total: number): string {
	if (rank === null || total === 0) return '0%';
	return `${(((total - rank + 1) / total) ** 15 * 100).toFixed(1)}%`;
}

/** Color based on rank percentile (lower rank = better) */
function getRankColorClass(rank: number | null, total: number): string {
	if (rank === null || total === 0) return 'bg-gray-400';
	const pct = 1 - (1 - rank / total) ** 15;
	if (pct <= 0.2) return 'bg-purple-500';
	if (pct <= 0.4) return 'bg-blue-500';
	if (pct <= 0.6) return 'bg-green-500';
	if (pct <= 0.8) return 'bg-yellow-500';
	return 'bg-gray-400';
}

function getTagOpacity(confidence: ConfidenceLevel): string {
	switch (confidence) {
		case 'high':
			return 'opacity-100';
		case 'medium':
			return 'opacity-75';
		case 'low':
			return 'opacity-50';
	}
}

const tagCategoryOrder: Record<TagCategory, number> = {
	character: 1,
	ip: 2,
	feature: 3,
};

const confidenceOrder: Record<ConfidenceLevel, number> = {
	high: 1,
	medium: 2,
	low: 3,
};

function getFilteredTags(tagData: TagData) {
	const tags: Array<{
		name: string;
		category: TagCategory;
		confidence: ConfidenceLevel;
		score?: number;
	}> = [];

	const confidenceLevels: ConfidenceLevel[] =
		tagConfidenceFilter.value === 'high'
			? ['high']
			: tagConfidenceFilter.value === 'medium'
				? ['high', 'medium']
				: ['high', 'medium', 'low'];

	const tagSet = new Set<string>();

	for (const confidence of confidenceLevels) {
		const confidenceKey = `${confidence}_confidence` as keyof TagList;
		const tagList = tagData.tag_list[confidenceKey];

		for (const category of ['character', 'ip', 'feature'] as TagCategory[]) {
			const categoryTags = tagList?.[category];
			for (const tagName in categoryTags) {
				const score =
					category === 'character'
						? tagData.raw_scores.character[tagName]
						: category === 'feature'
							? tagData.raw_scores.feature[tagName]
							: undefined;

				if (!tagSet.has(tagName)) {
					tagSet.add(tagName);
					tags.push({name: tagName, category, confidence, score});
				}
			}
		}
	}

	tags.sort((a, b) => {
		if (a.category !== b.category)
			return tagCategoryOrder[a.category] - tagCategoryOrder[b.category];
		if (a.confidence !== b.confidence)
			return confidenceOrder[a.confidence] - confidenceOrder[b.confidence];
		return a.name.localeCompare(b.name);
	});

	return tags;
}

function getRatingColorClass(rating: number | null): string {
	if (rating === null) return 'bg-gray-500';
	if (rating <= 2) return 'bg-green-500';
	if (rating <= 4) return 'bg-lime-500';
	if (rating <= 6) return 'bg-orange-500';
	if (rating <= 8) return 'bg-red-500';
	return 'bg-purple-500';
}

function getRatingLabel(rating: number | null): string {
	if (rating === null) return 'Unknown';
	if (rating <= 2) return 'Safe';
	if (rating <= 4) return 'Slightly Suggestive';
	if (rating <= 6) return 'Sensitive';
	if (rating <= 8) return 'Adult';
	return 'Explicit';
}

function getModelDisplayName(key: string): string {
	const named = NAMED_SORTS.find((s) => s.modelKey === key);
	if (named) return `${named.symbol} ${named.name}`;
	return key;
}

// External source URL from image key — pixiv/sankaku/twitter only
const sourceUrl = computed((): string | null => {
	const key = image.value?.key;
	if (!key) return null;
	const parts = key.split('/');
	if (parts.length < 2) return null;
	const provider = parts[0];
	const filename = parts[parts.length - 1];
	const stem = filename.replace(/\.[^.]+$/, '');

	if (provider === 'pixiv') {
		const id = stem.replace(/(-[0-9a-f]{32})?_p\d+$/, '');
		return `https://www.pixiv.net/artworks/${id}`;
	}
	if (provider === 'sankaku')
		return `https://chan.sankakucomplex.com/ja/posts/${stem}`;
	if (provider === 'twitter' && image.value?.source?.tweetId)
		return `https://twitter.com/i/web/status/${image.value.source.tweetId}`;
	return null;
});

// For danbooru/gelbooru: fetch source URL from API, fall back to post page
const imageProvider = computed(
	(): string => image.value?.key?.split('/')[0] ?? '',
);
const imageStem = computed((): string => {
	const parts = image.value?.key?.split('/') ?? [];
	if (parts.length < 2) return '';
	return parts[parts.length - 1].replace(/\.[^.]+$/, '');
});

function getPostPageUrl(
	provider: 'danbooru' | 'gelbooru',
	stem: string,
): string {
	if (provider === 'danbooru')
		return `https://danbooru.donmai.us/posts/${stem}`;
	return `https://gelbooru.com/index.php?page=post&s=view&id=${stem}`;
}

const sourceLoading = ref(false);

async function openViewSource(): Promise<void> {
	const provider = imageProvider.value;
	const stem = imageStem.value;
	if (provider !== 'danbooru' && provider !== 'gelbooru') return;

	const fallback = getPostPageUrl(provider as 'danbooru' | 'gelbooru', stem);
	sourceLoading.value = true;
	try {
		const source = await fetchPostSource(
			provider as 'danbooru' | 'gelbooru',
			stem,
		);
		const url =
			source !== null &&
			(source.startsWith('http://') || source.startsWith('https://'))
				? source
				: fallback;
		window.open(url, '_blank', 'noopener,noreferrer');
	} catch {
		window.open(fallback, '_blank', 'noopener,noreferrer');
	} finally {
		sourceLoading.value = false;
	}
}

// Link to daily list for a sort field + current date
function dailyLink(sortField: string): string {
	const date = image.value?.date ?? '';
	return `/daily?date=${date}&sort=${encodeURIComponent(sortField)}`;
}

function goBack() {
	if (window.history.length > 1) {
		router.back();
	} else {
		router.push('/daily');
	}
}

const similarImages = ref<SimilarImage[]>([]);
const similarLoading = ref(false);
const similarError = ref<string | null>(null);

async function loadSimilarImages(id: string) {
	similarLoading.value = true;
	similarError.value = null;
	try {
		const result = await fetchSimilarImages(id, {
			limit: 20,
			status: 'inferred',
		});
		similarImages.value = result.similar;
		if (result.similar.length > 0) {
			await loadFavoritesForImages(result.similar.map((sim) => sim.id));
		}
	} catch (e) {
		similarError.value = (e as Error).message;
	} finally {
		similarLoading.value = false;
	}
}

async function loadImage(id: string) {
	loading.value = true;
	error.value = null;
	image.value = null;
	similarImages.value = [];
	try {
		image.value = await fetchImageById(id);
		await loadFavoritesForImages([id]);
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
	loadSimilarImages(id);
}

// Re-fetch when navigating between similar images (component is reused)
watch(
	() => route.params.id as string,
	(id) => {
		if (id) loadImage(id);
	},
);

onMounted(() => {
	loadImage(route.params.id as string);
});

function onSimilarWheel(e: WheelEvent) {
	const el = e.currentTarget as HTMLElement;
	el.scrollLeft += e.deltaY;
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
				Please login with your Google account to view Daily Recommendation
				details.
			</p>
		</div>

		<!-- Main Content -->
		<template v-else>
			<!-- Back link -->
			<button
				type="button"
				@click="goBack"
				class="inline-flex items-center gap-2 text-gray-600 hover:text-gray-900 mb-6 transition-colors"
			>
				<ChevronLeft :size="20" />
				Back to Daily Recommendation
			</button>

			<!-- Loading -->
			<div v-if="loading" class="flex justify-center items-center py-20">
				<div
					class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
				/>
			</div>

			<!-- Error -->
			<div
				v-else-if="error"
				class="bg-red-50 border border-red-200 rounded-xl p-6 text-center"
			>
				<p class="text-red-700">{{ error }}</p>
			</div>

			<!-- Content -->
			<div v-else-if="image" class="space-y-6">
				<!-- Top section: Image + Named Scores -->
				<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
					<!-- Image -->
					<div class="lg:col-span-2">
						<div
							class="bg-black rounded-xl overflow-hidden cursor-pointer"
							@click="showLightbox = true"
						>
							<img
								:src="imageUrl"
								:alt="image.key"
								class="w-full h-auto max-h-[75vh] object-contain mx-auto"
							>
						</div>
						<!-- Favorite + Source links below image -->
						<div class="mt-2 flex items-center justify-between">
							<!-- Favorite button -->
							<button
								type="button"
								@click="(e) => handleToggleFavorite(e, image.id)"
								:disabled="savingFavoriteIds.has(image.id)"
								:class="[
								'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
								isFavorite(image.id)
									? 'bg-red-500 hover:bg-red-600 text-white'
									: 'bg-white border border-gray-300 hover:border-red-400 hover:text-red-500 text-gray-700',
							]"
							>
								<Heart
									:size="15"
									:fill="isFavorite(image.id) ? 'currentColor' : 'none'"
								/>
								{{ isFavorite(image.id) ? 'Favorited' : 'Favorite' }}
							</button>
						</div>
						<div
							v-if="imageProvider === 'danbooru' || imageProvider === 'gelbooru' || sourceUrl"
							class="mt-2 flex justify-end"
						>
							<button
								v-if="imageProvider === 'danbooru' || imageProvider === 'gelbooru'"
								type="button"
								:disabled="sourceLoading"
								class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-white text-xs rounded-lg transition-colors disabled:opacity-50 disabled:cursor-wait"
								@click="openViewSource"
							>
								<ExternalLink :size="13" />
								View Source ({{ image.type }})
							</button>
							<a
								v-else
								:href="sourceUrl ?? ''"
								target="_blank"
								rel="noopener noreferrer"
								class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-white text-xs rounded-lg transition-colors"
							>
								<ExternalLink :size="13" />
								View Source ({{ image.type }})
							</a>
						</div>
					</div>

					<!-- Right: Named Scores + Metadata -->
					<div class="space-y-4">
						<!-- Named sort scores -->
						<div class="bg-white rounded-xl shadow-md p-4">
							<h2 class="text-base font-semibold text-gray-900 mb-3">
								Recommendation Scores
							</h2>
							<div class="space-y-3">
								<RouterLink
									v-for="preset in NAMED_SORTS"
									:key="preset.modelKey"
									:to="dailyLink(preset.field)"
									class="block space-y-1 rounded-lg p-1.5 -mx-1.5 hover:bg-gray-50 transition-colors group"
									:title="`Open daily list sorted by ${preset.name}`"
								>
									<div class="flex items-center justify-between text-sm">
										<span
											class="font-medium text-gray-700 group-hover:text-blue-600 transition-colors"
										>
											{{ preset.symbol }} {{ preset.name }}
										</span>
										<div class="flex items-center gap-2">
											<!-- Rank badge -->
											<template v-if="getNamedRank(preset.modelKey)">
												<span class="text-xs text-gray-500 tabular-nums">
													#{{ getNamedRank(preset.modelKey)?.rank }}
													/ {{ getNamedRank(preset.modelKey)?.total }}
												</span>
											</template>
											<span class="font-mono text-gray-900 tabular-nums">
												{{ getNamedScore(preset.modelKey)?.toFixed(4) ?? '—' }}
											</span>
										</div>
									</div>
									<div class="w-full bg-gray-100 rounded-full h-2">
										<template v-if="getNamedRank(preset.modelKey)">
											<div
												:class="[
												'h-2 rounded-full transition-all',
												getRankColorClass(
													getNamedRank(preset.modelKey)?.rank ?? null,
													getNamedRank(preset.modelKey)?.total ?? 0,
												),
											]"
												:style="{
												width: getRankBarWidth(
													getNamedRank(preset.modelKey)?.rank ?? null,
													getNamedRank(preset.modelKey)?.total ?? 0,
												),
											}"
											/>
										</template>
									</div>
								</RouterLink>
							</div>
						</div>

						<!-- Image Metadata -->
						<div class="bg-white rounded-xl shadow-md p-4">
							<h2 class="text-base font-semibold text-gray-900 mb-3">
								Metadata
							</h2>
							<dl class="space-y-2 text-sm">
								<div class="flex justify-between">
									<dt class="text-gray-500">Type</dt>
									<dd class="font-medium text-gray-900 capitalize">
										{{ image.type }}
									</dd>
								</div>
								<div class="flex justify-between">
									<dt class="text-gray-500">Date</dt>
									<dd class="font-medium text-gray-900">
										{{ image.date ?? '—' }}
									</dd>
								</div>
								<div v-if="image.postId" class="flex justify-between">
									<dt class="text-gray-500">Post ID</dt>
									<dd class="font-mono text-gray-900 text-xs">
										{{ image.postId }}
									</dd>
								</div>
								<div class="flex justify-between">
									<dt class="text-gray-500">Status</dt>
									<dd class="capitalize text-gray-900">{{ image.status }}</dd>
								</div>
								<div class="pt-1 border-t border-gray-100">
									<dt class="text-gray-500 mb-0.5">MongoDB ID</dt>
									<dd class="font-mono text-xs text-gray-700 break-all">
										{{ image.id }}
									</dd>
								</div>
							</dl>
						</div>

						<!-- Similar Images -->
						<div class="bg-white rounded-xl shadow-md p-4">
							<h2 class="text-base font-semibold text-gray-900 mb-3">
								Similar Images
							</h2>

							<!-- Loading -->
							<div
								v-if="similarLoading"
								class="flex items-center gap-2 text-sm text-gray-500 py-4"
							>
								<div
									class="animate-spin rounded-full h-4 w-4 border-2 border-blue-500 border-t-transparent"
								/>
								Searching for similar images...
							</div>

							<!-- Error -->
							<p v-else-if="similarError" class="text-sm text-red-500 py-2">
								{{ similarError }}
							</p>

							<!-- No results -->
							<p
								v-else-if="similarImages.length === 0"
								class="text-sm text-gray-400 py-2"
							>
								No similar images found. (Qdrant index may not include this
								image yet.)
							</p>

							<!-- Thumbnail strip -->
							<div
								v-else
								class="flex gap-2 overflow-x-auto pb-2"
								@wheel.prevent="onSimilarWheel"
							>
								<RouterLink
									v-for="sim in similarImages"
									:key="sim.id"
									:to="`/daily/image/${sim.id}`"
									class="flex-shrink-0 relative group"
									:title="`Similarity: ${(sim.similarity * 100).toFixed(1)}%`"
								>
									<img
										:src="getImageUrl(sim, true)"
										:alt="sim.id"
										class="h-64 w-auto object-cover rounded-lg bg-gray-100"
										loading="lazy"
									>
									<!-- Favorite button -->
									<button
										type="button"
										@click="(e) => handleToggleFavorite(e, sim.id)"
										:disabled="savingFavoriteIds.has(sim.id)"
										:class="[
										'absolute top-1.5 left-1.5 p-1.5 rounded-md shadow-lg transition-all z-10',
										isFavorite(sim.id)
											? 'bg-red-500 text-white hover:bg-red-600'
											: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
										savingFavoriteIds.has(sim.id) && 'opacity-50 cursor-not-allowed',
										!isFavorite(sim.id) && 'opacity-0 group-hover:opacity-100',
									]"
										:title="isFavorite(sim.id) ? 'Remove from favorites' : 'Add to favorites'"
									>
										<Heart
											:size="14"
											:fill="isFavorite(sim.id) ? 'currentColor' : 'none'"
										/>
									</button>
									<!-- Similarity badge -->
									<span
										class="absolute bottom-1 right-1 px-1.5 py-0.5 bg-black/70 text-white text-xs rounded font-mono opacity-0 group-hover:opacity-100 transition-opacity"
									>
										{{ (sim.similarity * 100).toFixed(1) }}%
									</span>
								</RouterLink>
							</div>
						</div>

						<!-- Twitter Source -->
						<div v-if="image.source" class="bg-white rounded-xl shadow-md p-4">
							<h2 class="text-base font-semibold text-gray-900 mb-3">
								Twitter Source
							</h2>
							<div class="space-y-2 text-sm">
								<div
									v-if="image.source.user"
									class="flex flex-wrap items-center gap-2"
								>
									<a
										v-if="image.source.user.screen_name"
										:href="`https://twitter.com/${image.source.user.screen_name}`"
										target="_blank"
										rel="noopener noreferrer"
										class="text-blue-600 hover:underline font-medium flex items-center gap-1"
									>
										@{{ image.source.user.screen_name }}
										<ExternalLink :size="12" />
									</a>
									<span v-if="image.source.user.name" class="text-gray-600"
										>{{ image.source.user.name }}</span
									>
								</div>
								<div
									v-if="image.source.text"
									class="text-gray-700 bg-gray-50 rounded-lg p-2 whitespace-pre-wrap break-words text-xs"
								>
									{{ image.source.text }}
								</div>
								<a
									v-if="image.source.tweetId"
									:href="`https://twitter.com/i/web/status/${image.source.tweetId}`"
									target="_blank"
									rel="noopener noreferrer"
									class="text-blue-600 hover:underline text-xs flex items-center gap-1"
								>
									View Tweet <ExternalLink :size="11" />
								</a>
							</div>
						</div>
					</div>
				</div>

				<!-- All Inference Scores -->
				<div
					v-if="sortedInferences.length > 0"
					class="bg-white rounded-xl shadow-md p-4"
				>
					<h2 class="text-base font-semibold text-gray-900 mb-4">
						All ML Inference Scores
					</h2>
					<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
						<RouterLink
							v-for="item in sortedInferences"
							:key="`${item.modelKey}-${item.field}`"
							:to="dailyLink(item.sortField)"
							class="block space-y-1 rounded-lg p-2 hover:bg-gray-50 transition-colors group"
							:title="`Open daily list sorted by ${item.sortField}`"
						>
							<div class="flex items-center justify-between text-xs">
								<span
									class="text-gray-600 truncate mr-2 group-hover:text-blue-600 transition-colors"
									:title="item.modelKey"
								>
									{{ getModelDisplayName(item.modelKey) }}
									<span v-if="item.field !== 'score'" class="text-gray-400">
										/ {{ item.field }}</span
									>
								</span>
								<div class="flex items-center gap-2 shrink-0">
									<span
										class="font-mono font-medium tabular-nums text-gray-400"
									>
										{{ item.score.toFixed(4) }}
									</span>
									<!-- Rank -->
									<span
										v-if="image.scoreRanks?.[item.sortField]"
										class="text-gray-900 tabular-nums"
									>
										#{{ image.scoreRanks[item.sortField].rank }}
										/ {{ image.scoreRanks[item.sortField].total }}
									</span>
								</div>
							</div>
							<div class="w-full bg-gray-100 rounded-full h-1.5">
								<div
									:class="[
									'h-1.5 rounded-full',
									image.scoreRanks?.[item.sortField]
										? getRankColorClass(
												image.scoreRanks[item.sortField].rank,
												image.scoreRanks[item.sortField].total,
											)
										: getScoreColorClass(item.score),
								]"
									:style="{
									width: image.scoreRanks?.[item.sortField]
										? getRankBarWidth(
												image.scoreRanks[item.sortField].rank,
												image.scoreRanks[item.sortField].total,
											)
										: getScoreBarWidth(item.score),
								}"
								/>
							</div>
						</RouterLink>
					</div>
				</div>

				<!-- Important Tag Probabilities -->
				<div
					v-if="deepdanbooruTags.length > 0 || pixaiTags.length > 0"
					class="grid grid-cols-1 md:grid-cols-2 gap-4"
				>
					<!-- DeepDanbooru tags -->
					<div
						v-if="deepdanbooruTags.length > 0"
						class="bg-white rounded-xl shadow-md p-4"
					>
						<h2 class="text-base font-semibold text-gray-900 mb-3">
							Important Tags (DeepDanbooru)
						</h2>
						<div class="space-y-1 max-h-64 overflow-y-auto pr-1">
							<RouterLink
								v-for="[tag, prob] in deepdanbooruTags"
								:key="tag"
								:to="dailyLink(`importantTagProbs.deepdanbooru.${tag}`)"
								class="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-indigo-50 transition-colors group"
								:title="`Sort daily by DeepDanbooru: ${tag}`"
							>
								<div class="w-full bg-gray-100 rounded-full h-1.5 flex-1">
									<div
										class="h-1.5 rounded-full bg-indigo-400"
										:style="{width: `${(prob * 100).toFixed(1)}%`}"
									/>
								</div>
								<span
									class="text-xs text-gray-700 w-32 truncate text-right group-hover:text-indigo-700 transition-colors"
									:title="tag"
									>{{ tag }}</span
								>
								<span
									class="text-xs font-mono text-gray-500 w-12 text-right tabular-nums"
									>{{ (prob * 100).toFixed(1) }}%</span
								>
							</RouterLink>
						</div>
					</div>

					<!-- PixAI tags -->
					<div
						v-if="pixaiTags.length > 0"
						class="bg-white rounded-xl shadow-md p-4"
					>
						<h2 class="text-base font-semibold text-gray-900 mb-3">
							Important Tags (PixAI)
						</h2>
						<div class="space-y-1 max-h-64 overflow-y-auto pr-1">
							<RouterLink
								v-for="[tag, prob] in pixaiTags"
								:key="tag"
								:to="dailyLink(`importantTagProbs.pixai.${tag}`)"
								class="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-pink-50 transition-colors group"
								:title="`Sort daily by PixAI: ${tag}`"
							>
								<div class="w-full bg-gray-100 rounded-full h-1.5 flex-1">
									<div
										class="h-1.5 rounded-full bg-pink-400"
										:style="{width: `${(prob * 100).toFixed(1)}%`}"
									/>
								</div>
								<span
									class="text-xs text-gray-700 w-32 truncate text-right group-hover:text-pink-700 transition-colors"
									:title="tag"
									>{{ tag }}</span
								>
								<span
									class="text-xs font-mono text-gray-500 w-12 text-right tabular-nums"
									>{{ (prob * 100).toFixed(1) }}%</span
								>
							</RouterLink>
						</div>
					</div>
				</div>

				<!-- Moderation Ratings -->
				<div
					v-if="
					image.moderations && Object.keys(image.moderations).length > 0
				"
					class="bg-white rounded-xl shadow-md p-4"
				>
					<h2 class="text-base font-semibold text-gray-900 mb-3">
						Moderation Ratings
					</h2>
					<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
						<div
							v-for="(modData, model) in image.moderations"
							:key="model"
							class="border border-gray-100 rounded-lg p-3"
						>
							<div class="flex items-center justify-between mb-2">
								<span class="font-medium text-gray-700 capitalize text-sm"
									>{{ model }}</span
								>
								<span
									:class="[
									getRatingColorClass(modData.result),
									'px-2 py-0.5 rounded-full text-white text-xs font-semibold',
								]"
								>
									{{ modData.result ?? 'N/A' }}
									— {{ getRatingLabel(modData.result) }}
								</span>
							</div>
							<p
								v-if="modData.explanation"
								class="text-xs text-gray-600 leading-relaxed"
							>
								{{ modData.explanation }}
							</p>
						</div>
					</div>
				</div>

				<!-- Age Estimations -->
				<div
					v-if="
					image.ageEstimations &&
					Object.keys(image.ageEstimations).length > 0
				"
					class="bg-white rounded-xl shadow-md p-4"
				>
					<h2 class="text-base font-semibold text-gray-900 mb-3">
						Age Estimations
					</h2>
					<div class="space-y-4">
						<div
							v-for="(ageData, model) in image.ageEstimations"
							:key="model"
							class="border border-gray-100 rounded-lg p-3"
						>
							<div class="flex items-center justify-between mb-2">
								<span class="font-medium text-gray-700 capitalize text-sm"
									>{{ model }}</span
								>
								<span class="text-xs text-gray-500">
									{{ ageData.result.characters_detected }}
									character(s)
								</span>
							</div>
							<div
								v-if="ageData.result.characters.length > 0"
								class="grid grid-cols-1 sm:grid-cols-2 gap-2"
							>
								<div
									v-for="char in ageData.result.characters"
									:key="char.id"
									class="bg-gray-50 rounded-lg p-2"
								>
									<div class="flex items-center justify-between mb-1">
										<span class="text-xs font-medium text-gray-700">
											Character {{ char.id }} ({{ char.gender_guess }})
										</span>
										<span class="text-sm font-bold text-blue-600">
											{{ char.most_likely_age ?? '?' }}
										</span>
									</div>
									<p class="text-xs text-gray-500">
										{{ char.estimated_age_range }}
									</p>
									<div class="mt-1 w-full bg-gray-200 rounded-full h-1">
										<div
											class="h-1 rounded-full bg-blue-400"
											:style="{width: `${char.confidence * 100}%`}"
										/>
									</div>
								</div>
							</div>
						</div>
					</div>
				</div>

				<!-- Captions + Tags -->
				<div
					v-if="
					captionModels.length > 0 ||
					(image.tags && Object.keys(image.tags).length > 0)
				"
					class="grid grid-cols-1 lg:grid-cols-2 gap-4"
				>
					<!-- PixAI Tags -->
					<div
						v-if="image.tags && Object.keys(image.tags).length > 0"
						class="bg-white rounded-xl shadow-md p-4"
					>
						<div class="flex items-center justify-between mb-3">
							<h2 class="text-base font-semibold text-gray-900">Image Tags</h2>
							<select
								v-model="tagConfidenceFilter"
								class="text-xs border border-gray-300 rounded-lg px-2 py-1 focus:ring-2 focus:ring-blue-500"
							>
								<option value="high">High only</option>
								<option value="medium">Med+</option>
								<option value="low">All</option>
							</select>
						</div>
						<div
							v-for="(tagData, model) in image.tags"
							:key="model"
							class="space-y-2"
						>
							<p class="text-xs font-medium text-gray-500 capitalize">
								{{ model }}
							</p>
							<div class="flex flex-wrap gap-1.5">
								<span
									v-for="tag in getFilteredTags(tagData)"
									:key="`${tag.category}-${tag.name}`"
									:class="[
									'inline-flex items-center gap-0.5 px-2 py-0.5 rounded text-xs font-medium',
									getTagOpacity(tag.confidence),
									tag.category === 'character'
										? 'bg-blue-100 text-blue-800'
										: tag.category === 'ip'
											? 'bg-purple-100 text-purple-800'
											: 'bg-green-100 text-green-800',
								]"
									:title="tag.score ? `Score: ${tag.score.toFixed(3)}` : undefined"
								>
									<span v-if="tag.category === 'character'" class="text-[9px]"
										>👤</span
									>
									<span v-else-if="tag.category === 'ip'" class="text-[9px]"
										>©</span
									>
									{{ tag.name }}
								</span>
							</div>
						</div>
					</div>

					<!-- Captions -->
					<div v-if="captionModels.length > 0" class="space-y-4">
						<div
							v-for="model in captionModels"
							:key="model"
							class="bg-white rounded-xl shadow-md overflow-hidden"
						>
							<div
								class="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between"
							>
								<span class="font-semibold text-gray-900 capitalize text-sm"
									>{{ model }}</span
								>
								<span
									v-if="image.moderations?.[model]?.result !== undefined"
									:class="[
									getRatingColorClass(image.moderations[model].result),
									'px-2 py-0.5 rounded text-white text-xs font-medium',
								]"
								>
									{{ image.moderations[model].result }}
								</span>
							</div>
							<div class="p-4">
								<div class="bg-gray-50 rounded-lg p-3 max-h-64 overflow-y-auto">
									<ThinkBlock
										:text="image.captions?.[model]?.caption ?? 'No caption'"
										class="text-sm text-gray-700 leading-relaxed"
									/>
								</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		</template>

		<!-- Lightbox -->
		<ImageLightbox
			v-if="showLightbox && image"
			:src="imageUrl"
			:alt="image.key"
			@close="showLightbox = false"
		/>
	</div>
</template>
