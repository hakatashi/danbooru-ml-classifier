import type {
	AgeEstimationData,
	CaptionData,
	FavoritesData,
	ModerationData,
	TagData,
	TwitterSourceData,
} from '../types';

const BASE_URL = 'https://danbooru-api.matrix.hakatashi.com';

// ---------------------------------------------------------------------------
// metadata フィールド — 画像取得時にソースAPIから得られたデータをそのまま保存
// ---------------------------------------------------------------------------

/** Pixiv ランキングAPIの1エントリ（/ranking.php?format=json の contents[] 要素） */
export interface PixivArtworkMetadata {
	illust_id: number;
	title: string;
	/** "2026年05月10日 18:38" 形式 */
	date: string;
	tags: string[];
	user_name: string;
	user_id: number;
	/** ランキング順位（1始まり） */
	rank: number;
	yes_rank: number;
	rating_count: number;
	view_count: number;
	width: number;
	height: number;
	illust_page_count: string;
	illust_upload_timestamp: number;
	illust_content_type: {
		sexual: number;
		lo: boolean;
		grotesque: boolean;
		violent: boolean;
		homosexual: boolean;
		drug: boolean;
		bl: boolean;
		yuri: boolean;
		[key: string]: unknown;
	};
	is_bookmarked: boolean;
	[key: string]: unknown;
}

/** Pixiv ランキング取得時のコンテキスト */
export interface PixivRankingContext {
	/** ランキング種別 例: "daily" | "male_r18" | "female_r18" | "daily_ai" | "daily_r18_ai" */
	mode: string;
	/** ランキングのページ番号（0始まり）。artworkId.page（作品内ページ）とは別物 */
	page: number;
	/** ランキング日付 "YYYY-MM-DD" */
	date: string;
}

/** Danbooru ポストAPIのレスポンス（/explore/posts/popular.json の1要素） */
export interface DanbooruPostMetadata {
	id: number;
	created_at: string;
	score: number;
	up_score: number;
	down_score: number;
	fav_count: number;
	/** "g" | "s" | "q" | "e" */
	rating: string;
	image_width: number;
	image_height: number;
	md5: string;
	file_ext: string;
	file_url: string;
	large_file_url: string;
	preview_file_url: string;
	/** スペース区切りの全タグ文字列 */
	tag_string: string;
	tag_string_general: string;
	tag_string_character: string;
	tag_string_copyright: string;
	tag_string_artist: string;
	tag_string_meta: string;
	tag_count: number;
	pixiv_id: number | null;
	source: string;
	media_asset: {
		variants: Array<{
			type: string;
			url: string;
			width: number;
			height: number;
			file_ext: string;
		}>;
		[key: string]: unknown;
	};
	[key: string]: unknown;
}

/** Danbooru ランキング取得時のコンテキスト */
export interface DanbooruRankingContext {
	/** 常に "popular" */
	mode: string;
	/** ランキング日付 "YYYY-MM-DD" */
	date: string;
	/** ページ番号（0始まり） */
	page: number;
	/** ページ内インデックス（0始まり） */
	index: number;
	/** 集計スケール。常に "day" */
	scale: string;
}

/** Gelbooru ポストAPIのレスポンス（index.php?page=dapi&s=post の1要素） */
export interface GelbooruPostMetadata {
	id: number;
	/** "Tue May 12 07:45:22 -0500 2026" 形式 */
	created_at: string;
	score: number;
	width: number;
	height: number;
	md5: string;
	/** スペース区切りのタグ文字列 */
	tags: string;
	/** "general" | "sensitive" | "questionable" | "explicit" */
	rating: string;
	source: string;
	file_url: string;
	preview_url: string;
	sample_url: string;
	sample_width: number;
	sample_height: number;
	[key: string]: unknown;
}

/** Gelbooru 検索取得時のコンテキスト */
export interface GelbooruQueryContext {
	/** 検索タグ。現在は "score:>1" 固定 */
	tags: string;
	/** ページ番号（0始まり） */
	page: number;
	/** ページ内インデックス（0始まり） */
	index: number;
}

/** Sankaku タグオブジェクト */
export interface SankakuTag {
	id: string;
	/** 英語タグ名 */
	name_en: string;
	name_ja: string | null;
	/** タグ種別: 1=artist, 3=copyright, 4=character, 5=general */
	type: number;
	tagName: string;
	post_count: number;
	[key: string]: unknown;
}

/** Sankaku ポストAPIのレスポンス（/v2/posts/keyset の data[] 要素） */
export interface SankakuPostMetadata {
	id: string;
	/** "s" | "q" | "e" */
	rating: string;
	status: string;
	author: {id: string; name: string; display_name: string; level: number};
	file_url: string;
	sample_url: string;
	preview_url: string;
	width: number;
	height: number;
	file_size: number;
	file_ext: string;
	file_type: string;
	/** Unix時刻 。 `created_at.s` でエポック秒を取得 */
	created_at: {json_class: string; s: number; n: number};
	md5: string;
	fav_count: number;
	vote_count: number;
	total_score: number;
	tags: SankakuTag[];
	/** タグ名の文字列配列（tag.tagName の集合） */
	tag_names: string[];
	total_tags: number;
	[key: string]: unknown;
}

/** Sankaku 検索取得時のコンテキスト */
export interface SankakuQueryContext {
	/** APIに渡した完全なタグクエリ文字列 */
	tags: string;
	/** メインクロール以外で指定した追加タグ。メインクロール時は null */
	additionalTag: string | null;
	/** クロール対象日付 "YYYY-MM-DD" */
	targetDate: string;
	/** ページ番号（0始まり） */
	page: number;
	/** ページ内インデックス（0始まり） */
	index: number;
}

/** images コレクションの metadata フィールド */
export type ImageMetadata =
	| {pixiv: PixivArtworkMetadata; pixivRanking: PixivRankingContext}
	| {danbooru: DanbooruPostMetadata; danbooruRanking: DanbooruRankingContext}
	| {gelbooru: GelbooruPostMetadata; gelbooruQuery: GelbooruQueryContext}
	| {sankaku: SankakuPostMetadata; sankakuQuery: SankakuQueryContext};

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
	metadata?: ImageMetadata;
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
