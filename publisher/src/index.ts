// eslint-disable-next-line import/no-namespace
import * as firebase from 'firebase-admin';
import {getFunctions} from 'firebase-admin/functions';
import {info} from 'firebase-functions/logger';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';
import {GoogleAuth} from 'google-auth-library';

const app = firebase.initializeApp();
let auth: GoogleAuth | null = null;

const getFunctionUrl = async (name, location = 'us-central1') => {
	if (!auth) {
		auth = new GoogleAuth({
			scopes: 'https://www.googleapis.com/auth/cloud-platform',
		});
	}
	const projectId = await auth.getProjectId();
	const url = `https://cloudfunctions.googleapis.com/v2beta/projects/${projectId}/locations/${location}/functions/${name}`;

	const client = await auth.getClient();
	const res = await client.request({url});
	const uri = (res.data as any)?.serviceConfig?.uri;
	if (!uri) {
		throw new Error(`Unable to retreive uri for function at ${url}`);
	}

	info(`Retrieved uri for function at ${url}: ${uri}`);

	return uri;
};

export const onArtworkCreated = onDocumentCreated('artworks/{artworkId}', async (event) => {
	info('Artwork created', {structuredData: true});
	// @ts-expect-error: hoge
	info(await app.INTERNAL.getToken());
	const queue = getFunctions().taskQueue('executeTestFunction');
	info(queue);
	const result = await queue.enqueue({
		artworkId: event.params.artworkId,
	}, {
		scheduleDelaySeconds: 0,
		dispatchDeadlineSeconds: 60 * 5,
		uri: await getFunctionUrl('executeTestFunction'),
	});
	info(result);
});
