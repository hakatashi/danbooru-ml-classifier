import {basename} from 'path';
import axios from 'axios';
import * as firebase from 'firebase-admin';
import {getFirestore} from 'firebase-admin/firestore';
import {getFunctions} from 'firebase-admin/functions';
import {getStorage} from 'firebase-admin/storage';
import {info} from 'firebase-functions/logger';
import {defineSecret} from 'firebase-functions/params';
import {tasks} from 'firebase-functions/v2';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';
import {onSchedule} from 'firebase-functions/v2/scheduler';
import dayjs from './dayjs';

const pixivSessionId = defineSecret('PIXIV_SESSION_ID');
const USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36';

const escapeFirestoreKey = (key: string) => (
	key
		.replaceAll(/%/g, '%25')
		.replaceAll(/\//g, '%2F')
		.replaceAll(/\./g, '%2E')
);

export const downloadPixivImage = tasks.onTaskDispatched(
	{
		retryConfig: {
			maxAttempts: 3,
			maxBackoffSeconds: 10,
		},
		rateLimits: {
			maxConcurrentDispatches: 1,
			maxDispatchesPerSecond: 0.1,
		},
		secrets: [pixivSessionId],
	},
	async (req) => {
		const {artworkId, page, date} = req.data as {artworkId: number, page: number, date: string};
		const db = getFirestore();

		const imageDoc = db.collection('images')
			.where('type', '==', 'pixiv')
			.where('artworkId', '==', artworkId)
			.where('page', '==', page);
		const imageDocSnapshot = await imageDoc.get();
		if (!imageDocSnapshot.empty) {
			info(`Image ${artworkId} page ${page} already exists`);
			return;
		}

		info(`Downloading artwork ${artworkId} page ${page}`);

		const pages = await db.runTransaction(async (transaction) => {
			const pixivPages = await transaction.get(db.collection('pixivPages').doc(artworkId.toString()));
			const pixivPagesData = pixivPages.data();

			if (pixivPagesData) {
				return pixivPagesData.pages;
			}

			info(`Fetching pages for artwork ${artworkId}`);
			const {data: pagesData} = await axios.get(`https://www.pixiv.net/ajax/illust/${artworkId}/pages`, {
				headers: {
					Cookie: `PHPSESSID=${pixivSessionId.value()}`,
					'User-Agent': USER_AGENT,
				},
			});
			if (pagesData.error) {
				throw new Error(`Unable to fetch pages for artwork ${artworkId}: ${pagesData.message}`);
			}
			if (!Array.isArray(pagesData.body) || pagesData.body.length === 0) {
				throw new Error(`Unable to fetch pages for artwork ${artworkId}: ${pagesData}`);
			}

			transaction.set(pixivPages.ref, {
				pages: pagesData.body,
			});

			return pagesData.body;
		});

		if (page >= pages.length) {
			throw new Error(`Page ${page} is out of range for artwork ${artworkId}`);
		}

		info(`Downloading page ${page} for artwork ${artworkId}`);
		const pageData = pages[page];
		const url = pageData.urls.original;
		const filename = basename(url);
		const response = await axios.get(url, {
			responseType: 'arraybuffer',
			headers: {
				Cookie: `PHPSESSID=${pixivSessionId.value()}`,
				'User-Agent': USER_AGENT,
				Referer: 'https://www.pixiv.net/',
			},
		});

		info(`Downloaded ${url}`);

		info(`Uploading ${filename} to storage`);
		const storage = getStorage();
		const bucket = storage.bucket('danbooru-ml-classifier-images');
		const file = bucket.file(`pixiv/${filename}`);

		await file.save(response.data, {
			metadata: {
				contentType: response.headers['content-type'],
			},
		});

		info(`Saving ${filename} to firestore`);
		await db.collection('images').doc(escapeFirestoreKey(`pixiv/${filename}`)).set({
			status: 'pending',
			type: 'pixiv',
			artworkId,
			page,
			date,
			originalUrl: url,
			key: `pixiv/${filename}`,
			downloadedAt: firebase.firestore.FieldValue.serverTimestamp(),
			inferences: {},
			topTagProbs: {},
		});
	},
);

export const onPixivRankingArtworkCreated = onDocumentCreated('pixivRanking/{rankingId}', async (event) => {
	if (!event.data) {
		return;
	}

	const ranking = event.data.data();
	const db = getFirestore();

	const imageDoc = db.collection('images')
		.where('type', '==', 'pixiv')
		.where('postId', '==', ranking.artwork.illust_id);
	const imageDocSnapshot = await imageDoc.get();

	const pageCount = parseInt(ranking.artwork.illust_page_count) || 1;
	const queue = getFunctions().taskQueue('downloadPixivImage');
	const date = dayjs(ranking.ranking.date, 'YYYYMMDD').format('YYYY-MM-DD');

	for (const page of Array(pageCount).keys()) {
		const isAlreadyDownloaded = imageDocSnapshot.docs.some((doc) => doc.data().page === page);
		if (isAlreadyDownloaded) {
			info(`Page ${page} of post ${ranking.artwork.illust_id} already exists`);
			continue;
		}
		await queue.enqueue({
			artworkId: ranking.artwork.illust_id,
			page,
			date,
		}, {
			scheduleDelaySeconds: 0,
			dispatchDeadlineSeconds: 60 * 5,
		});
	}
});

export const fetchPixivDailyRankings = onSchedule({
	schedule: 'every day 15:00',
	timeZone: 'Asia/Tokyo',
	secrets: [pixivSessionId],
	timeoutSeconds: 300,
}, async (event) => {
	const db = getFirestore();

	const dateString = dayjs(event.scheduleTime).tz('Asia/Tokyo').subtract(2, 'days').format('YYYYMMDD');
	const rankings = [
		['daily', 8],
		['male_r18', 6],
		['female_r18', 6],
	];

	info(`Fetching pixiv rankings for ${dateString}...`);

	for (const [mode, pageCount] of rankings) {
		for (const page of Array(pageCount).keys()) {
			info(`Fetching ${mode} ranking page ${page + 1}...`);
			await new Promise((resolve) => {
				setTimeout(resolve, 10000);
			});
			const {data} = await axios.get('https://www.pixiv.net/ranking.php', {
				params: {
					format: 'json',
					mode,
					date: dateString,
					p: page + 1,
					content: 'all',
				},
				headers: {
					Cookie: `PHPSESSID=${pixivSessionId.value()}`,
					'User-Agent': USER_AGENT,
				},
			});

			info(`Fetched ${mode} ranking page ${page + 1} (count = ${data.contents.length})`);

			const batch = db.batch();

			for (const artwork of data.contents) {
				const rankingId = `${dateString}-${mode}-${artwork.illust_id}`;
				const rankingRef = db.collection('pixivRanking').doc(rankingId);
				batch.set(rankingRef, {
					artwork,
					ranking: {
						date: data.date,
						mode,
						page,
					},
				});
			}

			await batch.commit();
		}
	}
});
