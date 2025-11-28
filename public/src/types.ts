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

export interface ImageDocument {
	id: string;
	key: string;
	status: string;
	type: string;
	postId: string;
	captions?: Record<string, CaptionData>;
	moderations?: Record<string, ModerationData>;
	favorites?: FavoritesData;
}
