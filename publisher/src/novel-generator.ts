import {readFileSync} from 'fs';
import {join} from 'path';
import axios from 'axios';
import {render} from 'ejs';
import {getFirestore, FieldValue} from 'firebase-admin/firestore';
import {info, error} from 'firebase-functions/logger';
import {defineSecret} from 'firebase-functions/params';
import {onCall, HttpsError} from 'firebase-functions/v2/https';

const grokApiKey = defineSecret('GROK_API_KEY');

interface GrokMessage {
	role: 'system' | 'user' | 'assistant';
	content: string | Array<{type: string; image_url?: {url: string}; text?: string}>;
}

interface GrokResponse {
	choices: Array<{
		message: {
			content: string;
		};
	}>;
	usage: {
		prompt_tokens: number;
		completion_tokens: number;
		total_tokens: number;
		reasoning_tokens?: number;
	};
}

interface Scene {
	sceneNumber: number;
	content: string;
}

interface NovelGenerationData {
	imageId: string;
	mode: 'caption' | 'image';
	captionProvider?: 'joycaption' | 'minicpm';
	model?: 'grok-4-1-fast-non-reasoning' | 'grok-4-1-fast-reasoning';
}

interface TokenUsage {
	promptTokens: number;
	completionTokens: number;
	totalTokens: number;
	reasoningTokens?: number;
}

interface NovelDocument {
	imageId: string;
	mode: 'caption' | 'image';
	captionProvider: string;
	model: string;
	outline: string;
	plot: string;
	scenes: Scene[];
	createdAt: ReturnType<typeof FieldValue.serverTimestamp>;
	status: 'generating' | 'completed' | 'failed';
	error?: string;
	tokenUsage: TokenUsage;
	estimatedCost: {
		inputCost: number;
		outputCost: number;
		totalCost: number;
	};
}

// Load prompt templates
// __dirname points to lib/ after build, so we need to go up to root and into src/prompts
const promptsDir = join(__dirname, '..', 'src', 'prompts');
const outlineFromCaptionTemplate = readFileSync(
	join(promptsDir, 'step1_outline_generation_from_caption.ejs'),
	'utf-8',
);
const outlineFromImageTemplate = readFileSync(
	join(promptsDir, 'step1_outline_generation_from_image.ejs'),
	'utf-8',
);
const plotTemplate = readFileSync(
	join(promptsDir, 'step2_plot_generation.ejs'),
	'utf-8',
);
const firstSceneTemplate = readFileSync(
	join(promptsDir, 'step3_first_chapter_generation.ejs'),
	'utf-8',
);
const remainingSceneTemplate = readFileSync(
	join(promptsDir, 'step4_remaining_chapter_generation.ejs'),
	'utf-8',
);

/**
 * Call Grok API with conversation history
 * @param {GrokMessage[]} messages - Conversation history
 * @param {string} apiKey - Grok API key
 * @param {string} model - Model to use
 * @return {Promise<Object>} Generated text and token usage
 */
const callGrok = async (
	messages: GrokMessage[],
	apiKey: string,
	model = 'grok-4-1-fast-non-reasoning',
): Promise<{content: string; usage: TokenUsage}> => {
	try {
		const response = await axios.post<GrokResponse>(
			'https://api.x.ai/v1/chat/completions',
			{
				model,
				messages,
				temperature: 0.7,
			},
			{
				headers: {
					Authorization: `Bearer ${apiKey}`,
					'Content-Type': 'application/json',
				},
			},
		);

		const content = response.data.choices[0]?.message?.content;
		if (!content) {
			throw new Error('No content in Grok API response');
		}

		const usage: TokenUsage = {
			promptTokens: response.data.usage.prompt_tokens,
			completionTokens: response.data.usage.completion_tokens,
			totalTokens: response.data.usage.total_tokens,
		};

		// Add reasoning tokens if present (for reasoning models)
		if (response.data.usage.reasoning_tokens !== undefined) {
			usage.reasoningTokens = response.data.usage.reasoning_tokens;
		}

		return {content, usage};
	} catch (err) {
		error('Grok API error:', err);
		throw new HttpsError('internal', 'Failed to call Grok API');
	}
};

/**
 * Get image URL from storage key
 * @param {string} storageKey - Storage key for the image
 * @return {string} Image URL
 */
const getImageUrl = (storageKey: string): string => {
	const filename = storageKey.split('/').pop() || storageKey;
	return `https://matrix-images.hakatashi.com/hakataarchive/twitter/${filename}`;
};

/**
 * Add token usage to total
 * @param {TokenUsage} total - Total token usage
 * @param {TokenUsage} usage - Usage to add
 * @return {void}
 */
const addTokenUsage = (total: TokenUsage, usage: TokenUsage): void => {
	total.promptTokens += usage.promptTokens;
	total.completionTokens += usage.completionTokens;
	total.totalTokens += usage.totalTokens;
	if (usage.reasoningTokens !== undefined) {
		total.reasoningTokens = (total.reasoningTokens || 0) + usage.reasoningTokens;
	}
};

