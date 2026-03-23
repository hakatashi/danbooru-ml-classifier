import fs from 'fs';
import path, {extname} from 'path';
import axios from './axios';
import {IMAGE_CACHE_DIR} from './config';
import dayjs from './dayjs';
import {getDb} from './db';

const SUPPORTED_EXTENSIONS = ['.jpg', '.png', '.gif', '.tiff', '.jpeg'];

const sleep = (ms: number) => new Promise<void>((resolve) => {
	setTimeout(resolve, ms);
});

export const fetchDanbooruDailyRankings = async (): Promise<void> => {
	const danbooruApiUser = process.env.DANBOORU_API_USER;
	const danbooruApiKey = process.env.DANBOORU_API_KEY;
	if (!danbooruApiUser || !danbooruApiKey) {
		throw new Error('DANBOORU_API_USER or DANBOORU_API_KEY is not set');
	}

	const db = await getDb();
	const danbooruRankingCollection = db.collection<{_id: string}>('danbooruRanking');
	const imagesCollection = db.collection('images');

	const dateString = dayjs().tz('Asia/Tokyo').subtract(2, 'days').format('YYYY-MM-DD');
	const mode = 'popular';

	console.log(`[Danbooru] Fetching rankings for ${dateString}...`);

	for (const page of Array(100).keys()) {
		console.log(`[Danbooru] Fetching ranking page ${page + 1}/100...`);
		await sleep(5000);

		let posts: Record<string, unknown>[];
		try {
			const {data, status} = await axios.get('https://danbooru.donmai.us/explore/posts/popular.json', {
				params: {
					login: danbooruApiUser,
					api_key: danbooruApiKey,
					date: dateString,
					page: page + 1,
					scale: 'day',
				},
				validateStatus: null,
			});

			if (status !== 200) {
				console.warn(`[Danbooru] Failed to fetch ranking page ${page + 1} (status = ${status})`);
				continue;
			}
			posts = data as typeof posts;
		} catch (error) {
			console.error(`[Danbooru] Error fetching ranking page ${page + 1}:`, error);
			continue;
		}

		if (!Array.isArray(posts) || posts.length === 0) {
			console.log(`[Danbooru] No posts on page ${page + 1}, stopping`);
			break;
		}

		console.log(`[Danbooru] Fetched ranking page ${page + 1} (count = ${posts.length})`);

		for (const [index, post] of posts.entries()) {
			const postId = post.id as number;
			const rankingId = `${dateString}-${mode}-${postId}`;

			await danbooruRankingCollection.updateOne(
				{_id: rankingId},
				{$set: {post, ranking: {date: dateString, page, mode, index}}},
				{upsert: true},
			);

			const existing = await imagesCollection.findOne({type: 'danbooru', postId});
			if (existing) {
				continue;
			}

			const url = post.file_url as string | undefined;
			if (typeof url !== 'string') {
				console.warn(`[Danbooru] No file_url for post ${postId}`);
				continue;
			}

			const extension = extname(url);
			if (!SUPPORTED_EXTENSIONS.includes(extension)) {
				console.log(`[Danbooru] Unsupported extension ${extension} for post ${postId}`);
				continue;
			}

			const filename = `${postId}${extension}`;
			const key = `danbooru/${filename}`;

			console.log(`[Danbooru] Downloading post ${postId}...`);
			await sleep(1000);

			let imageBuffer: Uint8Array;
			let contentType: string;
			try {
				const response = await axios.get(url, {responseType: 'arraybuffer'});
				imageBuffer = new Uint8Array(response.data as ArrayBuffer);
				contentType = String(response.headers['content-type'] ?? 'application/octet-stream');
			} catch (error) {
				console.error(`[Danbooru] Error downloading post ${postId}:`, error);
				continue;
			}

			const dirPath = path.join(IMAGE_CACHE_DIR, 'danbooru');
			fs.mkdirSync(dirPath, {recursive: true});
			const filePath = path.join(dirPath, filename);
			fs.writeFileSync(filePath, imageBuffer);
			console.log(`[Danbooru] Saved ${filename} to ${filePath}`);

			await imagesCollection.updateOne(
				{key},
				{
					$set: {
						status: 'pending',
						type: 'danbooru',
						postId,
						date: dateString,
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

	console.log('[Danbooru] Done');
};
