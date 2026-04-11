/**
 * Find duplicate images (same sha256) in the DB, keep the oldest entry as
 * canonical, and for each duplicate:
 *   - Set status to 'deduped' and localPath to null
 *   - Delete the local file (unless --dry-run)
 *
 * Usage:
 *   npx ts-node --project tsconfig.json scripts/dedupe-images.ts [--dry-run]
 *
 * Options:
 *   --dry-run   Print what would be done without making any changes
 *
 * Optional env vars:
 *   MONGODB_URI - MongoDB URI (default: mongodb://localhost:27017)
 *   MONGODB_DB  - Database name (default: danbooru-ml-classifier)
 */

import 'dotenv/config';
import fs from 'fs';
import type {ObjectId} from 'mongodb';
import {MongoClient} from 'mongodb';

const MONGODB_URI = process.env.MONGODB_URI ?? 'mongodb://localhost:27017';
const MONGODB_DB = process.env.MONGODB_DB ?? 'danbooru-ml-classifier';

const isDryRun = process.argv.includes('--dry-run');

const main = async (): Promise<void> => {
	if (isDryRun) {
		console.log('[dry-run] No changes will be made');
	}

	const client = new MongoClient(MONGODB_URI);
	await client.connect();
	const db = client.db(MONGODB_DB);
	const imagesCollection = db.collection('images');

	// Aggregate duplicate sha256 groups (only among docs that have a sha256)
	const pipeline = [
		{$match: {sha256: {$exists: true, $ne: null}}},
		{$group: {_id: '$sha256', count: {$sum: 1}, docs: {$push: {id: '$_id', key: '$key', localPath: '$localPath', downloadedAt: '$downloadedAt', status: '$status'}}}},
		{$match: {count: {$gt: 1}}},
	];

	const cursor = imagesCollection.aggregate<{
		_id: string;
		count: number;
		docs: {id: ObjectId; key: string; localPath: string | null | undefined; downloadedAt: Date | null | undefined; status: string}[];
	}>(pipeline);

	let groupCount = 0;
	let dedupeCount = 0;
	let fileDeleteCount = 0;
	let alreadyDedupedCount = 0;

	for await (const group of cursor) {
		groupCount++;
		const sha256 = group._id;

		// Sort docs oldest-first by downloadedAt; use key as tiebreaker for stability
		const sorted = group.docs.slice().sort((a, b) => {
			const aTime = a.downloadedAt ? new Date(a.downloadedAt).getTime() : 0;
			const bTime = b.downloadedAt ? new Date(b.downloadedAt).getTime() : 0;
			if (aTime !== bTime) return aTime - bTime;
			return String(a.key).localeCompare(String(b.key));
		});

		const canonical = sorted[0];
		const duplicates = sorted.slice(1);

		console.log(`\nsha256: ${sha256} (${group.count} entries)`);
		console.log(`  canonical: ${canonical.key} (status=${canonical.status})`);

		for (const dup of duplicates) {
			if (dup.status === 'deduped') {
				console.log(`  skip (already deduped): ${dup.key}`);
				alreadyDedupedCount++;
				continue;
			}

			const localPath = dup.localPath;
			const hasFile = typeof localPath === 'string' && localPath.length > 0 && fs.existsSync(localPath);

			console.log(`  dedupe: ${dup.key}${hasFile ? ` (file: ${localPath})` : ' (no local file)'}`);

			if (!isDryRun) {
				await imagesCollection.updateOne(
					{_id: dup.id},
					{$set: {status: 'deduped', localPath: null}},
				);

				if (hasFile) {
					await fs.promises.unlink(localPath!);
					fileDeleteCount++;
					console.log(`    deleted: ${localPath}`);
				}
			}

			dedupeCount++;
		}
	}

	console.log(`\n${'='.repeat(60)}`);
	if (isDryRun) {
		console.log('[dry-run] Summary (no changes made):');
	} else {
		console.log('Summary:');
	}
	console.log(`  Duplicate groups found : ${groupCount}`);
	console.log(`  Entries to dedupe      : ${dedupeCount}`);
	console.log(`  Local files deleted    : ${fileDeleteCount}`);
	console.log(`  Already deduped        : ${alreadyDedupedCount}`);

	await client.close();
};

main().catch((error) => {
	console.error('Fatal error:', error);
	process.exit(1);
});
