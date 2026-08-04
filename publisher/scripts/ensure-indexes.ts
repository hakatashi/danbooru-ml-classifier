/**
 * Ensure required MongoDB indexes exist across collections.
 *
 * Idempotent: createIndex() is a no-op when an index with the same spec
 * already exists under the same name.
 *
 * Usage:
 *   npx ts-node --project tsconfig.json scripts/ensure-indexes.ts [--dry-run]
 *
 * Optional env vars:
 *   MONGODB_URI - MongoDB URI (default: mongodb://localhost:27017)
 *   MONGODB_DB  - Database name (default: danbooru-ml-classifier)
 */

import 'dotenv/config';
import type {Db} from 'mongodb';
import {getDb, closeDb} from '../src/db';

interface IndexSpec {
	name: string;
	collection: string;
	keys: Record<string, 1 | -1>;
	options: Record<string, unknown>;
}

const REQUIRED_INDEXES: IndexSpec[] = [
	{
		name: 'favorites_favoritedAt_desc',
		collection: 'images',
		keys: {'favorites.favoritedAt': -1},
		options: {
			partialFilterExpression: {'favorites.isFavorited': true},
		},
	},
	{
		name: 'pageViews_date_sortField_page_unique',
		collection: 'pageViews',
		keys: {date: 1, sortField: 1, page: 1},
		options: {unique: true},
	},
	{
		name: 'pageViews_sortField_date_desc',
		collection: 'pageViews',
		keys: {sortField: 1, date: -1},
		options: {},
	},
];

/**
 * Idempotently create every required index, grouped by collection. Exported
 * so other scripts (e.g. the favorites migration) can call it without
 * shelling out.
 */
export const ensureIndexes = async (db: Db, dryRun = false): Promise<void> => {
	const collectionNames = [...new Set(REQUIRED_INDEXES.map((spec) => spec.collection))];

	for (const collectionName of collectionNames) {
		const collection = db.collection(collectionName);
		const specs = REQUIRED_INDEXES.filter((spec) => spec.collection === collectionName);

		console.log(`\nCurrent indexes on \`${collectionName}\`:`);
		const before = await collection.indexes();
		for (const index of before) {
			console.log(`  - ${index.name}`);
		}

		for (const spec of specs) {
			const exists = before.some((index) => index.name === spec.name);
			if (exists) {
				console.log(`\n[${collectionName}.${spec.name}] Already exists. Skipping.`);
				continue;
			}

			if (dryRun) {
				console.log(`\n[${collectionName}.${spec.name}] Would create index: ${JSON.stringify(spec.keys)} ${JSON.stringify(spec.options)}`);
				continue;
			}

			console.log(`\n[${collectionName}.${spec.name}] Creating index...`);
			await collection.createIndex(spec.keys, {name: spec.name, ...spec.options});
			console.log(`[${collectionName}.${spec.name}] Created.`);
		}

		if (!dryRun) {
			console.log(`\nIndexes on \`${collectionName}\` after:`);
			const after = await collection.indexes();
			for (const index of after) {
				console.log(`  - ${index.name}`);
			}
		}
	}
};

const main = async (): Promise<void> => {
	const db = await getDb();
	await ensureIndexes(db, process.argv.includes('--dry-run'));
	await closeDb();
};

if (require.main === module) {
	main().catch((error) => {
		console.error('Fatal error:', error);
		process.exitCode = 1;
	});
}
