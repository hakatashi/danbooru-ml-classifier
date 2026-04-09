import fs from 'fs';
import path, {basename} from 'path';
import {imageSize} from 'image-size';
import axios from './axios';
import {IMAGE_CACHE_DIR} from './config';
import dayjs from './dayjs';
import {getDb} from './db';

interface PixivPagesDoc {
	_id: string;
	pages: Record<string, unknown>[];
}

const USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36';

const sleep = (ms: number) => new Promise<void>((resolve) => {
	setTimeout(resolve, ms);
});

const downloadPixivArtwork = async (
	artwork: Record<string, unknown>,
	rankingDate: string,
	pixivSessionId: string,
): Promise<void> => {
	const db = await getDb();
	const imagesCollection = db.collection('images');
	const pixivPagesCollection = db.collection<PixivPagesDoc>('pixivPages');

	const artworkId = artwork.illust_id as number;
	const pageCount = parseInt(String(artwork.illust_page_count)) || 1;
	const date = dayjs(rankingDate, 'YYYYMMDD').format('YYYY-MM-DD');

	let pagesEntry = await pixivPagesCollection.findOne({_id: artworkId.toString()});
	if (!pagesEntry) {
		console.log(`[Pixiv] Fetching pages for artwork ${artworkId}`);
		await sleep(2000);
		try {
			const {data} = await axios.get(`https://www.pixiv.net/ajax/illust/${artworkId}/pages`, {
				headers: {
					Cookie: `PHPSESSID=${pixivSessionId}`,
					'User-Agent': USER_AGENT,
				},
			});
			if (data.error || !Array.isArray(data.body) || data.body.length === 0) {
				console.error(`[Pixiv] Unable to fetch pages for artwork ${artworkId}: ${data.message ?? JSON.stringify(data)}`);
				return;
			}
			await pixivPagesCollection.updateOne(
				{_id: artworkId.toString()},
				{$set: {pages: data.body}},
				{upsert: true},
			);
			pagesEntry = {_id: artworkId.toString(), pages: data.body};
		} catch (error) {
			console.error(`[Pixiv] Error fetching pages for artwork ${artworkId}:`, error);
			return;
		}
	}

	if (!pagesEntry) {
		return;
	}

	const pages = pagesEntry.pages;

	for (const page of Array(pageCount).keys()) {
		const existing = await imagesCollection.findOne({type: 'pixiv', artworkId, page});
		if (existing) {
			continue;
		}

		if (page >= pages.length) {
			console.error(`[Pixiv] Page ${page} out of range for artwork ${artworkId}`);
			continue;
		}

		const pageData = pages[page] as {urls: {original: string}};
		const url = pageData.urls.original;
		const filename = basename(url);
		const key = `pixiv/${filename}`;

		console.log(`[Pixiv] Downloading artwork ${artworkId} page ${page}...`);
		await sleep(2000);

		let imageBuffer: Uint8Array = new Uint8Array();
		let contentType = '';
		try {
			const response = await axios.get(url, {
				responseType: 'arraybuffer',
				headers: {
					Cookie: `PHPSESSID=${pixivSessionId}`,
					'User-Agent': USER_AGENT,
					Referer: 'https://www.pixiv.net/',
				},
			});
			imageBuffer = new Uint8Array(response.data as ArrayBuffer);
			contentType = String(response.headers['content-type'] ?? 'application/octet-stream');
		} catch (error) {
			console.error(`[Pixiv] Error downloading artwork ${artworkId} page ${page}:`, error);
			continue;
		}

		const dirPath = path.join(IMAGE_CACHE_DIR, 'pixiv');
		await fs.promises.mkdir(dirPath, {recursive: true});
		const filePath = path.join(dirPath, filename);
		await fs.promises.writeFile(filePath, imageBuffer);
		console.log(`[Pixiv] Saved ${filename} to ${filePath}`);

		let width: number | undefined;
		let height: number | undefined;
		try {
			const dimensions = imageSize(imageBuffer);
			width = dimensions.width;
			height = dimensions.height;
		} catch (error) {
			console.warn(`[Pixiv] Could not get dimensions for ${filename}:`, error);
		}

		await imagesCollection.updateOne(
			{key},
			{
				$set: {
					status: 'pending',
					type: 'pixiv',
					artworkId,
					page,
					date,
					originalUrl: url,
					contentType,
					key,
					localPath: filePath,
					downloadedAt: new Date(),
					inferences: {},
					topTagProbs: {},
					...(width !== undefined && height !== undefined ? {width, height} : {}),
				},
			},
			{upsert: true},
		);
	}
};

export const fetchPixivDailyRankings = async (): Promise<void> => {
	const pixivSessionId = process.env.PIXIV_SESSION_ID;
	if (!pixivSessionId) {
		throw new Error('PIXIV_SESSION_ID is not set');
	}

	const db = await getDb();
	const pixivRankingCollection = db.collection<{_id: string}>('pixivRanking');

	const dateString = dayjs().tz('Asia/Tokyo').subtract(2, 'days').format('YYYYMMDD');
	const rankings: [string, number][] = [
		['daily', 8],
		['male_r18', 6],
		['female_r18', 6],
		['daily_ai', 1],
		['daily_r18_ai', 1],
	];

	console.log(`[Pixiv] Fetching rankings for ${dateString}...`);

	for (const [mode, pageCount] of rankings) {
		for (const page of Array(pageCount).keys()) {
			console.log(`[Pixiv] Fetching ${mode} ranking page ${page + 1}/${pageCount}...`);
			await sleep(10000);

			let data: {contents: Record<string, unknown>[], date: string} = {contents: [], date: ''};
			try {
				const response = await axios.get('https://www.pixiv.net/ranking.php', {
					params: {
						format: 'json',
						mode,
						date: dateString,
						p: page + 1,
						content: 'all',
					},
					headers: {
						Cookie: `PHPSESSID=${pixivSessionId}`,
						'User-Agent': USER_AGENT,
					},
				});
				data = response.data as typeof data;
			} catch (error) {
				console.error(`[Pixiv] Failed to fetch ${mode} ranking page ${page + 1}:`, error);
				continue;
			}

			if (!Array.isArray(data.contents)) {
				console.warn(`[Pixiv] No contents in ${mode} ranking page ${page + 1}`);
				continue;
			}

			console.log(`[Pixiv] Fetched ${mode} ranking page ${page + 1} (count = ${data.contents.length})`);

			for (const artwork of data.contents) {
				const rankingId = `${dateString}-${mode}-${String(artwork.illust_id)}`;
				await pixivRankingCollection.updateOne(
					{_id: rankingId},
					{$set: {artwork, ranking: {date: data.date, mode, page}}},
					{upsert: true},
				);

				await downloadPixivArtwork(artwork, data.date, pixivSessionId);
			}
		}
	}

	console.log('[Pixiv] Done');
};