interface CallGrokOptions {
	conversationHistories: GrokMessage[];
	userMessage: string | Array<{type: string; image_url?: {url: string}; text?: string}>;
	apiKey: string;
	model: string;
	totalUsage: TokenUsage;
}

/**
 * Call Grok API and update conversation history and token usage
 * @param {CallGrokOptions} options - Options
 * @return {Promise<string>} Assistant's response content
 */
const callGrokAndUpdate = async (options: CallGrokOptions): Promise<string> => {
	const {conversationHistories, userMessage, apiKey, model, totalUsage} = options;
	conversationHistories.push({
		role: 'user',
		content: userMessage,
	});
	const result = await callGrok(conversationHistories, apiKey, model);
	addTokenUsage(totalUsage, result.usage);
	conversationHistories.push({
		role: 'assistant',
		content: result.content,
	});
	return result.content;
};

/**
 * Clean scene content by removing lines starting with '#',
 * collapsing multiple empty lines, and trimming whitespace
 * @param {string} content - Raw scene content
 * @return {string} Cleaned scene content
 */
const cleanSceneContent = (content: string): string => {
	// Split into lines
	const lines = content.split('\n');

	// Remove lines starting with '#'
	const filteredLines = lines.filter((line) => !line.trimStart().startsWith('#'));

	// Join back and collapse multiple empty lines to single empty line
	let cleaned = filteredLines.join('\n');
	cleaned = cleaned.replace(/\n\n+/g, '\n\n');

	// Trim leading and trailing whitespace
	cleaned = cleaned.trim();

	return cleaned;
};

/**
 * Generate novel from image
 * @param {NovelGenerationData} data - Generation data
 * @param {string} apiKey - Grok API key
 * @return {Promise<NovelDocument>} Generated novel document
 */
const generateNovel = async (
	data: NovelGenerationData,
	apiKey: string,
): Promise<NovelDocument> => {
	const db = getFirestore();
	const {imageId, mode, captionProvider, model = 'grok-4-1-fast-non-reasoning'} = data;

	// Fetch image document
	const imageDoc = await db.collection('images').doc(imageId).get();
	if (!imageDoc.exists) {
		throw new HttpsError('not-found', 'Image not found');
	}

	const imageData = imageDoc.data();
	if (!imageData) {
		throw new HttpsError('not-found', 'Image data is empty');
	}

	// Initialize conversation history and token usage tracking
	const conversationHistories: GrokMessage[] = [];
	const totalUsage: TokenUsage = {
		promptTokens: 0,
		completionTokens: 0,
		totalTokens: 0,
		reasoningTokens: 0,
	};

	// Step 1: Generate outline
	info(`Generating outline for image ${imageId} using mode: ${mode}`);

	let outline = '';
	if (mode === 'caption') {
		// Check if caption exists
		const provider = captionProvider || 'minicpm';
		const caption = imageData.captions?.[provider]?.caption;
		if (!caption) {
			throw new HttpsError(
				'failed-precondition',
				`Caption from ${provider} not found`,
			);
		}

		const prompt = render(outlineFromCaptionTemplate, {caption});
		outline = await callGrokAndUpdate({conversationHistories, userMessage: prompt, apiKey, model, totalUsage});
	} else {
		// Get image URL
		const imageUrl = getImageUrl(imageData.key);
		const imageMessages = [
			{
				type: 'image_url',
				image_url: {
					url: imageUrl,
				},
			},
			{
				type: 'text',
				text: outlineFromImageTemplate,
			},
		];
		outline = await callGrokAndUpdate({conversationHistories, userMessage: imageMessages, apiKey, model, totalUsage});
	}

	info(`Generated outline: ${outline.substring(0, 100)}...`);

	// Step 2: Generate plot
	info('Generating plot');
	const plot = await callGrokAndUpdate({conversationHistories, userMessage: plotTemplate, apiKey, model, totalUsage});
	info(`Generated plot: ${plot.substring(0, 100)}...`);

	// Step 3: Generate first scene
	info('Generating first scene');
	const firstSceneContent = await callGrokAndUpdate({conversationHistories, userMessage: firstSceneTemplate, apiKey, model, totalUsage});
	const scenes: Scene[] = [
		{
			sceneNumber: 1,
			content: cleanSceneContent(firstSceneContent),
		},
	];

	// Step 4: Generate remaining scenes (2-5)
	for (let sceneNumber = 2; sceneNumber <= 5; sceneNumber++) {
		info(`Generating scene ${sceneNumber}`);
		const nextSceneNumber = sceneNumber < 5 ? sceneNumber + 1 : null;
		const remainingScenePrompt = render(remainingSceneTemplate, {
			scene_number: sceneNumber,
			next_scene_number: nextSceneNumber,
		});
		const sceneContent = await callGrokAndUpdate({conversationHistories, userMessage: remainingScenePrompt, apiKey, model, totalUsage});
		scenes.push({
			sceneNumber,
			content: cleanSceneContent(sceneContent),
		});
	}

	// Calculate API costs
	// Both grok-4-1-fast-non-reasoning and grok-4-1-fast-reasoning use the same pricing:
	// $0.20/1M input tokens, $0.50/1M output tokens (reasoning tokens charged same as output)
	const inputRate = 0.20;
	const outputRate = 0.50;

	const inputCost = (totalUsage.promptTokens / 1_000_000) * inputRate;
	const outputCost = (totalUsage.completionTokens / 1_000_000) * outputRate;
	// Reasoning tokens are charged at the same rate as output tokens
	const reasoningCost = totalUsage.reasoningTokens
		? (totalUsage.reasoningTokens / 1_000_000) * outputRate
		: 0;
	const totalCost = inputCost + outputCost + reasoningCost;

	const reasoningInfo = totalUsage.reasoningTokens ? `, Reasoning: ${totalUsage.reasoningTokens}` : '';
	info(
		`Token usage - Prompt: ${totalUsage.promptTokens}, Completion: ${totalUsage.completionTokens}${reasoningInfo}, Total: ${totalUsage.totalTokens}`,
	);
	const costBreakdown = reasoningCost > 0
		? `Input: $${inputCost.toFixed(4)}, Output: $${outputCost.toFixed(4)}, Reasoning: $${reasoningCost.toFixed(4)}, Total: $${totalCost.toFixed(4)}`
		: `Input: $${inputCost.toFixed(4)}, Output: $${outputCost.toFixed(4)}, Total: $${totalCost.toFixed(4)}`;
	info(`Estimated cost - ${costBreakdown}`);

	// Create novel document
	const novelDoc: NovelDocument = {
		imageId,
		mode,
		captionProvider: mode === 'caption' && captionProvider ? captionProvider : '',
		model,
		outline,
		plot,
		scenes,
		createdAt: FieldValue.serverTimestamp(),
		status: 'completed',
		tokenUsage: totalUsage,
		estimatedCost: {
			inputCost,
			outputCost,
			totalCost,
		},
	};

	return novelDoc;
};

