<script setup lang="ts">
import type {User} from 'firebase/auth';
import {computed, onMounted, ref} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import ImageLightbox from '../components/ImageLightbox.vue';
import ThinkBlock from '../components/ThinkBlock.vue';
import {useImages} from '../composables/useImages';
import type {ImageDocument} from '../types';

defineProps<{
	user: User | null;
}>();

const route = useRoute();
const router = useRouter();
const {getImageById} = useImages();

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
	const id = route.params.id as string;
	if (id) {
		try {
			image.value = await getImageById(id);
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
</script>

<template>
	<div>
		<!-- Back link -->
		<button
			@click="goBack"
			class="inline-flex items-center gap-2 text-gray-600 hover:text-gray-900 mb-6"
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
					d="M15 19l-7-7 7-7"
				/>
			</svg>
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
				<div class="bg-black rounded-xl overflow-hidden">
					<img
						:src="imageUrl"
						:alt="filename"
						class="w-full h-auto max-h-[80vh] object-contain cursor-pointer"
						@click="showLightbox = true"
					>
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
			</div>

			<!-- Right: Captions -->
			<div class="space-y-6">
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
