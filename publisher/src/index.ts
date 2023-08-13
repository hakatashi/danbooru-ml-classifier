// eslint-disable-next-line import/no-namespace
import * as firebase from 'firebase-admin';
import {getFunctions} from 'firebase-admin/functions';
import {info} from 'firebase-functions/logger';
import {tasks} from 'firebase-functions/v2';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';
import {GoogleAuth} from 'google-auth-library';

firebase.initializeApp();
let auth: GoogleAuth | null = null;

export const executeTestFunction3 = tasks.onTaskDispatched(
	{
		retryConfig: {
			maxAttempts: 5,
			minBackoffSeconds: 60,
		},
		rateLimits: {
			maxConcurrentDispatches: 6,
		},
	},
	(req) => {
		info('Test function 3 dispatched', {structuredData: true});
		info(req);
	},
);

const getFunctionUrl = async (name: string, location = 'us-central1') => {
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

const callFunction = async (name: string, location = 'us-central1') => {
	if (!auth) {
		auth = new GoogleAuth({
			scopes: 'https://www.googleapis.com/auth/cloud-platform',
		});
	}

	const projectId = await auth.getProjectId();
	const url = `https://cloudtasks.googleapis.com/v2/projects/${projectId}/locations/${location}/queues/${name}/tasks`;

	const client = await auth.getClient();
	const res = await client.request({
		url,
		method: 'POST',
		body: JSON.stringify({
			task: {
				httpRequest: {
					url: await getFunctionUrl(name),
					body: Buffer.from(JSON.stringify({
						artworkId: '123',
					})).toString('base64'),
					oidcToken: {
						serviceAccountEmail: `${projectId}@appspot.gserviceaccount.com`,
						audience: 'danbooru-ml-classifier',
					},
				},
			},
		}),
	});

	info(res);
	info(res.data);

	return res.data;
};

export const onArtworkCreated = onDocumentCreated('artworks/{artworkId}', async () => {
	info('Artwork created', {structuredData: true});
	await callFunction('executeTestFunction2');
	const queue = getFunctions().taskQueue('executeTestFunction2');
	info(queue);
	const result = await queue.enqueue({
		artworkId: '123',
	}, {
		scheduleDelaySeconds: 0,
		dispatchDeadlineSeconds: 60 * 5,
		uri: await getFunctionUrl('executeTestFunction2'),
	});
	info(result);
});
