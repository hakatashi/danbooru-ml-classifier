<script setup lang="ts">
import type {User} from 'firebase/auth';
import {ChevronLeft, Heart} from 'lucide-vue-next';
import {computed, onMounted, ref} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import ImageLightbox from '../components/ImageLightbox.vue';
import ThinkBlock from '../components/ThinkBlock.vue';
import {useImages} from '../composables/useImages';
import type {ImageDocument, TagData, TagList} from '../types';

type ConfidenceLevel = 'high' | 'medium' | 'low';
type TagCategory = 'character' | 'feature' | 'ip';

defineProps<{
	user: User | null;
}>();

const route = useRoute();
const router = useRouter();
const {getImageById, toggleFavorite, isFavorite} = useImages();
const isSavingFavorite = ref(false);

function goBack() {
	// If there's history, go back, otherwise go to home
	if (window.history.length > 1) {
		router.back();
	} else {
		router.push('/');
	}
}

const IMAGE_BASE_URL =
	'https://matrix.hakatashi.com/images/hakataarchive/twitter/';

const image = ref<ImageDocument | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const showLightbox = ref(false);

const filename = computed(() => {
	if (!image.value) return '';
	return image.value.key ? image.value.key.split('/').pop() : image.value.id;
});

const imageUrl = computed(() => IMAGE_BASE_URL + filename.value);

const models = computed(() => Object.keys(image.value?.captions || {}));

function removeThinkBlocks(text: string): string {
	return text.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
}

function getTranslateUrls(
	text: string,
): {name: string; url: string; icon: string}[] {
	const cleanText = removeThinkBlocks(text);
	const encoded = encodeURIComponent(cleanText);

	return [
		{
			name: 'Google Translate',
			url: `https://translate.google.com/?sl=en&tl=ja&text=${encoded}`,
			icon: 'G',
		},
		{
			name: 'DeepL',
			url: `https://www.deepl.com/translator#en/ja/${encoded}`,
			icon: 'D',
		},
		{
			name: 'Bing Translator',
			url: `https://www.bing.com/translator?from=en&to=ja&text=${encoded}`,
			icon: 'B',
		},
	];
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

onMounted(async () => {
	// Get the path from route params (e.g., "twitter/C60DOS6V4AAytlY.jpg")
	const imagePath = route.params.id as string;
	if (imagePath) {
		try {
			// Encode the path to create the document ID (e.g., "twitter%2FC60DOS6V4AAytlY.jpg")
			const documentId = encodeURIComponent(imagePath);
			image.value = await getImageById(documentId);
			if (!image.value) {
				error.value = 'Image not found';
			}
		} catch (e) {
			error.value = (e as Error).message;
		} finally {
			loading.value = false;
		}
	}
});

async function handleToggleFavorite() {
	if (!image.value || isSavingFavorite.value) return;

	isSavingFavorite.value = true;
	try {
		await toggleFavorite(image.value.id);
		// Refetch image data to update favorites state
		const updatedImage = await getImageById(image.value.id);
		if (updatedImage) {
			image.value = updatedImage;
		}
	} catch (e) {
		console.error('Failed to toggle favorite:', e);
	} finally {
		isSavingFavorite.value = false;
	}
}

// Tag display state
const tagConfidenceFilter = ref<ConfidenceLevel>('medium');

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

const tagCategoryOrder : Record<TagCategory, number> = {
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
					tags.push({
						name: tagName,
						category,
						confidence,
						score,
					});
				}
			}
		}
	}

	tags.sort((a, b) => {
		// Sort by category first
		if (a.category !== b.category) {
			return tagCategoryOrder[a.category] - tagCategoryOrder[b.category];
		}
		// Then by confidence level
		if (a.confidence !== b.confidence) {
			return confidenceOrder[a.confidence] - confidenceOrder[b.confidence];
		}
		// Then by alphabetical order
		return a.name.localeCompare(b.name);
	});

	return tags;
}

function handleTagClick(
	tagName: string,
	category: TagCategory,
	confidence: ConfidenceLevel,
) {
	// Navigate to home page with tag filter
	router.push({
		path: '/',
		query: {
			pixaiTag: tagName,
			pixaiCategory: category,
			pixaiConfidence: confidence,
		},
	});
}
</script>

