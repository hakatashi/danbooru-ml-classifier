import axios from 'axios';
import axiosRetry from 'axios-retry';

axiosRetry(axios, {
	retries: 3,
	retryDelay() {
		return 5000;
	},
	retryCondition(error) {
		return error.response?.status === 429;
	},
});

export default axios;
