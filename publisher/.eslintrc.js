module.exports = {
	root: true,
	extends: [
		'@hakatashi/eslint-config/typescript',
	],
	parserOptions: {
		sourceType: 'module',
	},
	ignorePatterns: [
		'/lib/**/*',
	],
};