/**
 * Firebase callable function to generate novel
 */
export const generateNovelFromImage = onCall(
	{
		secrets: [grokApiKey],
		timeoutSeconds: 540, // 9 minutes
		memory: '512MiB',
	},
	async (request) => {
		// Authentication check
		if (!request.auth) {
			throw new HttpsError('unauthenticated', 'User must be authenticated');
		}

		const userEmail = request.auth.token.email;
		if (userEmail !== 'hakatasiloving@gmail.com') {
			throw new HttpsError(
				'permission-denied',
				'Only authorized user can generate novels',
			);
		}

		const data = request.data as NovelGenerationData;
		const {imageId, mode, captionProvider, model} = data;

		// Validate input
		if (!imageId || typeof imageId !== 'string') {
			throw new HttpsError('invalid-argument', 'imageId is required');
		}

		if (!mode || !['caption', 'image'].includes(mode)) {
			throw new HttpsError(
				'invalid-argument',
				'mode must be either "caption" or "image"',
			);
		}

		if (mode === 'caption') {
			if (
				!captionProvider ||
				!['joycaption', 'minicpm'].includes(captionProvider)
			) {
				throw new HttpsError(
					'invalid-argument',
					'captionProvider must be either "joycaption" or "minicpm" when mode is "caption"',
				);
			}
		}

		if (model && !['grok-4-1-fast-non-reasoning', 'grok-4-1-fast-reasoning'].includes(model)) {
			throw new HttpsError(
				'invalid-argument',
				'model must be either "grok-4-1-fast-non-reasoning" or "grok-4-1-fast-reasoning"',
			);
		}

		const db = getFirestore();

		try {
			// Create a placeholder document
			const novelRef = db.collection('generated_novels').doc();
			await novelRef.set({
				imageId,
				mode,
				captionProvider: mode === 'caption' && captionProvider ? captionProvider : '',
				model: model || 'grok-4-1-fast-non-reasoning',
				status: 'generating',
				createdAt: FieldValue.serverTimestamp(),
			});

			info(`Started novel generation: ${novelRef.id}`);

			// Generate the novel
			const novelDoc = await generateNovel(data, grokApiKey.value());

			// Update the document
			await novelRef.set(novelDoc);

			// Add novel reference to image document
			await db
				.collection('images')
				.doc(imageId)
				.update({
					generatedNovels: FieldValue.arrayUnion(novelRef.id),
				});

			info(`Completed novel generation: ${novelRef.id}`);

			return {
				success: true,
				novelId: novelRef.id,
			};
		} catch (err) {
			error('Novel generation error:', err);

			// If we have a novelRef, mark it as failed
			if (err instanceof Error) {
				throw new HttpsError('internal', `Novel generation failed: ${err.message}`);
			}

			throw new HttpsError('internal', 'Novel generation failed');
		}
	},
);
