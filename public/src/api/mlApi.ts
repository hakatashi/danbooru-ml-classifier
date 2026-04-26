import type {
	AgeEstimationData,
	CaptionData,
	FavoritesData,
	ModerationData,
	TagData,
	TwitterSourceData,
} from '../types';

const BASE_URL = 'https://danbooru-api.matrix.hakatashi.com';

export interface InferenceScore {
	score?: number;
	not_bookmarked?: number;
	bookmarked_public?: number;
	bookmarked_private?: number;
}

export interface ApiImageDocument {
	id: string;
	key: string;
	status: string;
	type: string;
	postId?: string;
	date?: string;
	width?: number;
	height?: number;
	captions?: Record<string, CaptionData>;
	moderations?: Record<string, ModerationData>;
	ageEstimations?: Record<string, AgeEstimationData>;
	tags?: Record<string, TagData>;
	favorites?: FavoritesData;
	source?: TwitterSourceData;
	inferences?: Record<string, InferenceScore>;
	importantTagProbs?: {
		deepdanbooru?: Record<string, number>;
		pixai?: Record<string, number>;
	};
	// Present only on single-image GET /images/{id}
	scoreRanks?: Record<string, ScoreRankInfo>;
}

export interface ScoreRankInfo {
	rank: number | null;
	total: number;
}

export interface ImagesResponse {
	images: ApiImageDocument[];
	page: number;
	limit: number;
	count: number;
	total: number;
}

export interface DailyCountsResponse {
	month: string;
	days: Record<string, number>;
}

export interface InferenceModel {
	key: string;
	type: 'pu' | 'legacy_multiclass';
	fields: string[];
}

export interface InferenceModelsResponse {
	models: InferenceModel[];
}

export interface ImportantTagsResponse {
	tags: {
		deepdanbooru?: string[];
		pixai?: string[];
	};
}

export async function fetchImages(params: {
	sort_field: string;
	sort_dir?: 'asc' | 'desc';
	date?: string;
	page?: number;
	limit?: number;
}): Promise<ImagesResponse> {
	const url = new URL(`${BASE_URL}/images`);
	url.searchParams.set('sort_field', params.sort_field);
	if (params.sort_dir) url.searchParams.set('sort_dir', params.sort_dir);
	if (params.date) url.searchParams.set('date', params.date);
	if (params.page !== undefined)
		url.searchParams.set('page', String(params.page));
	if (params.limit !== undefined)
		url.searchParams.set('limit', String(params.limit));

	const res = await fetch(url.toString());
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	return res.json();
}

export async function fetchImageById(id: string): Promise<ApiImageDocument> {
	const res = await fetch(`${BASE_URL}/images/${id}`);
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	return res.json();
}

export async function fetchDailyCounts(
	month: string,
): Promise<DailyCountsResponse> {
	const url = new URL(`${BASE_URL}/daily-counts`);
	url.searchParams.set('month', month);
	const res = await fetch(url.toString());
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	return res.json();
}

export async function fetchInferenceModels(): Promise<InferenceModelsResponse> {
	const res = await fetch(`${BASE_URL}/inference-models`);
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	return res.json();
}

export async function fetchImportantTags(): Promise<ImportantTagsResponse> {
	const res = await fetch(`${BASE_URL}/important-tags`);
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	return res.json();
}

export interface SimilarImage extends ApiImageDocument {
	similarity: number;
}

export interface SimilarImagesResponse {
	similar: SimilarImage[];
	total: number;
}

export async function fetchSimilarImages(
	id: string,
	params?: {
		limit?: number;
		status?: string;
		date?: string;
		type?: string;
		axis?: string;
	},
): Promise<SimilarImagesResponse> {
	const url = new URL(`${BASE_URL}/images/${id}/similar`);
	if (params?.limit !== undefined)
		url.searchParams.set('limit', String(params.limit));
	if (params?.status) url.searchParams.set('status', params.status);
	if (params?.date) url.searchParams.set('date', params.date);
	if (params?.type) url.searchParams.set('type', params.type);
	if (params?.axis) url.searchParams.set('axis', params.axis);
	const res = await fetch(url.toString());
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	return res.json();
}

export async function fetchPostSource(
	provider: 'danbooru' | 'gelbooru',
	postId: string,
): Promise<string | null> {
	const url = new URL(`${BASE_URL}/post-source`);
	url.searchParams.set('provider', provider);
	url.searchParams.set('id', postId);
	const res = await fetch(url.toString());
	if (!res.ok) throw new Error(`API error: ${res.status}`);
	const data: {source: string | null} = await res.json();
	return data.source;
}

export function getImageUrl(
	image: ApiImageDocument,
	thumbnail = false,
): string {
	const BASE = 'https://matrix-images.hakatashi.com/danbooru-ml-classifier/';
	const path = thumbnail ? 'thumbnails/' : 'images/';
	if (image.key) return `${BASE}${path}${image.key}`;
	return `${BASE}${path}twitter/${image.id}`;
}
