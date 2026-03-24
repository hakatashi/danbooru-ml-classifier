import {describe, expect, it} from 'vitest';
import {escapeFirestoreKey} from '../utils';

describe('escapeFirestoreKey', () => {
	it('escapes forward slashes', () => {
		expect(escapeFirestoreKey('danbooru/image.jpg')).toBe('danbooru%2Fimage%2Ejpg');
	});

	it('escapes percent signs', () => {
		expect(escapeFirestoreKey('file%20name')).toBe('file%2520name');
	});

	it('escapes dots', () => {
		expect(escapeFirestoreKey('image.png')).toBe('image%2Epng');
	});

	it('leaves plain strings unchanged', () => {
		expect(escapeFirestoreKey('plainfilename')).toBe('plainfilename');
	});

	it('escapes percent before slash to avoid double-decoding issues', () => {
		expect(escapeFirestoreKey('%2F')).toBe('%252F');
	});
});
