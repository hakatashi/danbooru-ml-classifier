<script setup lang="ts">
import {onMounted, onUnmounted, watch} from 'vue';
import {type ApiImageDocument, getImageUrl} from '../api/mlApi';
import {useGallery} from '../composables/useGallery';
import type {ImageDocument} from '../types';

const props = withDefaults(
	defineProps<{
		images: ApiImageDocument[];
		thumbnail?: boolean;
		mobileGrid?: boolean;
		selectable?: boolean;
		selectedIds?: Set<string>;
	}>(),
	{
		thumbnail: true,
		mobileGrid: true,
		selectable: false,
		selectedIds: () => new Set<string>(),
	},
);

const emit = defineEmits<{
	imageClick: [image: ApiImageDocument];
	toggleSelect: [image: ApiImageDocument, event: MouseEvent];
	imageError: [image: ApiImageDocument];
}>();

const {
	imageAspectRatios,
	galleryRows,
	handleImageLoad,
	calculateGalleryRows,
	getGalleryImageContainerStyle,
	getGalleryImageStyle,
	resetGallery,
	handleResize,
} = useGallery();

function recalculate() {
	resetGallery();
	// Pre-seed a provisional 1:1 aspect ratio for images with no known
	// width/height (e.g. legacy Twitter favorites) so they aren't dropped
	// from the layout entirely -- calculateGalleryRows skips images with no
	// entry in imageAspectRatios. The real ratio overwrites this on @load.
	for (const image of props.images) {
		if (image.width && image.height && image.width > 0 && image.height > 0) {
			imageAspectRatios.value.set(image.id, image.width / image.height);
		} else {
			imageAspectRatios.value.set(image.id, 1);
		}
	}
	calculateGalleryRows(props.images as unknown as ImageDocument[]);
}

watch(() => props.images, recalculate, {immediate: true});

function onImageLoad(event: Event, imageId: string) {
	handleImageLoad(event, imageId);
	calculateGalleryRows(props.images as unknown as ImageDocument[]);
}

function onResize() {
	handleResize(props.images as unknown as ImageDocument[]);
}

onMounted(() => window.addEventListener('resize', onResize));
onUnmounted(() => window.removeEventListener('resize', onResize));

function isSelected(imageId: string): boolean {
	return props.selectedIds.has(imageId);
}

function handleTileClick(image: ApiImageDocument, event: MouseEvent) {
	if (props.selectable) {
		emit('toggleSelect', image, event);
	} else {
		emit('imageClick', image);
	}
}
</script>

<template>
	<div>
		<!-- Mobile: 2-column square grid -->
		<div v-if="mobileGrid" class="grid grid-cols-2 gap-1 sm:hidden">
			<div
				v-for="image in images"
				:key="image.id"
				class="relative aspect-square overflow-hidden group cursor-pointer bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
				@click="handleTileClick(image, $event)"
			>
				<img
					:src="getImageUrl(image, thumbnail)"
					:alt="image.id"
					class="w-full h-full object-cover"
					loading="lazy"
					@error="emit('imageError', image)"
				>
				<div
					v-if="selectable"
					:class="[
						'absolute inset-0 transition-colors',
						isSelected(image.id) ? 'bg-blue-500/30' : 'bg-black/0 group-hover:bg-black/10',
					]"
				/>
				<div
					v-if="selectable"
					:class="[
						'absolute top-1.5 right-1.5 w-5 h-5 rounded border-2 flex items-center justify-center',
						isSelected(image.id)
							? 'bg-blue-600 border-blue-600'
							: 'bg-white/80 border-white',
					]"
				>
					<svg
						aria-hidden="true"
						v-if="isSelected(image.id)"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="w-3.5 h-3.5 text-white"
					>
						<path
							fill-rule="evenodd"
							d="M16.7 5.3a1 1 0 010 1.4l-8 8a1 1 0 01-1.4 0l-4-4a1 1 0 111.4-1.4L8 12.6l7.3-7.3a1 1 0 011.4 0z"
							clip-rule="evenodd"
						/>
					</svg>
				</div>
				<div class="absolute top-1.5 left-1.5 z-10">
					<slot name="overlay-top-left" :image="image" />
				</div>
				<div class="absolute bottom-1.5 right-1.5 z-10">
					<slot name="overlay-top-right" :image="image" />
				</div>
			</div>
		</div>

		<!-- Desktop: Justified rows -->
		<div
			v-if="galleryRows.length > 0"
			:class="['flex-col gap-2', mobileGrid ? 'hidden sm:flex' : 'flex']"
		>
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
					@click="handleTileClick(image as unknown as ApiImageDocument, $event)"
				>
					<img
						:src="getImageUrl(image as unknown as ApiImageDocument, thumbnail)"
						:alt="image.id"
						class="justified-gallery-image bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
						:style="getGalleryImageStyle(image.id, image.height)"
						loading="lazy"
						@load="(e) => onImageLoad(e, image.id)"
						@error="emit('imageError', image as unknown as ApiImageDocument)"
					>
					<div
						v-if="selectable"
						:class="[
							'absolute inset-0 transition-colors',
							isSelected(image.id) ? 'bg-blue-500/30' : 'bg-black/0 group-hover:bg-black/10',
						]"
					/>
					<div
						v-if="selectable"
						:class="[
							'absolute top-2 right-2 w-6 h-6 rounded border-2 flex items-center justify-center z-10',
							isSelected(image.id)
								? 'bg-blue-600 border-blue-600'
								: 'bg-white/80 border-white',
						]"
					>
						<svg
							aria-hidden="true"
							v-if="isSelected(image.id)"
							viewBox="0 0 20 20"
							fill="currentColor"
							class="w-4 h-4 text-white"
						>
							<path
								fill-rule="evenodd"
								d="M16.7 5.3a1 1 0 010 1.4l-8 8a1 1 0 01-1.4 0l-4-4a1 1 0 111.4-1.4L8 12.6l7.3-7.3a1 1 0 011.4 0z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
					<div class="absolute top-2 left-2 z-10">
						<slot name="overlay-top-left" :image="image" />
					</div>
					<div class="absolute bottom-2 right-2 z-10">
						<slot name="overlay-top-right" :image="image" />
					</div>
				</div>
			</div>
		</div>

		<!-- Pre-load: fixed height before aspect ratios known -->
		<div
			v-else
			:class="[
				'flex-wrap justify-center gap-2',
				mobileGrid ? 'hidden sm:flex' : 'flex',
			]"
		>
			<div
				v-for="image in images"
				:key="image.id"
				class="relative h-[320px] flex-shrink-0 group cursor-pointer overflow-hidden"
				:style="getGalleryImageContainerStyle(image.id, 320)"
				@click="handleTileClick(image, $event)"
			>
				<img
					:src="getImageUrl(image, thumbnail)"
					:alt="image.id"
					class="justified-gallery-image bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
					:style="getGalleryImageStyle(image.id, 320)"
					loading="lazy"
					@load="(e) => onImageLoad(e, image.id)"
					@error="emit('imageError', image)"
				>
				<div class="absolute top-2 left-2 z-10">
					<slot name="overlay-top-left" :image="image" />
				</div>
				<div class="absolute bottom-2 right-2 z-10">
					<slot name="overlay-top-right" :image="image" />
				</div>
			</div>
		</div>
	</div>
</template>
