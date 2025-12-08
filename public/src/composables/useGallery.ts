import {onMounted, onUnmounted, ref} from 'vue';
import type {ImageDocument} from '../types';

export function useGallery() {
	const imageAspectRatios = ref<Map<string, number>>(new Map());
	const galleryRows = ref<Array<Array<ImageDocument & {height: number}>>>([]);

	function handleImageLoad(event: Event, imageId: string) {
		const img = event.target as HTMLImageElement;
		const aspectRatio = img.naturalWidth / img.naturalHeight;
		imageAspectRatios.value.set(imageId, aspectRatio);
	}

	function calculateGalleryRows(images: ImageDocument[]) {
		const containerWidth = window.innerWidth - 32; // padding考慮
		const rowGap = 8; // gap-2 = 8px
		const targetRowHeight = 480;

		const rows: Array<Array<ImageDocument & {height: number}>> = [];
		let currentRow: Array<ImageDocument & {height: number}> = [];
		let currentRowWidth = 0;

		for (const image of images) {
			const aspectRatio = imageAspectRatios.value.get(image.id);
			if (!aspectRatio) continue;

			// 縦横比を制限
			let constrainedAspectRatio = aspectRatio;
			if (aspectRatio > 2) constrainedAspectRatio = 2;
			else if (aspectRatio < 0.5) constrainedAspectRatio = 0.5;

			const imageWidth = targetRowHeight * constrainedAspectRatio;

			// 現在の行に追加できるかチェック
			const wouldBeWidth =
				currentRowWidth + imageWidth + (currentRow.length > 0 ? rowGap : 0);

			if (currentRow.length > 0 && wouldBeWidth > containerWidth) {
				// 現在の行を確定し、高さを調整
				const totalWidth = currentRowWidth;
				const scale = containerWidth / totalWidth;
				const rowHeight = targetRowHeight * scale;

				for (const img of currentRow) {
					img.height = rowHeight;
				}

				rows.push(currentRow);
				currentRow = [];
				currentRowWidth = 0;
			}

			currentRow.push({...image, height: targetRowHeight});
			currentRowWidth += imageWidth + (currentRow.length > 1 ? rowGap : 0);
		}

		// 最後の行を追加（幅が足りなくてもそのまま）
		if (currentRow.length > 0) {
			rows.push(currentRow);
		}

		galleryRows.value = rows;
	}

	function getGalleryImageContainerStyle(imageId: string, height: number) {
		const aspectRatio = imageAspectRatios.value.get(imageId);
		if (!aspectRatio) return {};

		let width: number;

		// 横長すぎる (2:1より横長) → 2:1の比率で表示
		if (aspectRatio > 2) {
			width = height * 2;
		}
		// 縦長すぎる (1:2より縦長) → 1:2の比率で表示
		else if (aspectRatio < 0.5) {
			width = height / 2;
		}
		// 通常の縦横比 → そのまま表示
		else {
			width = height * aspectRatio;
		}

		return {width: `${width}px`, height: `${height}px`};
	}

	function getGalleryImageStyle(imageId: string, height: number) {
		const aspectRatio = imageAspectRatios.value.get(imageId);
		if (!aspectRatio) return {};

		let width: number;
		let objectFit: 'cover' | 'contain';

		// 横長すぎる (2:1より横長) → 2:1の比率で表示
		if (aspectRatio > 2) {
			width = height * 2;
			objectFit = 'cover';
		}
		// 縦長すぎる (1:2より縦長) → 1:2の比率で表示
		else if (aspectRatio < 0.5) {
			width = height / 2;
			objectFit = 'cover';
		}
		// 通常の縦横比 → そのまま表示
		else {
			width = height * aspectRatio;
			objectFit = 'contain';
		}

		return {
			width: `${width}px`,
			height: `${height}px`,
			objectFit,
		};
	}

	function resetGallery() {
		imageAspectRatios.value.clear();
		galleryRows.value = [];
	}

	// Window resize handler
	let resizeTimeout: number | null = null;

	function handleResize(images: ImageDocument[]) {
		// Debounce resize events
		if (resizeTimeout !== null) {
			window.clearTimeout(resizeTimeout);
		}

		resizeTimeout = window.setTimeout(() => {
			// Recalculate gallery rows if we have aspect ratios
			if (imageAspectRatios.value.size === images.length) {
				calculateGalleryRows(images);
			}
		}, 300);
	}

	// Add/remove resize listener
	onMounted(() => {
		window.addEventListener('resize', () => {
			// Note: The actual images array needs to be passed from the component
			// This will be handled in the component using this composable
		});
	});

	onUnmounted(() => {
		if (resizeTimeout !== null) {
			window.clearTimeout(resizeTimeout);
		}
	});

	return {
		imageAspectRatios,
		galleryRows,
		handleImageLoad,
		calculateGalleryRows,
		getGalleryImageContainerStyle,
		getGalleryImageStyle,
		resetGallery,
		handleResize,
	};
}
