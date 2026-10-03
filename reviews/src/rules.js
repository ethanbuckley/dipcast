// The limits a review is held to, in a module of their own: the Workers runtime treats every named
// export of the main module (index.js) as an entry point, and refuses to start when one is a number,
// an object or an array rather than a function ("Incorrect type for map entry").

export const MAX_PHOTOS = 3, MAX_TEXT = 1500, MAX_NAME = 40;
// Requests a day from one connection. The page sends one request a review, so a swimmer never meets
// these; a script that floods the queue does.
export const LIMITS = { review: 10, report: 20, remove: 30 };
export const REASONS = ['not-about-spot', 'rude', 'person', 'spam', 'other'];