<template>
	<div>
		<!-- Back link -->
		<button
			@click="goBack"
			class="inline-flex items-center gap-2 text-gray-600 hover:text-gray-900 mb-6"
		>
			<ChevronLeft :size="20"/>
			Back to Gallery
		</button>

		<!-- Loading -->
		<div v-if="loading" class="flex justify-center items-center py-20">
			<div
				class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
			></div>
		</div>

		<!-- Error -->
		<div
			v-else-if="error"
			class="bg-red-50 border border-red-200 rounded-xl p-6 text-center"
		>
			<p class="text-red-700">{{ error }}</p>
		</div>

		<!-- Image Detail -->
		<div v-else-if="image" class="grid grid-cols-1 lg:grid-cols-2 gap-8">
			<!-- Left: Image -->
			<div class="space-y-4">
				<div class="bg-black rounded-xl overflow-hidden relative group">
					<img
						:src="imageUrl"
						:alt="filename"
						class="w-full h-auto max-h-[80vh] object-contain cursor-pointer"
						@click="showLightbox = true"
					>
					<!-- Favorite button overlay -->
					<button
						@click="handleToggleFavorite"
						:disabled="isSavingFavorite"
						:class="[
							'absolute top-4 left-4 p-3 rounded-lg shadow-lg transition-all',
							isFavorite(image)
								? 'bg-red-500 text-white hover:bg-red-600'
								: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
							isSavingFavorite && 'opacity-50 cursor-not-allowed',
						]"
						:title="isFavorite(image) ? 'Remove from favorites' : 'Add to favorites'"
					>
						<Heart
							:size="24"
							:fill="isFavorite(image) ? 'currentColor' : 'none'"
						/>
					</button>
				</div>

				<!-- Image Meta -->
				<div class="bg-white rounded-xl shadow-md p-4">
					<h2 class="text-lg font-semibold text-gray-900 mb-4">
						Image Information
					</h2>
					<dl class="grid grid-cols-2 gap-4 text-sm">
						<div>
							<dt class="text-gray-500">Filename</dt>
							<dd class="font-mono text-gray-900 break-all">{{ filename }}</dd>
						</div>
						<div>
							<dt class="text-gray-500">Type</dt>
							<dd class="text-gray-900">{{ image.type || 'unknown' }}</dd>
						</div>
						<div>
							<dt class="text-gray-500">Post ID</dt>
							<dd class="font-mono text-gray-900">
								{{ image.postId || 'N/A' }}
							</dd>
						</div>
						<div>
							<dt class="text-gray-500">Status</dt>
							<dd class="text-gray-900 capitalize">
								{{ image.status || 'unknown' }}
							</dd>
						</div>
						<div class="col-span-2">
							<dt class="text-gray-500">Document ID</dt>
							<dd class="font-mono text-xs text-gray-900 break-all">
								{{ image.id }}
							</dd>
						</div>
					</dl>
				</div>

				<!-- Twitter Source Information -->
				<div v-if="image.source" class="bg-white rounded-xl shadow-md p-4">
					<h2 class="text-lg font-semibold text-gray-900 mb-3">
						Twitter Source
					</h2>
					<div class="space-y-2 text-sm">
						<div
							v-if="image.source.user"
							class="flex items-center gap-2 flex-wrap"
						>
							<!-- Show screen_name if available -->
							<a
								v-if="image.source.user.screen_name"
								:href="`https://twitter.com/${image.source.user.screen_name}`"
								target="_blank"
								rel="noopener noreferrer"
								class="text-blue-600 hover:text-blue-800 hover:underline font-medium"
							>
								@{{ image.source.user.screen_name }}
							</a>
							<!-- Show user ID if screen_name is not available -->
							<span
								v-else-if="image.source.user.id_str"
								class="text-gray-600 font-mono text-xs"
							>
								User ID: {{ image.source.user.id_str }}
							</span>

							<!-- Show name if available -->
							<span v-if="image.source.user.name" class="text-gray-600">
								{{ image.source.user.name }}
							</span>

							<!-- Filter by user link (always show if user ID exists) -->
							<router-link
								v-if="image.source.user.id_str"
								:to="`/?twitterUserId=${image.source.user.id_str}`"
								class="text-purple-600 hover:text-purple-800 hover:underline text-xs"
								title="Filter by this user"
							>
								View in Gallery →
							</router-link>

							<!-- View tweet link -->
							<a
								v-if="image.source.tweetId"
								:href="`https://twitter.com/i/web/status/${image.source.tweetId}`"
								target="_blank"
								rel="noopener noreferrer"
								class="text-blue-600 hover:text-blue-800 hover:underline text-xs ml-auto"
							>
								View Tweet →
							</a>
						</div>
						<div
							v-if="image.source.text"
							class="text-gray-700 bg-gray-50 rounded-lg p-2 whitespace-pre-wrap break-words"
						>
							{{ image.source.text }}
						</div>
						<div
							v-if="image.source.retweetedStatus"
							class="text-xs text-gray-500"
						>
							RT from
							<a
								v-if="image.source.retweetedStatus.user?.screen_name"
								:href="`https://twitter.com/${image.source.retweetedStatus.user.screen_name}`"
								target="_blank"
								rel="noopener noreferrer"
								class="text-blue-600 hover:text-blue-800 hover:underline"
							>
								@{{ image.source.retweetedStatus.user.screen_name }}
							</a>
						</div>
					</div>
				</div>

				<!-- Moderation Ratings -->
				<div class="bg-white rounded-xl shadow-md p-4">
					<h2 class="text-lg font-semibold text-gray-900 mb-4">
						Moderation Ratings
					</h2>
					<div class="space-y-3">
						<div
							v-for="model in models"
							:key="model"
							class="flex items-center justify-between"
						>
							<span class="font-medium text-gray-700 capitalize"
								>{{ model }}</span
							>
							<div class="flex items-center gap-2">
								<span
									:class="[
										getRatingColorClass(image.moderations?.[model]?.result ?? null),
										'px-3 py-1 rounded-full text-white font-semibold text-sm'
									]"
								>
									{{ image.moderations?.[model]?.result ?? 'N/A' }}
								</span>
								<span class="text-sm text-gray-500">
									{{ getRatingLabel(image.moderations?.[model]?.result ?? null) }}
								</span>
							</div>
						</div>
					</div>
				</div>

				<!-- Age Estimations -->
				<div
					v-if="image.ageEstimations && Object.keys(image.ageEstimations).length > 0"
					class="bg-white rounded-xl shadow-md p-4"
				>
					<h2 class="text-lg font-semibold text-gray-900 mb-4">
						Age Estimations
					</h2>
					<div class="space-y-4">
						<div
							v-for="model in Object.keys(image.ageEstimations)"
							:key="model"
							class="border-b border-gray-200 last:border-0 pb-4 last:pb-0"
						>
							<div class="flex items-center justify-between mb-3">
								<span class="font-medium text-gray-700 capitalize"
									>{{ model }}</span
								>
								<span class="text-sm text-gray-500">
									{{ image.ageEstimations?.[model]?.result.characters_detected ?? 0 }}
									character(s) detected
								</span>
							</div>

							<div
								v-if="(image.ageEstimations?.[model]?.result.characters.length ?? 0) > 0"
								class="space-y-3"
							>
								<div
									v-for="character in image.ageEstimations?.[model]?.result.characters ?? []"
									:key="character.id"
									class="bg-gray-50 rounded-lg p-3"
								>
									<div class="flex items-start justify-between mb-2">
										<div>
											<span class="font-semibold text-gray-900"
												>Character {{ character.id }}</span
											>
											<span class="ml-2 text-sm text-gray-500"
												>({{ character.gender_guess }})</span
											>
										</div>
										<div class="text-right">
											<div class="text-lg font-bold text-blue-600">
												{{ character.most_likely_age ?? 'N/A' }}
											</div>
											<div class="text-xs text-gray-500">
												{{ character.estimated_age_range }}
											</div>
										</div>
									</div>
									<div class="mb-2">
										<div
											class="flex items-center justify-between text-xs text-gray-600 mb-1"
										>
											<span>Confidence</span>
											<span>{{ Math.round(character.confidence * 100) }}%</span>
										</div>
										<div class="w-full bg-gray-200 rounded-full h-1.5">
											<div
												class="bg-blue-600 h-1.5 rounded-full"
												:style="{ width: `${character.confidence * 100}%` }"
											></div>
										</div>
									</div>
									<p class="text-xs text-gray-600 italic">
										{{ character.notes }}
									</p>
								</div>
							</div>
							<div v-else class="text-sm text-gray-500 text-center py-2">
								No characters detected
							</div>
						</div>
					</div>
				</div>
			</div>

			<!-- Right: Captions -->
			<div class="space-y-6">
				<!-- PixAI Tags -->
				<div
					v-if="image.tags && Object.keys(image.tags).length > 0"
					class="bg-white rounded-xl shadow-md p-4"
				>
					<div class="flex items-center justify-between mb-4">
						<h2 class="text-lg font-semibold text-gray-900">Image Tags</h2>
						<select
							v-model="tagConfidenceFilter"
							class="text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
						>
							<option value="high">High Confidence</option>
							<option value="medium">Medium Confidence</option>
							<option value="low">Low Confidence</option>
						</select>
					</div>

					<div
						v-for="model in Object.keys(image.tags)"
						:key="model"
						class="space-y-3"
					>
						<div class="flex items-center justify-between">
							<span class="font-medium text-gray-700 capitalize"
								>{{ model }}</span
							>
							<span class="text-xs text-gray-500">
								{{ image.tags?.[model] ? getFilteredTags(image.tags[model]).length : 0 }}
								tags
							</span>
						</div>

						<div v-if="image.tags?.[model]" class="flex flex-wrap gap-2">
							<button
								v-for="tag in getFilteredTags(image.tags[model])"
								:key="`${tag.category}-${tag.name}-${tag.confidence}`"
								@click="handleTagClick(tag.name, tag.category, tag.confidence)"
								:class="[
									'inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all',
									'hover:scale-105 hover:shadow-md cursor-pointer',
									getTagOpacity(tag.confidence),
									tag.category === 'character'
										? 'bg-blue-100 text-blue-800 hover:bg-blue-200'
										: tag.category === 'ip'
											? 'bg-purple-100 text-purple-800 hover:bg-purple-200'
											: 'bg-green-100 text-green-800 hover:bg-green-200',
								]"
								:title="tag.score ? `Score: ${tag.score.toFixed(3)}` : undefined"
							>
								<span v-if="tag.category === 'character'" class="text-[10px]"
									>👤</span
								>
								<span v-else-if="tag.category === 'ip'" class="text-[10px]"
									>©</span
								>
								{{ tag.name }}
							</button>
						</div>
					</div>
				</div>

				<div
					v-for="model in models"
					:key="model"
					class="bg-white rounded-xl shadow-md overflow-hidden"
				>
					<div class="bg-gray-50 px-4 py-3 border-b border-gray-200">
						<div class="flex items-center justify-between">
							<h3 class="font-semibold text-gray-900 capitalize">
								{{ model }}
							</h3>
							<div class="flex items-center gap-2">
								<span
									:class="[
										getRatingColorClass(image.moderations?.[model]?.result ?? null),
										'px-2 py-0.5 rounded text-white text-xs font-medium'
									]"
								>
									Rating: {{ image.moderations?.[model]?.result ?? 'N/A' }}
								</span>
							</div>
						</div>
						<p class="text-xs text-gray-500 mt-1">
							{{ image.captions?.[model]?.metadata?.repository || 'Unknown model' }}
						</p>
					</div>

					<div class="p-4">
						<h4 class="text-sm font-medium text-gray-700 mb-2">Caption</h4>
						<div class="bg-gray-50 rounded-lg p-3 max-h-96 overflow-y-auto">
							<ThinkBlock
								:text="image.captions?.[model]?.caption || 'No caption available'"
								class="text-sm text-gray-700 leading-relaxed"
							/>
						</div>

						<!-- Translation Links -->
						<div class="mt-3 flex items-center gap-2 flex-wrap">
							<span class="text-xs text-gray-500">Translate:</span>
							<a
								v-for="translator in getTranslateUrls(image.captions?.[model]?.caption || '')"
								:key="translator.name"
								:href="translator.url"
								target="_blank"
								rel="noopener noreferrer"
								class="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded transition-colors"
								:title="translator.name"
							>
								<span
									class="w-4 h-4 flex items-center justify-center bg-blue-600 text-white rounded text-[10px] font-bold"
								>
									{{ translator.icon }}
								</span>
								{{ translator.name }}
							</a>
						</div>
					</div>

					<!-- Moderation Explanation -->
					<div v-if="image.moderations?.[model]?.explanation" class="px-4 pb-4">
						<h4 class="text-sm font-medium text-gray-700 mb-2">
							Moderation Explanation
						</h4>
						<div class="bg-amber-50 border border-amber-200 rounded-lg p-3">
							<p class="text-sm text-gray-700 leading-relaxed">
								{{ image.moderations[model].explanation }}
							</p>
						</div>
					</div>

					<!-- Raw Moderation Result -->
					<div v-if="image.moderations?.[model]?.raw_result" class="px-4 pb-4">
						<details class="group">
							<summary
								class="text-sm font-medium text-gray-500 cursor-pointer hover:text-gray-700"
							>
								Raw Moderation Response
							</summary>
							<div
								class="mt-2 bg-gray-50 rounded-lg p-3 text-xs font-mono text-gray-600 whitespace-pre-wrap"
							>
								{{ image.moderations[model].raw_result }}
							</div>
						</details>
					</div>
				</div>
			</div>
		</div>

		<ImageLightbox
			v-if="showLightbox && image"
			:src="imageUrl"
			:alt="filename || ''"
			@close="showLightbox = false"
		/>
	</div>
</template>
