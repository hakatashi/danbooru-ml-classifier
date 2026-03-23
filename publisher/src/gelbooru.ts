import fs from 'fs';
import path from 'path';
import {extname} from 'path';
import axios from './axios';
import {IMAGE_CACHE_DIR} from './config';
import dayjs from './dayjs';
import {getDb} from './db';

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

		let posts: Record<string, unknown>[];
		try {
			const {data, status} = await axios.get('https://gelbooru.com/index.php', {
				params: {
					page: 'dapi',
					s: 'post',
					q: 'index',
					tags: 'score:>1',
					limit: '100',
					pid: page + 1,
					user_id: gelbooruApiUser,
					api_key: gelbooruApiKey,
					json: '1',
				},
				validateStatus: null,
			});

			if (status !== 200) {
				console.warn(`[Gelbooru] Failed to fetch page ${page + 1} (status = ${status})`);
				continue;
			}

			posts = (data as {post: Record<string, unknown>[]})?.post;
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

			let imageBuffer: Uint8Array;
			let contentType: string;
			try {
				const response = await axios.get(url, {responseType: 'arraybuffer'});
				imageBuffer = new Uint8Array(response.data as ArrayBuffer);
				contentType = String(response.headers['content-type'] ?? 'application/octet-stream');
			} catch (error) {
				console.error(`[Gelbooru] Error downloading post ${postId}:`, error);
				continue;
			}

			const dirPath = path.join(IMAGE_CACHE_DIR, 'gelbooru');
			fs.mkdirSync(dirPath, {recursive: true});
			const filePath = path.join(dirPath, filename);
			fs.writeFileSync(filePath, imageBuffer);
			console.log(`[Gelbooru] Saved ${filename} to ${filePath}`);

			await imagesCollection.updateOne(
				{key},
				{
					$set: {
						status: 'pending',
						type: 'gelbooru',
						postId,
						date,
						originalUrl: url,
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
	}

	console.log('[Gelbooru] Done');
};
