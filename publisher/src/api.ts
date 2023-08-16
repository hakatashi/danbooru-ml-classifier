import {getFirestore} from 'firebase-admin/firestore';
import {getStorage} from 'firebase-admin/storage';
import {defineSecret} from 'firebase-functions/params';
import {https} from 'firebase-functions/v2';

const hakatashiApiKey = defineSecret('HAKATASHI_API_KEY');

export const getTopImages = https.onRequest(
	{cors: ['localhost', 'archive.hakatashi.com']},
	async (req, res) => {
		const {date, model: rawModel = '', category: rawCategory = '', apikey: rawApikey = ''} = req.query;
		if (rawApikey.toString() !== hakatashiApiKey.value()) {
			res.status(403).send('Invalid API key');
			return;
		}

		const model = rawModel.toString();
		const category = rawCategory.toString();
		if (!model.match(/^[0-9a-z_]+$/i) || !category.match(/^[0-9a-z_]+$/i)) {
			res.status(400).send('Invalid model or category');
			return;
		}

		const db = getFirestore();
		const result = await db.collection('images')
			.where('date', '==', date)
			.orderBy(`inferences.${model}.${category}`, 'desc')
			.limit(100)
			.get();

		const storage = getStorage();
		const bucket = storage.bucket('danbooru-ml-classifier-images');

		const images = result.docs.map(async (doc) => {
			const data = doc.data();
			const [url] = await bucket.file(data.key).getSignedUrl({
				version: 'v4',
				action: 'read',
				expires: Date.now() + 15 * 60 * 1000,
			});
			return {
				...data,
				url,
				score: data.inferences[model][category],
			};
		});

		res.json(await Promise.all(images));
	},
);
