import assert from 'assert';
import {extname} from 'path';
import * as firebase from 'firebase-admin';
import {getFirestore} from 'firebase-admin/firestore';
import {getFunctions} from 'firebase-admin/functions';
import {getStorage} from 'firebase-admin/storage';
import {info, warn} from 'firebase-functions/logger';
import {defineSecret} from 'firebase-functions/params';
import {tasks} from 'firebase-functions/v2';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';
import {onSchedule} from 'firebase-functions/v2/scheduler';
import axios from './axios';
import dayjs from './dayjs';

const gelbooruApiUser = defineSecret('GELBOORU_API_USER');
const gelbooruApiKey = defineSecret('GELBOORU_API_KEY');

const escapeFirestoreKey = (key: string) => (
	key
		.replaceAll(/%/g, '%25')
		.replaceAll(/\//g, '%2F')
		.replaceAll(/\./g, '%2E')
);

export const downloadGelbooruImage = tasks.onTaskDispatched(
	{
		retryConfig: {
			maxAttempts: 3,
			maxBackoffSeconds: 10,
		},
		rateLimits: {
			maxConcurrentDispatches: 1,
			maxDispatchesPerSecond: 0.1,
		},
		secrets: [gelbooruApiUser, gelbooruApiKey],
	},
	async (req) => {
		const {postId, date} = req.data as {postId: number, date: string};
		const db = getFirestore();

		const imageDoc = db.collection('images')
			.where('type', '==', 'gelbooru')
			.where('postId', '==', postId);
		const imageDocSnapshot = await imageDoc.get();
		if (!imageDocSnapshot.empty) {
			info(`Post ${postId} already exists`);
			return;
		}

		const imageInfo = await db.collection('gelbooruImage').doc(postId.toString()).get();
		const imageInfoData = imageInfo.data();
		assert(imageInfoData, 'Image info not found');

		info(`Downloading artwork ${postId}`);
		const url = imageInfoData.post.file_url;
		if (typeof url !== 'string') {
			warn(`No file_url for post ${postId}`);
			return;
		}
		const extension = extname(url);

		if (!['.jpg', '.png', '.gif', '.tiff', '.jpeg'].includes(extension)) {
			info(`Unsupported extension ${extension}`);
			return;
		}

		const filename = `${postId}${extension}`;

		const response = await axios.get(url, {
			responseType: 'arraybuffer',
		});

		info(`Downloaded ${url}`);

		info(`Uploading ${filename} to storage`);
		const storage = getStorage();
		const bucket = storage.bucket('danbooru-ml-classifier-images');
		const file = bucket.file(`gelbooru/${filename}`);

		await file.save(response.data, {
			metadata: {
				contentType: response.headers['content-type'],
			},
		});

		info(`Saving ${filename} to firestore`);
		await db.collection('images').doc(escapeFirestoreKey(`gelbooru/${filename}`)).set({
			status: 'pending',
			type: 'gelbooru',
			postId,
			date,
			originalUrl: url,
			key: `gelbooru/${filename}`,
			downloadedAt: firebase.firestore.FieldValue.serverTimestamp(),
			inferences: {},
			topTagProbs: {},
		});
	},
);

export const onGelbooruImageCreated = onDocumentCreated('gelbooruImage/{imageId}', async (event) => {
	if (!event.data) {
		return;
	}

	const image = event.data.data();

	const db = getFirestore();

	const imageDoc = db.collection('images')
		.where('type', '==', 'gelbooru')
		.where('postId', '==', image.post.id);
	const imageDocSnapshot = await imageDoc.get();
	if (!imageDocSnapshot.empty) {
		info(`Post ${image.post.id} already exists`);
		return;
	}

	const queue = getFunctions().taskQueue('downloadGelbooruImage');

	await queue.enqueue({
		postId: image.post.id,
		date: image.image.date,
	}, {
		scheduleDelaySeconds: 0,
		dispatchDeadlineSeconds: 60 * 5,
	});
});

export const fetchGelbooruDailyImages = onSchedule({
	schedule: 'every day 15:00',
	timeZone: 'Asia/Tokyo',
	secrets: [gelbooruApiKey, gelbooruApiUser],
	timeoutSeconds: 540,
}, async () => {
	const db = getFirestore();

	for (const page of Array(20).keys()) {
		info(`Fetching gelbooru image page ${page + 1}...`);
		await new Promise((resolve) => {
			setTimeout(resolve, 10000);
		});

		const {data, status} = await axios.get('https://gelbooru.com/index.php', {
			params: {
				page: 'dapi',
				s: 'post',
				q: 'index',
				tags: 'score:>1',
				limit: '100',
				pid: page + 1,
				user_id: gelbooruApiUser.value(),
				api_key: gelbooruApiKey.value(),
				json: '1',
			},
			validateStatus: null,
		});

		if (status !== 200) {
			warn(`Failed to fetch gelbooru image page ${page + 1} (status = ${status})`);
			continue;
		}

		const posts = data?.post;

		if (!Array.isArray(posts) || posts.length === 0) {
			warn(`No posts found on gelbooru image page ${page + 1}`);
			continue;
		}

		info(`Fetched gelbooru image page ${page + 1} (count = ${posts.length})`);

		const batch = db.batch();

		for (const [index, post] of posts.entries()) {
			const date = dayjs(post.created_at).tz('Asia/Tokyo').format('YYYY-MM-DD');
			const imageRef = db.collection('gelbooruImage').doc(post.id.toString());
			batch.set(imageRef, {
				post,
				image: {
					date,
					page,
					index,
				},
			});
		}

		await batch.commit();
	}
});
