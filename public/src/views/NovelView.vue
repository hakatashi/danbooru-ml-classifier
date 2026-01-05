<script setup lang="ts">
import {doc, getDoc} from 'firebase/firestore';
import {ChevronDown, ChevronLeft, ChevronUp} from 'lucide-vue-next';
import {computed, onMounted, ref} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import ImageLightbox from '../components/ImageLightbox.vue';
import {db} from '../firebase';
import type {ImageDocument, NovelDocument} from '../types';

const route = useRoute();
const router = useRouter();

const novel = ref<NovelDocument | null>(null);
const image = ref<ImageDocument | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const showLightbox = ref(false);

const IMAGE_BASE_URL =
	'https://matrix-images.hakatashi.com/hakataarchive/twitter/';

const imageFilename = computed(() => {
	if (!image.value) return '';
	return image.value.key ? image.value.key.split('/').pop() : image.value.id;
});

const imageUrl = computed(() => IMAGE_BASE_URL + imageFilename.value);

onMounted(async () => {
	const novelId = route.params.novelId as string;
	if (!novelId) {
		error.value = 'Novel ID is required';
		loading.value = false;
		return;
	}

	try {
		// Fetch novel document
		const novelDoc = await getDoc(doc(db, 'generated_novels', novelId));
		if (!novelDoc.exists()) {
			error.value = 'Novel not found';
			loading.value = false;
			return;
		}

		novel.value = {
			id: novelDoc.id,
			...novelDoc.data(),
		} as NovelDocument;

		// Fetch image document
		const imageDoc = await getDoc(doc(db, 'images', novel.value.imageId));
		if (imageDoc.exists()) {
			image.value = {
				id: imageDoc.id,
				...imageDoc.data(),
			} as ImageDocument;
		}
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
});

function goBackToImage() {
	if (image.value) {
		const imagePath = decodeURIComponent(image.value.id);
		router.push(`/image/${imagePath}`);
	} else {
		router.push('/');
	}
}

function getModelDisplayName(modelName: string): string {
	switch (modelName) {
		case 'grok-4-1-fast-non-reasoning':
			return 'Grok 4.1 Fast';
		case 'grok-4-1-fast-reasoning':
			return 'Grok 4.1 Fast Reasoning';
		default:
			return modelName;
	}
}
</script>

<template>
	<div class="min-h-screen">
		<!-- Loading -->
		<div v-if="loading" class="flex justify-center items-center py-20">
			<div
				class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
			></div>
		</div>

		<!-- Error -->
		<div
			v-else-if="error"
			class="max-w-5xl mx-auto bg-red-50 border border-red-200 rounded-xl p-6 text-center mt-6"
		>
			<p class="text-red-700">{{ error }}</p>
		</div>

		<!-- Novel Content -->
		<div v-else-if="novel" class="md:grid md:grid-cols-2 md:gap-8 md:h-screen">
			<!-- Left Column: Image (Fixed on Desktop) -->
			<div
				v-if="image"
				class="md:sticky md:top-0 md:h-screen md:overflow-hidden md:flex md:items-center md:justify-center bg-black p-4"
			>
				<img
					:src="imageUrl"
					:alt="imageFilename"
					class="w-full h-auto max-h-full object-contain cursor-pointer"
					@click="showLightbox = true"
				>
			</div>

			<!-- Right Column: Content (Scrollable) -->
			<div class="md:overflow-y-auto md:h-screen p-0 space-y-6">
				<!-- Back link -->
				<button
					@click="goBackToImage"
					class="inline-flex items-center gap-2 text-gray-600 hover:text-gray-900"
				>
					<ChevronLeft :size="20" />
					Back to Image Details
				</button>

				<!-- Novel Info -->
				<div class="bg-white rounded-xl shadow-md p-6 space-y-4">
					<div>
						<div class="flex items-center gap-2 mb-2">
							<span
								:class="[
									'px-3 py-1 rounded-full text-sm font-medium',
									novel.status === 'completed'
										? 'bg-green-100 text-green-800'
										: novel.status === 'generating'
											? 'bg-blue-100 text-blue-800'
											: 'bg-red-100 text-red-800',
								]"
							>
								{{ novel.status }}
							</span>
							<span class="text-sm text-gray-500">
								Generated
								{{ novel.mode === 'image' ? 'from image' : `from ${novel.captionProvider} caption` }}
							</span>
						</div>
						<p class="text-sm text-gray-500">
							Created on
							{{ new Date(novel.createdAt.seconds * 1000).toLocaleString() }}
						</p>
					</div>

					<!-- Stats -->
					<div class="grid grid-cols-2 gap-4">
						<div class="bg-gray-50 rounded-lg p-3">
							<dt class="text-xs text-gray-500 mb-1">Scenes</dt>
							<dd class="text-lg font-semibold text-gray-900">
								{{ novel.scenes.length }}
							</dd>
						</div>
						<div class="bg-gray-50 rounded-lg p-3">
							<dt class="text-xs text-gray-500 mb-1">Total Tokens</dt>
							<dd class="text-lg font-semibold text-gray-900">
								{{ novel.tokenUsage.totalTokens.toLocaleString() }}
							</dd>
						</div>
						<div class="bg-gray-50 rounded-lg p-3">
							<dt class="text-xs text-gray-500 mb-1">Cost</dt>
							<dd class="text-lg font-semibold text-green-600">
								${{ novel.estimatedCost.totalCost.toFixed(4) }}
							</dd>
						</div>
						<div class="bg-gray-50 rounded-lg p-3">
							<dt class="text-xs text-gray-500 mb-1">Model</dt>
							<dd class="text-sm font-semibold text-gray-900">
								{{ getModelDisplayName(novel.model) }}
							</dd>
						</div>
					</div>

					<!-- Expandable sections for outline and plot -->
					<div class="space-y-2">
						<details class="group">
							<summary
								class="cursor-pointer flex items-center justify-between bg-blue-50 hover:bg-blue-100 rounded-lg p-3 transition-colors"
							>
								<span class="font-medium text-blue-900">Outline</span>
								<ChevronDown
									:size="20"
									class="text-blue-600 group-open:hidden"
								/>
								<ChevronUp
									:size="20"
									class="text-blue-600 hidden group-open:block"
								/>
							</summary>
							<div class="mt-2 bg-gray-50 rounded-lg p-4">
								<p class="text-gray-700 whitespace-pre-wrap">
									{{ novel.outline }}
								</p>
							</div>
						</details>

						<details class="group">
							<summary
								class="cursor-pointer flex items-center justify-between bg-purple-50 hover:bg-purple-100 rounded-lg p-3 transition-colors"
							>
								<span class="font-medium text-purple-900">Plot</span>
								<ChevronDown
									:size="20"
									class="text-purple-600 group-open:hidden"
								/>
								<ChevronUp
									:size="20"
									class="text-purple-600 hidden group-open:block"
								/>
							</summary>
							<div class="mt-2 bg-gray-50 rounded-lg p-4">
								<p class="text-gray-700 whitespace-pre-wrap">
									{{ novel.plot }}
								</p>
							</div>
						</details>
					</div>
				</div>

				<!-- Scenes -->
				<div class="space-y-4">
					<h2 class="text-2xl font-bold text-gray-900">Scenes</h2>
					<div
						v-for="scene in novel.scenes"
						:key="scene.sceneNumber"
						class="bg-white rounded-xl shadow-md p-4"
					>
						<div class="prose max-w-none">
							<p
								class="text-gray-700 leading-relaxed whitespace-pre-wrap text-justify text-2xl"
								style="font-family: 'Yu Mincho', YuMincho, 'Hiragino Mincho ProN', 'Hiragino Mincho Pro', 'Source Han Serif', 'Source Han Serif JP', 'BIZ UDMincho Medium', 'Noto Serif JP', 'Source Serif Pro', 'Source Serif', 'Noto Serif', 'Times New Roman', 'Georgia Pro', Georgia, 'Songti SC', 'Source Han Serif SC', 'Source Han Serif CN', 'Noto Serif SC', Simsun, 'Songti TC', 'Source Han Serif TC', 'Source Han Serif TW', 'Source Han Serif HK', 'Noto Serif TC', PMingLiu, AppleMyungjo, 'Source Han Serif K', 'Source Han Serif KR', 'Noto Serif KR', Batang, serif;"
							>
								{{ scene.content }}
							</p>
						</div>
					</div>
				</div>
			</div>
		</div>

		<ImageLightbox
			v-if="showLightbox && image"
			:src="imageUrl"
			:alt="imageFilename || ''"
			@close="showLightbox = false"
		/>
	</div>
</template>
