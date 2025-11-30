export interface CaptionData {
	metadata: {
		model: string;
		backend: string;
		repository: string;
		language_file: string | null;
		vision_file: string | null;
		prompt: string;
		createdAt?: {
			seconds: number;
			nanoseconds: number;
		};
	};
	caption: string;
}

export interface ModerationData {
	metadata: {
		model: string;
		backend: string;
		repository: string;
		language_file: string | null;
		vision_file: string | null;
		prompt: string;
		createdAt?: {
			seconds: number;
			nanoseconds: number;
		};
	};
	raw_result: string;
	result: number | null;
	explanation?: string;
}

export interface FavoritesData {
	isFavorited: boolean;
	categories: string[];
}

export interface AgeEstimationCharacter {
	id: number;
	estimated_age_range: string;
	most_likely_age: number | null;
	confidence: number;
	gender_guess: 'male' | 'female' | 'unknown';
	notes: string;
}

export interface AgeEstimationResult {
	characters_detected: number;
	characters: AgeEstimationCharacter[];
}

export interface AgeEstimationData {
	metadata: {
		model: string;
		backend: string;
		language_repository: string;
		vision_repository: string;
		language_file: string | null;
		vision_file: string | null;
		prompt: string;
		createdAt?: {
			seconds: number;
			nanoseconds: number;
		};
	};
	raw_result: string;
	result: AgeEstimationResult;
	main_character_age: number | null;
}

export interface TwitterUser {
	screen_name?: string;
	name?: string;
	id_str?: string;
}

export interface TwitterRetweetedStatus {
	id_str?: string;
	user?: TwitterUser;
}

export interface TwitterSourceData {
	tweetId?: string;
	text?: string;
	createdAt?: string;
	mediaUrl?: string;
	user?: TwitterUser;
	retweetedStatus?: TwitterRetweetedStatus;
	isQuoteStatus?: boolean;
}

export interface ImageDocument {
	id: string;
	key: string;
	status: string;
	type: string;
	postId: string;
	captions?: Record<string, CaptionData>;
	moderations?: Record<string, ModerationData>;
	ageEstimations?: Record<string, AgeEstimationData>;
	favorites?: FavoritesData;
	source?: TwitterSourceData;
}
