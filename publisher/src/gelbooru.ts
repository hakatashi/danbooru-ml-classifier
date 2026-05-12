import crypto from 'crypto';
import fs from 'fs';
import path, {extname} from 'path';
import querystring from 'querystring';
import {imageSize} from 'image-size';
import {IMAGE_CACHE_DIR} from './config';
import dayjs from './dayjs';
import {getDb} from './db';

// Gelbooru is behind Cloudflare which blocks axios (JA3 fingerprint).
// Use the built-in fetch (undici) which is accepted.
const gelbooruFetch = (url: string): Promise<Response> => fetch(url, {
	headers: {
		'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
		Accept: 'application/json, text/plain, */*',
		Referer: 'https://gelbooru.com/',
	},
});

const SUPPORTED_EXTENSIONS = ['.jpg', '.png', '.gif', '.tiff', '.jpeg'];

const sleep = (ms: number) => new Promise<void>((resolve) => {
	setTimeout(resolve, ms);
});

export const fetchGelbooruDailyImages = async (): Promise<void> => {
	const gelbooruApiUser = process.env.GELBOORU_API_USER;
	const gelbooruApiKey = process.env.GELBOORU_API_KEY;
	if (!gelbooruApiUser || !gelbooruApiKey) {
		throw new Error('GELBOORU_API_USER or GELBOORU_API_KEY is not set');
	}

	const db = await getDb();
	const gelbooruImageCollection = db.collection<{_id: string}>('gelbooruImage');
	const imagesCollection = db.collection('images');

	console.log('[Gelbooru] Fetching images...');

	for (const page of Array(20).keys()) {
		console.log(`[Gelbooru] Fetching page ${page + 1}/20...`);
		await sleep(10000);

		let posts: Record<string, unknown>[] = [];
		try {
			const qs = querystring.stringify({
				page: 'dapi',
				s: 'post',
				q: 'index',
				tags: 'score:>1',
				limit: 100,
				pid: page,
				user_id: gelbooruApiUser,
				api_key: gelbooruApiKey,
				json: 1,
			});
			const response = await gelbooruFetch(`https://gelbooru.com/index.php?${qs}`);

			if (!response.ok) {
				console.warn(`[Gelbooru] Failed to fetch page ${page + 1} (status = ${response.status})`);
				continue;
			}

			const data = await response.json() as {post: Record<string, unknown>[]};
			posts = data?.post;
		} catch (error) {
			console.error(`[Gelbooru] Error fetching page ${page + 1}:`, error);
			continue;
		}

		if (!Array.isArray(posts) || posts.length === 0) {
			console.warn(`[Gelbooru] No posts found on page ${page + 1}`);
			continue;
		}

		console.log(`[Gelbooru] Fetched page ${page + 1} (count = ${posts.length})`);

		for (const [index, post] of posts.entries()) {
			const postId = post.id as number;
			const date = dayjs(post.created_at as string).tz('Asia/Tokyo').format('YYYY-MM-DD');

			await gelbooruImageCollection.updateOne(
				{_id: postId.toString()},
				{$set: {post, image: {date, page, index}}},
				{upsert: true},
			);

			const existing = await imagesCollection.findOne({type: 'gelbooru', postId});
			if (existing) {
				continue;
			}

			const url = post.file_url as string | undefined;
			if (typeof url !== 'string') {
				console.warn(`[Gelbooru] No file_url for post ${postId}`);
				continue;
			}

			const extension = extname(url);
			if (!SUPPORTED_EXTENSIONS.includes(extension)) {
				console.log(`[Gelbooru] Unsupported extension ${extension} for post ${postId}`);
				continue;
			}

			const filename = `${postId}${extension}`;
			const key = `gelbooru/${filename}`;

			console.log(`[Gelbooru] Downloading post ${postId}...`);
			await sleep(1000);

			let imageBuffer: Uint8Array = new Uint8Array();
			let contentType = '';
			try {
				const response = await gelbooruFetch(url);
				if (!response.ok) {
					console.error(`[Gelbooru] Error downloading post ${postId} (status = ${response.status})`);
					continue;
				}
				imageBuffer = new Uint8Array(await response.arrayBuffer());
				contentType = response.headers.get('content-type') ?? 'application/octet-stream';
			} catch (error) {
				console.error(`[Gelbooru] Error downloading post ${postId}:`, error);
				continue;
			}

			let width: number | undefined;
			let height: number | undefined;
			try {
				const dimensions = imageSize(imageBuffer);
				width = dimensions.width;
				height = dimensions.height;
			} catch (error) {
				console.warn(`[Gelbooru] Could not get dimensions for ${filename}:`, error);
			}

			const fileSize = imageBuffer.byteLength;
			const sha256 = crypto.createHash('sha256').update(imageBuffer).digest('hex');

			const dirPath = path.join(IMAGE_CACHE_DIR, 'gelbooru');
			const filePath = path.join(dirPath, filename);

			const duplicate = await imagesCollection.findOne({sha256, key: {$ne: key}});
			if (duplicate === null) {
				await fs.promises.mkdir(dirPath, {recursive: true});
				await fs.promises.writeFile(filePath, imageBuffer);
				console.log(`[Gelbooru] Saved ${filename} to ${filePath}`);
			}

			await imagesCollection.updateOne(
				{key},
				{
					$set: {
						status: duplicate ? 'deduped' : 'pending',
						type: 'gelbooru',
						postId,
						date,
						originalUrl: url,
						contentType,
						key,
						localPath: duplicate ? null : filePath,
						downloadedAt: new Date(),
						inferences: {},
						topTagProbs: {},
						fileSize,
						sha256,
						...(width !== undefined && height !== undefined ? {width, height} : {}),
						metadata: {
						gelbooru: post,
						gelbooruQuery: {tags: 'score:>1', page, index},
					},
					},
				},
				{upsert: true},
			);
		}
	}

	console.log('[Gelbooru] Done');
};
