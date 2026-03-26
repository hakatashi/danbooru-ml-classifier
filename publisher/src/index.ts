import firebase from 'firebase-admin';

firebase.initializeApp();

export * from './api';
export * from './moderation-stats';
export * from './novel-generator';
