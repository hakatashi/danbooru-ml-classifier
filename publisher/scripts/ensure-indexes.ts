/**
 * Ensure required MongoDB indexes exist on the `images` collection.
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
	keys: Record<string, 1 | -1>;
	options: Record<string, unknown>;
}

const REQUIRED_INDEXES: IndexSpec[] = [
	{
		name: 'favorites_favoritedAt_desc',
		keys: {'favorites.favoritedAt': -1},
		options: {
			partialFilterExpression: {'favorites.isFavorited': true},
		},
	},
];

/**
 * Idempotently create every required index on `images`. Exported so other
 * scripts (e.g. the favorites migration) can call it without shelling out.
 */
export const ensureIndexes = async (db: Db, dryRun = false): Promise<void> => {
	const imagesCollection = db.collection('images');

	console.log('Current indexes on `images`:');
	const before = await imagesCollection.indexes();
	for (const index of before) {
		console.log(`  - ${index.name}`);
	}

	for (const spec of REQUIRED_INDEXES) {
		const exists = before.some((index) => index.name === spec.name);
		if (exists) {
			console.log(`\n[${spec.name}] Already exists. Skipping.`);
			continue;
		}

		if (dryRun) {
			console.log(`\n[${spec.name}] Would create index: ${JSON.stringify(spec.keys)} ${JSON.stringify(spec.options)}`);
			continue;
		}

		console.log(`\n[${spec.name}] Creating index...`);
		await imagesCollection.createIndex(spec.keys, {name: spec.name, ...spec.options});
		console.log(`[${spec.name}] Created.`);
	}

	if (!dryRun) {
		console.log('\nIndexes after:');
		const after = await imagesCollection.indexes();
		for (const index of after) {
			console.log(`  - ${index.name}`);
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
