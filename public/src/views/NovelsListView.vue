<script setup lang="ts">
import type {User} from 'firebase/auth';
import {
	collection,
	doc,
	getDoc,
	getDocs,
	limit,
	orderBy,
	query,
} from 'firebase/firestore';
import {BookOpen, Calendar, Coins, Layers} from 'lucide-vue-next';
import {onMounted, ref} from 'vue';
import {useRouter} from 'vue-router';
import {db} from '../firebase';
import type {ImageDocument, NovelDocument} from '../types';

defineProps<{
	user: User | null;
}>();

const router = useRouter();
const novels = ref<NovelDocument[]>([]);
const images = ref<Map<string, ImageDocument>>(new Map());
const loading = ref(true);
const error = ref<string | null>(null);

const IMAGE_BASE_URL =
	'https://matrix-images.hakatashi.com/hakataarchive/twitter/';

function getImageUrl(imageDoc: ImageDocument | undefined): string {
	if (!imageDoc) return '';
	const filename = imageDoc.key ? imageDoc.key.split('/').pop() : imageDoc.id;
	return IMAGE_BASE_URL + filename;
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

onMounted(async () => {
	try {
		// Fetch novels ordered by creation date
		const novelsQuery = query(
			collection(db, 'generated_novels'),
			orderBy('createdAt', 'desc'),
			limit(50),
		);
		const snapshot = await getDocs(novelsQuery);
		novels.value = snapshot.docs.map((doc) => ({
			id: doc.id,
			...doc.data(),
		})) as NovelDocument[];

		// Fetch associated images
		const imageIds = [...new Set(novels.value.map((n) => n.imageId))];
		const imageMap = new Map<string, ImageDocument>();

		// Fetch images individually by ID
		await Promise.all(
			imageIds.map(async (imageId) => {
				try {
					const imageDoc = await getDoc(doc(db, 'images', imageId));
					if (imageDoc.exists()) {
						imageMap.set(imageId, {
							id: imageDoc.id,
							...imageDoc.data(),
						} as ImageDocument);
					}
				} catch (err) {
					console.error(`Failed to fetch image ${imageId}:`, err);
				}
			}),
		);

		images.value = imageMap;
	} catch (err) {
		error.value = (err as Error).message;
	} finally {
		loading.value = false;
	}
});

function viewNovel(novelId: string) {
	router.push(`/novels/${novelId}`);
}

function viewImage(imageId: string) {
	const imagePath = decodeURIComponent(imageId);
	router.push(`/image/${imagePath}`);
}
</script>

<template>
	<div class="container mx-auto px-4 py-8">
		<div class="mb-8">
			<h1 class="text-3xl font-bold text-gray-900 mb-2">
				Recently Generated Novels
			</h1>
			<p class="text-gray-600">
				Explore AI-generated novels created from images
			</p>
		</div>

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

		<!-- Novels List -->
		<div v-else-if="novels.length > 0" class="space-y-6">
			<div
				v-for="novel in novels"
				:key="novel.id"
				class="bg-white rounded-xl shadow-md overflow-hidden hover:shadow-lg transition-shadow h-80"
			>
				<div class="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-0">
					<!-- Left: Image -->
					<div
						class="bg-black aspect-square md:aspect-auto relative group cursor-pointer"
						@click="viewImage(novel.imageId)"
					>
						<img
							v-if="images.get(novel.imageId)"
							:src="getImageUrl(images.get(novel.imageId))"
							:alt="`Novel ${novel.id}`"
							class="w-full h-full object-cover"
						>
						<div
							v-else
							class="w-full h-full flex items-center justify-center text-gray-400"
						>
							<BookOpen :size="48"/>
						</div>
					</div>

					<!-- Right: Novel Info -->
					<div class="p-6">
						<div class="flex items-start justify-between mb-4">
							<div class="flex items-center gap-2 flex-wrap">
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
									{{ novel.mode === 'image' ? 'From Image' : `From ${novel.captionProvider}` }}
								</span>
							</div>
							<button
								@click="viewNovel(novel.id)"
								class="px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
							>
								Read Novel
							</button>
						</div>

						<!-- Content Preview -->
						<div class="mb-4">
							<h3 class="text-lg font-semibold text-gray-900 mb-2">Preview</h3>
							<p class="text-gray-700 line-clamp-3">
								{{ novel.scenes?.[0]?.content?.substring(0, 200) || 'Generating...' }}
								{{ novel.scenes?.[0]?.content ? '...' : '' }}
							</p>
						</div>

						<!-- Stats Grid -->
						<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
							<div class="flex items-center gap-2 text-sm">
								<Layers :size="16" class="text-gray-400"/>
								<div>
									<div class="text-gray-500 text-xs">Scenes</div>
									<div class="font-semibold text-gray-900">
										{{ novel.scenes?.length ?? 0 }}
									</div>
								</div>
							</div>

							<div class="flex items-center gap-2 text-sm">
								<BookOpen :size="16" class="text-gray-400"/>
								<div>
									<div class="text-gray-500 text-xs">Tokens</div>
									<div class="font-semibold text-gray-900">
										{{ novel.tokenUsage?.totalTokens?.toLocaleString() ?? '-' }}
									</div>
								</div>
							</div>

							<div class="flex items-center gap-2 text-sm">
								<Coins :size="16" class="text-gray-400"/>
								<div>
									<div class="text-gray-500 text-xs">Cost</div>
									<div class="font-semibold text-green-600">
										{{ novel.estimatedCost?.totalCost ? `$${novel.estimatedCost.totalCost.toFixed(4)}` : '-' }}
									</div>
								</div>
							</div>

							<div class="flex items-center gap-2 text-sm">
								<Calendar :size="16" class="text-gray-400"/>
								<div>
									<div class="text-gray-500 text-xs">Created</div>
									<div class="font-semibold text-gray-900">
										{{ novel.createdAt?.seconds ? new Date(novel.createdAt.seconds * 1000).toLocaleDateString() : '-' }}
									</div>
								</div>
							</div>
						</div>

						<!-- Model Info -->
						<div class="mt-4 pt-4 border-t border-gray-200">
							<div class="text-xs text-gray-500">
								Model:
								<span class="font-medium text-gray-700">
									{{ getModelDisplayName(novel.model) }}
								</span>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>

		<!-- Empty State -->
		<div
			v-else
			class="bg-gray-50 border border-gray-200 rounded-xl p-12 text-center"
		>
			<BookOpen :size="48" class="mx-auto text-gray-400 mb-4"/>
			<p class="text-gray-600 text-lg">No novels have been generated yet</p>
		</div>
	</div>
</template>
