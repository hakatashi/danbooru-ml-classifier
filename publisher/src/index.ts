// eslint-disable-next-line import/no-namespace
import * as assert from 'assert';
import {basename} from 'path';
import axios from 'axios';
// eslint-disable-next-line import/no-namespace
import * as firebase from 'firebase-admin';
import {getFirestore} from 'firebase-admin/firestore';
import {getFunctions} from 'firebase-admin/functions';
import {getStorage} from 'firebase-admin/storage';
import {info} from 'firebase-functions/logger';
import {defineSecret} from 'firebase-functions/params';
import {tasks} from 'firebase-functions/v2';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';

firebase.initializeApp();

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
		const {artworkId, page} = req.data as {artworkId: number; page: number};
		info(`Downloading and infering artwork ${artworkId} page ${page}`);

		const db = getFirestore();
		const artworkDataWithPages = await db.runTransaction(async (transaction) => {
			const artwork = await transaction.get(db.collection('pixivRanking').doc(artworkId.toString()));
			const artworkData = artwork.data();
			assert(artworkData !== undefined);

			if (Array.isArray(artworkData.pages)) {
				return artworkData;
			}

			info(`Fetching pages for artwork ${artworkId}`);
			const {data: pagesData} = await axios.get(`https://www.pixiv.net/ajax/illust/${artworkData.illust_id}/pages`, {
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

			transaction.update(artwork.ref, {
				pages: pagesData.body,
			});

			return {...artworkData, pages: pagesData.body};
		});

		if (page >= artworkDataWithPages.pages.length) {
			throw new Error(`Page ${page} is out of range for artwork ${artworkId}`);
		}

		info(`Downloading page ${page} for artwork ${artworkId}`);
		const pageData = artworkDataWithPages.pages[page];
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
		await db.collection('images').doc(escapeFirestoreKey(filename)).set({
			status: 'pending',
			artworkId,
			page,
			originalUrl: url,
			filename,
			date: artworkDataWithPages.date,
			inferences: {},
		});
	},
);

export const onPixivRankingArtworkCreated = onDocumentCreated('pixivRanking/{artworkId}', async (event) => {
	if (!event.data) {
		return;
	}

	const artwork = event.data.data();
	const pageCount = parseInt(artwork.illust_page_count) || 1;
	const queue = getFunctions().taskQueue('downloadPixivImage');

	for (const page of Array(pageCount).keys()) {
		await queue.enqueue({
			artworkId: event.params.artworkId,
			page,
		}, {
			scheduleDelaySeconds: 0,
			dispatchDeadlineSeconds: 60 * 5,
		});
	}
});
