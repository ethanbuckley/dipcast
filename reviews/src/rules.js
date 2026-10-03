// The limits a review is held to, in a module of their own: the Workers runtime treats every named
// export of the main module (index.js) as an entry point, and refuses to start when one is a number,
// an object or an array rather than a function ("Incorrect type for map entry").

export const MAX_PHOTOS = 3, MAX_TEXT = 1500, MAX_NAME = 40;
// Requests a day from one connection. The page sends one request a review, so a swimmer never meets
// these; a script that floods the queue does.
export const LIMITS = { review: 10, report: 20, remove: 30, status: 60 };
// For everyone together. KV takes 1,000 writes a day on the free plan, two a photo, and holds 1 GB:
// at most 300 photos a day and 300 reviews waiting for the operator. Published photos accumulate;
// the operator must monitor storage and move to R2 before the store fills.
export const PHOTOS_PER_DAY = 300, MAX_PENDING = 300;
// Published reviews the moderation page lists, newest first; older ones by id (README).
export const PUBLISHED_LISTED = 500;
export const REASONS = ['not-about-spot', 'rude', 'person', 'spam', 'other'];
