/**
 * Backfill width/height for existing images that have a local file but no dimensions.
 *
 * Usage:
 *   npx ts-node --project tsconfig.json scripts/backfill-image-dimensions.ts
 *
 * Optional env vars:
 *   MONGODB_URI - MongoDB URI (default: mongodb://localhost:27017)
 *   MONGODB_DB  - Database name (default: danbooru-ml-classifier)
 */

import 'dotenv/config';
import crypto from 'crypto';
import fs from 'fs';
import {imageSize} from 'image-size';
import {MongoClient} from 'mongodb';

const MONGODB_URI = process.env.MONGODB_URI ?? 'mongodb://localhost:27017';
const MONGODB_DB = process.env.MONGODB_DB ?? 'danbooru-ml-classifier';

const main = async (): Promise<void> => {
	const client = new MongoClient(MONGODB_URI);
	await client.connect();
	const db = client.db(MONGODB_DB);
	const imagesCollection = db.collection('images');

	const cursor = imagesCollection.find({
		localPath: {$exists: true},
		$or: [
			{width: {$exists: false}},
			{fileSize: {$exists: false}},
			{sha256: {$exists: false}},
		],
	});

	let processed = 0;
	let updated = 0;
	let skipped = 0;
	let errors = 0;

	for await (const doc of cursor) {
		processed++;
		const localPath = doc.localPath as string;

		if (!fs.existsSync(localPath)) {
			skipped++;
			continue;
		}

		try {
			const buffer = await fs.promises.readFile(localPath);

			const $set: Record<string, unknown> = {};

			if (!doc.width || !doc.height) {
				const dimensions = imageSize(buffer);
				if (dimensions.width !== undefined && dimensions.height !== undefined) {
					$set.width = dimensions.width;
					$set.height = dimensions.height;
				}
			}

			if (!doc.fileSize) {
				$set.fileSize = buffer.byteLength;
			}

			if (!doc.sha256) {
				$set.sha256 = crypto.createHash('sha256').update(buffer).digest('hex');
			}

			if (Object.keys($set).length > 0) {
				await imagesCollection.updateOne({_id: doc._id}, {$set});
				updated++;
			}

			if (processed % 100 === 0) {
				console.log(`Progress: ${updated} updated, ${skipped} skipped, ${errors} errors (${processed} total)`);
			}
		} catch (error) {
			console.warn(`[${processed}] Error processing ${localPath}:`, error);
			errors++;
		}
	}

	console.log(`Done: ${updated} updated, ${skipped} skipped (file not found), ${errors} errors, ${processed} total`);
	await client.close();
};

main().catch((error) => {
	console.error('Fatal error:', error);
	process.exit(1);
});
