import {MongoClient} from 'mongodb';
import type {Db} from 'mongodb';
import {MONGODB_DB, MONGODB_URI} from './config';

let client: MongoClient | null = null;
let db: Db | null = null;

export const getDb = async (): Promise<Db> => {
	if (db !== null) {
		return db;
	}
	// eslint-disable-next-line require-atomic-updates
	client = new MongoClient(MONGODB_URI);
	await client.connect();
	// eslint-disable-next-line require-atomic-updates
	db = client.db(MONGODB_DB);
	return db;
};

export const closeDb = async (): Promise<void> => {
	if (client !== null) {
		await client.close();
		// eslint-disable-next-line require-atomic-updates
		client = null;
		db = null;
	}
};
