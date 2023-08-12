module.exports = {
	root: true,
	extends: [
		'@hakatashi/eslint-config/typescript',
	],
	parserOptions: {
		project: ['tsconfig.json', 'tsconfig.dev.json'],
		sourceType: 'module',
	},
	ignorePatterns: [
		'/lib/**/*',
	],
};
