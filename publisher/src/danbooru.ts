import assert from 'assert';
import {extname} from 'path';
import axios from 'axios';
import axiosRetry from 'axios-retry';
import * as firebase from 'firebase-admin';
import {getFirestore} from 'firebase-admin/firestore';
import {getFunctions} from 'firebase-admin/functions';
import {getStorage} from 'firebase-admin/storage';
import {info, warn} from 'firebase-functions/logger';
import {defineSecret} from 'firebase-functions/params';
import {tasks} from 'firebase-functions/v2';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';
import {onSchedule} from 'firebase-functions/v2/scheduler';
import dayjs from './dayjs';

// TODO: Move to common file
axiosRetry(axios, {
	retries: 3,
	retryDelay() {
		return 5000;
	},
	retryCondition(error) {
		return error.response?.status === 429;
	},
});

const danbooruApiUser = defineSecret('DANBOORU_API_USER');
const danbooruApiKey = defineSecret('DANBOORU_API_KEY');

const escapeFirestoreKey = (key: string) => (
	key
		.replaceAll(/%/g, '%25')
		.replaceAll(/\//g, '%2F')
		.replaceAll(/\./g, '%2E')
);

export const downloadDanbooruImage = tasks.onTaskDispatched(
	{
		retryConfig: {
			maxAttempts: 3,
			maxBackoffSeconds: 10,
		},
		rateLimits: {
			maxConcurrentDispatches: 1,
			maxDispatchesPerSecond: 0.1,
		},
		secrets: [danbooruApiUser, danbooruApiKey],
	},
	async (req) => {
		const {postId, date} = req.data as {postId: number, date: string};
		const db = getFirestore();

		const imageDoc = db.collection('images')
			.where('type', '==', 'danbooru')
			.where('postId', '==', postId);
		const imageDocSnapshot = await imageDoc.get();
		if (!imageDocSnapshot.empty) {
			info(`Post ${postId} already exists`);
			return;
		}

		const rankingInfo = await db.collection('danbooruRanking').doc(`${date}-popular-${postId}`).get();
		const rankingInfoData = rankingInfo.data();
		assert(rankingInfoData, 'Ranking info not found');

		info(`Downloading artwork ${postId}`);
		const url = rankingInfoData.post.file_url;
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
		const file = bucket.file(`danbooru/${filename}`);

		await file.save(response.data, {
			metadata: {
				contentType: response.headers['content-type'],
			},
		});

		info(`Saving ${filename} to firestore`);
		await db.collection('images').doc(escapeFirestoreKey(`danbooru/${filename}`)).set({
			status: 'pending',
			type: 'danbooru',
			postId,
			date,
			originalUrl: url,
			key: `danbooru/${filename}`,
			downloadedAt: firebase.firestore.FieldValue.serverTimestamp(),
			inferences: {},
			topTagProbs: {},
		});
	},
);

export const onDanbooruRankingArtworkCreated = onDocumentCreated('danbooruRanking/{rankingId}', async (event) => {
	if (!event.data) {
		return;
	}

	const ranking = event.data.data();

	const db = getFirestore();

	const imageDoc = db.collection('images')
		.where('type', '==', 'danbooru')
		.where('postId', '==', ranking.post.id);
	const imageDocSnapshot = await imageDoc.get();
	if (!imageDocSnapshot.empty) {
		info(`Post ${ranking.post.id} already exists`);
		return;
	}

	const queue = getFunctions().taskQueue('downloadDanbooruImage');

	await queue.enqueue({
		postId: ranking.post.id,
		date: ranking.ranking.date,
	}, {
		scheduleDelaySeconds: 0,
		dispatchDeadlineSeconds: 60 * 5,
	});
});

export const fetchDanbooruDailyRankings = onSchedule({
	schedule: 'every day 15:00',
	timeZone: 'Asia/Tokyo',
	secrets: [danbooruApiKey, danbooruApiUser],
	timeoutSeconds: 540,
}, async (event) => {
	const db = getFirestore();

	const dateString = dayjs(event.scheduleTime).tz('Asia/Tokyo').subtract(2, 'days').format('YYYY-MM-DD');
	const mode = 'popular';

	info(`Fetching danbooru ranking for ${dateString}...`);

	for (const page of Array(100).keys()) {
		info(`Fetching danbooru ranking page ${page + 1}...`);
		await new Promise((resolve) => {
			setTimeout(resolve, 5000);
		});

		const {data: posts, status} = await axios.get('https://danbooru.donmai.us/explore/posts/popular.json', {
			params: {
				login: danbooruApiUser.value(),
				api_key: danbooruApiKey.value(),
				date: dateString,
				page: page + 1,
				scale: 'day',
			},
			validateStatus: null,
		});

		if (status !== 200) {
			warn(`Failed to fetch danbooru ranking page ${page + 1} (status = ${status})`);
			continue;
		}

		info(`Fetched danbooru ranking page ${page + 1} (count = ${posts.length})`);

		const batch = db.batch();

		for (const [index, post] of posts.entries()) {
			const rankingId = `${dateString}-${mode}-${post.id}`;
			const rankingRef = db.collection('danbooruRanking').doc(rankingId);
			batch.set(rankingRef, {
				post,
				ranking: {
					date: dateString,
					page,
					mode,
					index,
				},
			});
		}

		await batch.commit();
	}
});
