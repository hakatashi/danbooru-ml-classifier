import fs from 'fs';
import path, {extname} from 'path';
import axios from './axios';
import {IMAGE_CACHE_DIR} from './config';
import dayjs from './dayjs';
import {getDb} from './db';

const SUPPORTED_EXTENSIONS = ['.jpg', '.png', '.gif', '.tiff', '.jpeg', '.webp'];

const SANKAKU_API_BASE = 'https://sankakuapi.com';

const SANKAKU_HEADERS = {
	Accept: 'application/vnd.sankaku.api+json;v=2',
	Origin: 'https://www.sankakucomplex.com',
	Referer: 'https://www.sankakucomplex.com/',
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
};

const SANKAKU_CRAWL_DEFAULT_PAGES = 20;
const SANKAKU_CRAWL_ADDITIONAL_PAGES = 2;
const SANKAKU_CRAWL_ADDITIONAL_TAGS = (process.env.SANKAKU_CRAWL_ADDITIONAL_TAGS ?? '').split(',');

const sleep = (ms: number) => new Promise<void>((resolve) => {
	setTimeout(resolve, ms);
});

interface SankakuAuthResponse {
	success: boolean;
	access_token: string;
	error?: string;
}

interface SankakuPost {
	id: string;
	file_url: string | null;
	created_at: {json_class: string; s: number; n: number};
	file_type: string;
	file_size: number;
	status: string;
	rating: string;
}

const authenticate = async (login: string, password: string): Promise<string> => {
	const {data, status} = await axios.post<SankakuAuthResponse>(
		`${SANKAKU_API_BASE}/auth/token`,
		{login, password},
		{
			headers: SANKAKU_HEADERS,
			validateStatus: null,
		},
	);

	if (status >= 400 || !data.success) {
		throw new Error(`[Sankaku] Authentication failed (status = ${status}, error = ${data.error ?? 'unknown'})`);
	}

	return `Bearer ${data.access_token}`;
};

export const fetchSankakuDailyImages = async (): Promise<void> => {
	const sankakuLogin = process.env.SANKAKU_USERNAME;
	const sankakuPassword = process.env.SANKAKU_PASSWORD;
	if (!sankakuLogin || !sankakuPassword) {
		throw new Error('SANKAKU_USERNAME or SANKAKU_PASSWORD is not set');
	}

	console.log('[Sankaku] Authenticating...');
	const authorization = await authenticate(sankakuLogin, sankakuPassword);
	console.log('[Sankaku] Authenticated successfully');

	const db = await getDb();
	const sankakuImageCollection = db.collection<{_id: string}>('sankakuImage');
	const imagesCollection = db.collection('images');

	const headers = {...SANKAKU_HEADERS, Authorization: authorization};

	console.log('[Sankaku] Fetching images...');

	const targetDate = dayjs().tz('Asia/Tokyo')
		.subtract(5, 'hour')
		.subtract(3, 'day')
		.format('YYYY-MM-DD');
	console.log(`[Sankaku] Target date: ${targetDate}`);

	for (const tag of [null, ...SANKAKU_CRAWL_ADDITIONAL_TAGS]) {
		console.log(`[Sankaku] Crawling with tag: ${tag ?? 'none'}...`);

		let next: string | null = null;
		const crawlPages = tag === null ? SANKAKU_CRAWL_DEFAULT_PAGES : SANKAKU_CRAWL_ADDITIONAL_PAGES;

		for (const page of Array(crawlPages).keys()) {
			console.log(`[Sankaku] Fetching page ${page + 1}/${crawlPages}... ${next ? `(next = ${next})` : ''}`);
			await sleep(5000);

			let posts: SankakuPost[] = [];
			try {
				const params: Record<string, string | number> = {
					lang: 'en',
					default_threshold: 0,
					limit: 100,
					page: page + 1,
					tags: `${tag ? `${tag} ` : ''}order:popularity threshold:0 file_type:image date:${targetDate}T15:00`,
				};
				if (next !== null) {
					params.next = next;
				}

				const {data, status} = await axios.get<{data: SankakuPost[]; meta: {next: string | null}}>(
					`${SANKAKU_API_BASE}/v2/posts/keyset`,
					{params, headers, validateStatus: null},
				);

				if (status !== 200) {
					console.warn(`[Sankaku] Failed to fetch page ${page + 1} (status = ${status})`);
					console.warn(`[Sankaku] Response data: ${JSON.stringify(data)}`);
					break;
				}

				posts = data.data ?? [];
				next = data.meta?.next ?? null;
			} catch (error) {
				console.error(`[Sankaku] Error fetching page ${page + 1}:`, error);
				break;
			}

			if (!Array.isArray(posts) || posts.length === 0) {
				console.log(`[Sankaku] No posts on page ${page + 1}, stopping`);
				break;
			}

			console.log(`[Sankaku] Fetched page ${page + 1} (count = ${posts.length})`);

			for (const [index, post] of posts.entries()) {
				const postId = post.id;
				const date = dayjs.unix(post.created_at.s).tz('Asia/Tokyo').format('YYYY-MM-DD');

				await sankakuImageCollection.updateOne(
					{_id: postId.toString()},
					{$set: {post, image: {date, page, index}}},
					{upsert: true},
				);

				const existing = await imagesCollection.findOne({type: 'sankaku', postId});
				if (existing) {
					continue;
				}

				const url = post.file_url;
				if (typeof url !== 'string') {
					console.warn(`[Sankaku] No file_url for post ${postId} (status = ${post.status})`);
					continue;
				}

				// Ensure HTTPS
				const secureUrl = url.startsWith('http:') ? `https${url.slice(4)}` : url;

				const extension = extname(new URL(secureUrl).pathname);
				if (!SUPPORTED_EXTENSIONS.includes(extension)) {
					console.log(`[Sankaku] Unsupported extension ${extension} for post ${postId}`);
					continue;
				}

				const filename = `${postId}${extension}`;
				const key = `sankaku/${filename}`;

				console.log(`[Sankaku] Downloading post ${postId}...`);
				await sleep(2000);

				let imageBuffer: Uint8Array = new Uint8Array();
				let contentType = '';
				try {
					const response = await axios.get(secureUrl, {
						responseType: 'arraybuffer',
						headers,
					});
					imageBuffer = new Uint8Array(response.data as ArrayBuffer);
					contentType = String(response.headers['content-type'] ?? 'application/octet-stream');
				} catch (error) {
					console.error(`[Sankaku] Error downloading post ${postId}:`, error);
					continue;
				}

				const dirPath = path.join(IMAGE_CACHE_DIR, 'sankaku');
				await fs.promises.mkdir(dirPath, {recursive: true});
				const filePath = path.join(dirPath, filename);
				await fs.promises.writeFile(filePath, imageBuffer);
				console.log(`[Sankaku] Saved ${filename} to ${filePath}`);

				await imagesCollection.updateOne(
					{key},
					{
						$set: {
							status: 'pending',
							type: 'sankaku',
							postId,
							date,
							originalUrl: secureUrl,
							contentType,
							key,
							localPath: filePath,
							downloadedAt: new Date(),
							inferences: {},
							topTagProbs: {},
						},
					},
					{upsert: true},
				);
			}

			if (next === null) {
				console.log('[Sankaku] No more pages');
				break;
			}
		}
	}

	console.log('[Sankaku] Done');
};
