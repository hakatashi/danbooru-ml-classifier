import firebase from 'firebase-admin';

/**
 * Initializes moderation statistics collection from existing images data.
 * This script should be run once to populate the moderationStats collection.
 * @returns {Promise<void>} Promise that resolves when initialization is complete
 */
const initializeModerationStats = async () => {
	// Initialize Firebase Admin
	firebase.initializeApp({
		projectId: 'danbooru-ml-classifier',
	});
	const db = firebase.firestore();

	console.log('Starting moderation statistics initialization...');

	// Track statistics per provider
	const stats: Record<string, {count: number; sum: number}> = {};

	// Query all images with moderations
	const imagesRef = db.collection('images');
	const snapshot = await imagesRef.get();

	console.log(`Processing ${snapshot.size} images...`);

	let processedCount = 0;
	for (const doc of snapshot.docs) {
		const data = doc.data();
		const moderations = data.moderations || {};

		// Process each provider's moderation data
		for (const [provider, moderation] of Object.entries(moderations)) {
			const moderationData = moderation as {result?: number};
			const result = moderationData.result;

			// Only count if result is a valid number
			if (result !== null && result !== undefined && typeof result === 'number') {
				if (!stats[provider]) {
					stats[provider] = {count: 0, sum: 0};
				}
				stats[provider].count++;
				stats[provider].sum += result;
			}
		}

		processedCount++;
		if (processedCount % 100 === 0) {
			console.log(`Processed ${processedCount} images...`);
		}
	}

	console.log('\nCalculated statistics:');
	for (const [provider, stat] of Object.entries(stats)) {
		const average = stat.count > 0 ? (stat.sum / stat.count).toFixed(2) : 'N/A';
		console.log(`  ${provider}: count=${stat.count}, sum=${stat.sum.toFixed(2)}, avg=${average}`);
	}

	// Write statistics to Firestore
	console.log('\nWriting statistics to Firestore...');
	const batch = db.batch();
	const statsRef = db.collection('moderationStats');

	for (const [provider, stat] of Object.entries(stats)) {
		const docRef = statsRef.doc(provider);
		batch.set(docRef, {
			count: stat.count,
			sum: stat.sum,
			updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
			initializedAt: firebase.firestore.FieldValue.serverTimestamp(),
		});
	}

	await batch.commit();
	console.log('Successfully wrote statistics to moderationStats collection!');
};

// Run the initialization
initializeModerationStats().catch((error) => {
	console.error('Error initializing moderation statistics:', error);
	throw error;
});
